from __future__ import annotations

import json
import queue
import socket
import time
from pathlib import Path

import zenoh

from limx_tron2_sim.bridge import BridgeSettings, DurableReplayStore, LimXTron2Execution, LimXTron2ZenohBridge
from limx_tron2_sim.runtime import run_mujoco_episode

from conftest import correlated_event


def _free_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp/127.0.0.1:{probe.getsockname()[1]}"


def _client_config(endpoint: str):
    return zenoh.Config.from_json5(json.dumps({"mode": "client", "connect": {"endpoints": [endpoint]}}))


def test_real_zenoh_bridge_runs_real_mujoco_once_then_rejects_replay(tmp_path: Path) -> None:
    endpoint = _free_endpoint()
    host = zenoh.open(zenoh.Config.from_json5(json.dumps({"mode": "peer", "listen": {"endpoints": [endpoint]}})))
    observer = zenoh.open(_client_config(endpoint))
    received: queue.Queue[dict] = queue.Queue()
    result_subscriber = observer.declare_subscriber(
        "robot/tunnel/result", lambda sample: received.put(json.loads(bytes(sample.payload.to_bytes())))
    )
    calls = 0

    def measured_runner(request):
        nonlocal calls
        calls += 1
        return run_mujoco_episode(request)

    bridge = LimXTron2ZenohBridge(
        BridgeSettings(endpoint, None, "robot/tunnel/action", "robot/tunnel/result", "robot/limx_tron2/metrics"),
        LimXTron2Execution(
            replay_store=DurableReplayStore(tmp_path / "replay.sqlite3"), episode_runner=measured_runner
        ),
    )
    sender = zenoh.open(_client_config(endpoint))
    publisher = sender.declare_publisher("robot/tunnel/action")
    try:
        time.sleep(0.5)
        event = correlated_event(action_id="zenoh-001", idempotency_key="zenoh-key-001", payment_nonce="zenoh-payment-001")
        publisher.put(event)
        first = received.get(timeout=10)
        assert first["status"] == "success"
        assert first["result"]["simulator"] == "mujoco"
        assert calls == 1

        publisher.put(event)
        replay = received.get(timeout=5)
        assert replay["status"] == "failure"
        assert replay["result"]["error_code"] == "REPLAY_DETECTED"
        assert calls == 1
    finally:
        publisher.undeclare()
        sender.close()
        bridge.close()
        result_subscriber.undeclare()
        observer.close()
        host.close()
