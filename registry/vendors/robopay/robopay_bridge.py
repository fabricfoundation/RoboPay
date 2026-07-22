import json
import sys
import time
from typing import Any, Dict, Optional, Set

try:
    import zenoh
except ImportError:
    print("[ERROR] Zenoh module not found. Run 'python -m pip install eclipse-zenoh'")
    sys.exit(1)

PROCESSED_ACTIONS: Set[str] = set()


def _decode_payload(sample: Any) -> str:
    payload = getattr(sample, "payload", None)
    if payload is None:
        return ""
    if hasattr(payload, "to_string"):
        return payload.to_string()
    if isinstance(payload, (bytes, bytearray)):
        return payload.decode("utf-8")
    return str(payload)


def _build_result(
    action_id: str,
    status: str,
    execution_time_ms: int,
    simulator_metrics: Optional[Dict[str, Any]] = None,
    settled: bool = True,
) -> Dict[str, Any]:
    return {
        "actionId": action_id,
        "status": status,
        "execution_time_ms": execution_time_ms,
        "simulator_metrics": simulator_metrics or {},
        "settled": settled,
    }


def main() -> None:
    print("==========================================")
    print("  RoboPay Zenoh Action Bridge v1.1  ")
    print("==========================================")

    conf = zenoh.Config()
    print("[Zenoh] Opening session...")
    session = zenoh.open(conf)

    request_topic = "robopay/action/request"
    result_topic = "robopay/action/result"

    publisher = session.declare_publisher(result_topic)
    print(f"[Zenoh] Subscribing to '{request_topic}' and publishing results to '{result_topic}'")

    def handle_request(sample: Any) -> None:
        start_time = time.perf_counter()
        try:
            raw_payload = _decode_payload(sample)
            request = json.loads(raw_payload) if raw_payload else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            response = _build_result(
                "unknown",
                "invalid_payload",
                0,
                {"reason": "request payload was not valid JSON"},
                settled=False,
            )
            publisher.put(json.dumps(response).encode("utf-8"))
            print(f"[TX] {json.dumps(response)}")
            return

        action_id = request.get("actionId") or request.get("action_id") or "unknown"
        skill_id = request.get("skill_id") or request.get("skillId") or ""
        payment_proof = request.get("payment_proof")

        if action_id in PROCESSED_ACTIONS:
            print(f"[Zenoh] Dropping replayed action request: {action_id}")
            return

        if not payment_proof:
            response = _build_result(
                action_id,
                "rejected",
                0,
                {
                    "skill_id": skill_id,
                    "payment_verified": False,
                    "reason": "payment_proof missing",
                },
                settled=False,
            )
            publisher.put(json.dumps(response).encode("utf-8"))
            print(f"[TX] {json.dumps(response)}")
            return

        PROCESSED_ACTIONS.add(action_id)
        time.sleep(0.05)
        execution_time_ms = int((time.perf_counter() - start_time) * 1000)
        response = _build_result(
            action_id,
            "completed",
            execution_time_ms,
            {
                "skill_id": skill_id,
                "payment_verified": True,
                "simulator": "robopay-tier1",
                "execution_mode": "simulated",
            },
            settled=True,
        )
        publisher.put(json.dumps(response).encode("utf-8"))
        print(f"[TX] {json.dumps(response)}")

    session.declare_subscriber(request_topic, handle_request)
    print(f"[Zenoh] Listening for action events on '{request_topic}'")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Zenoh] Closing session...")
        session.close()


if __name__ == "__main__":
    main()