from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


RUNNER_PATH = Path(__file__).resolve().parents[1] / "bridge" / "run_live_base_sepolia_e2e.py"
SPEC = importlib.util.spec_from_file_location("limx_tron2_live_base_sepolia", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class RunningTunnel:
    returncode = None

    def poll(self):
        return None


class Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_public_readiness_retries_404_until_robot_is_discoverable(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter((Response(404, "not registered"), Response(200, "ok")))
    calls: list[str] = []

    def get(url: str, *, timeout: int):
        calls.append(url)
        assert timeout == 10
        return next(responses)

    monkeypatch.setattr(RUNNER.requests, "get", get)
    monkeypatch.setattr(RUNNER.time, "sleep", lambda _: None)

    RUNNER._wait_for_public_tunnel(
        RunningTunnel(), "https://api.example/robots/tron2/skills", timeout_seconds=1
    )

    assert calls == ["https://api.example/robots/tron2/skills"] * 2


def test_public_readiness_fails_immediately_if_tunnel_exits() -> None:
    class ExitedTunnel:
        returncode = 17

        def poll(self):
            return self.returncode

    with pytest.raises(RuntimeError, match=r"exit=17"):
        RUNNER._wait_for_public_tunnel(
            ExitedTunnel(), "https://api.example/robots/tron2/skills", timeout_seconds=1
        )
