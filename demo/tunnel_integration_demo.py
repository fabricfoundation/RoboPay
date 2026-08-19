#!/usr/bin/env python3
"""End-to-end demo showing Fabric tunnel → Zenoh → MuJoCo bridge flow.

This demo shows the COMPLETE RoboPay integration:
1. Start tunnel (verifies x402 payment)
2. Start MuJoCo bridge (subscribes to Zenoh)
3. Send paid action through tunnel API
4. Bridge receives verified action, executes in simulation
5. Bridge publishes result back to Zenoh

Usage:
    # Terminal 1: Start tunnel
    cd tunnel && go run cmd/main.go

    # Terminal 2: Start MuJoCo bridge
    python -m simulation.common.zenoh_mujoco_bridge \
        --scene simulation/mujoco/scenes/unitree_g1.xml \
        --robot-type g1 --robot-id g1-demo-001

    # Terminal 3: Run this demo
    python demo/tunnel_integration_demo.py --robot-id g1-demo-001
"""

import argparse
import json
import logging
import time
import uuid

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TUNNEL-DEMO] %(message)s")
logger = logging.getLogger(__name__)


def send_paid_action(robot_id: str, skill_id: str, params: dict, relay_url: str):
    """Send a paid action through the Fabric relay/tunnel."""
    import httpx

    action_id = f"act_{skill_id}_{uuid.uuid4().hex[:8]}"
    idempotency_key = f"demo-{skill_id}-{uuid.uuid4().hex[:8]}"

    # Step 1: Send unpaid request (expect 402)
    logger.info("Step 1: Sending UNPAID request (expect 402)...")
    try:
        resp = httpx.post(
            f"{relay_url}/v1/robots/{robot_id}/actions",
            json={"skillId": skill_id, "params": params, "idempotencyKey": idempotency_key},
            timeout=10,
        )
        if resp.status_code == 402:
            logger.info("  ✓ Got 402 Payment Required (expected)")
        else:
            logger.warning("  ✗ Got %d instead of 402", resp.status_code)
    except Exception as e:
        logger.warning("  Unpaid request failed: %s (tunnel may not be running)", e)

    # Step 2: Send paid request with x402 proof
    logger.info("Step 2: Sending PAID request with x402 proof...")
    payment_proof = f"0x{uuid.uuid4().hex}"  # Mock payment proof
    try:
        resp = httpx.post(
            f"{relay_url}/v1/robots/{robot_id}/actions",
            json={
                "skillId": skill_id,
                "params": params,
                "idempotencyKey": idempotency_key,
                "actionId": action_id,
            },
            headers={"X-PAYMENT": payment_proof},
            timeout=10,
        )
        logger.info("  Response: %d %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("  Paid request failed: %s (tunnel may not be running)", e)
        logger.info("  Falling back to direct Zenoh publish...")

        # Fallback: publish directly to Zenoh (for demo without tunnel)
        try:
            import zenoh
            conf = zenoh.Config.from_json5('{"connect":{"endpoints":["tcp/127.0.0.1:7447"]}}')
            session = zenoh.open(conf)
            envelope = {
                "payload": {
                    "action": skill_id,
                    "skillId": skill_id,
                    "actionId": action_id,
                    "params": params,
                    "idempotencyKey": idempotency_key,
                    "robotId": robot_id,
                    "payment": {"provider": "aeon-bnb-x402", "amount": "10000", "verified": True},
                },
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            session.put("robot/tunnel/action", json.dumps(envelope))
            logger.info("  Published to Zenoh: robot/tunnel/action")
            session.close()
        except Exception as e2:
            logger.error("  Zenoh publish failed: %s", e2)

    # Step 3: Wait for result
    logger.info("Step 3: Waiting for result on robot/tunnel/result...")
    try:
        import zenoh
        conf = zenoh.Config.from_json5('{"connect":{"endpoints":["tcp/127.0.0.1:7447"]}}')
        session = zenoh.open(conf)
        results = []
        def on_result(sample):
            results.append(json.loads(bytes(sample.payload)))
        sub = session.declare_subscriber("robot/tunnel/result", on_result)

        for _ in range(100):
            time.sleep(0.1)
            if results:
                break

        if results:
            logger.info("  ✓ Result: %s", json.dumps(results[0], indent=2))
        else:
            logger.warning("  ✗ No result received within 10s")

        sub.undeclare()
        session.close()
    except Exception as e:
        logger.error("  Result listener failed: %s", e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot-id", default="g1-demo-001")
    parser.add_argument("--relay-url", default="http://localhost:8080")
    parser.add_argument("--skill", default="move_forward")
    args = parser.parse_args()

    params = {"speed": 0.5, "durationSec": 3}
    logger.info("=== Fabric Tunnel → Zenoh → MuJoCo Bridge Demo ===")
    logger.info("Robot: %s | Skill: %s", args.robot_id, args.skill)
    send_paid_action(args.robot_id, args.skill, params, args.relay_url)
    logger.info("=== Demo Complete ===")


if __name__ == "__main__":
    main()
