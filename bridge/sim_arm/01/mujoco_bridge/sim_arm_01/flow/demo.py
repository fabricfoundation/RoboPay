"""Reproducible pay-to-actuate transcript for sim-arm-01.

Runs the full flow end-to-end over the in-process bus and prints a transcript a
reviewer can regenerate:

    python -m sim_arm_01.flow.demo

Cases:
  1. paid + valid target   -> accepted -> success terminal result -> SETTLED
  2. unpaid                 -> 402, never published, robot never actuates
  3. tampered paramsHash    -> 400, never published
  4. duplicate idempotency  -> 409, never published
  5. paid + unreachable     -> accepted -> ACTION_FAILED terminal result -> NOT settled
"""
import json

from .envelope import ActionEnvelope, params_hash
from .payment import PaymentGuard
from .relay import InProcBus, RobotNode, RoboPayRelay


def _line(tag, msg):
    print(f"[{tag:^8}] {msg}")


def main():
    bus = InProcBus()
    guard = PaymentGuard()
    RobotNode(bus)
    relay = RoboPayRelay(bus, guard)

    # 1. paid + valid -------------------------------------------------------
    a1 = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [1.0, -0.5]},
        payment={"txHash": "0xVALID", "amountUSDC": "0.50", "chain": "base"})
    _line("submit", f"paid move_to_pose {a1.params['target_qpos']} action={a1.actionId[:8]}")
    ack = relay.submit(a1)
    _line("ack", json.dumps(ack))
    r1 = relay.await_result(a1.actionId, timeout=30)
    _line("result", f"status={r1.status} joint_error={r1.metrics.get('joint_error')} "
                     f"steps={r1.metrics.get('steps_taken')}")
    _line("settle", f"settled={relay.is_settled(a1.actionId)}  (expect True)")
    assert ack["status"] == "accepted" and r1.status == "success"
    assert relay.is_settled(a1.actionId)
    print()

    # 2. unpaid -------------------------------------------------------------
    a2 = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [1.0, -0.5]}, payment={})
    ack = relay.submit(a2)
    _line("submit", "unpaid move_to_pose")
    _line("ack", json.dumps(ack))
    assert ack["status"] == "rejected" and ack["httpStatus"] == 402
    assert relay.await_result(a2.actionId, timeout=2) is None
    _line("verify", "no result published, robot never actuated  (expect 402)")
    print()

    # 3. tampered -----------------------------------------------------------
    a3 = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [1.0, -0.5]},
        payment={"txHash": "0xVALID"})
    a3.paramsHash = params_hash({"target_qpos": [9.9, 9.9]})  # claim != actual
    ack = relay.submit(a3)
    _line("submit", "tampered paramsHash")
    _line("ack", json.dumps(ack))
    assert ack["status"] == "rejected" and ack["httpStatus"] == 400
    print()

    # 4. duplicate idempotencyKey ------------------------------------------
    a4 = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [0.3, 0.3]},
        payment={"txHash": "0xVALID"}, idempotencyKey="dup-key-001")
    relay.submit(a4)
    relay.await_result(a4.actionId, timeout=30)
    a4b = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [0.3, 0.3]},
        payment={"txHash": "0xVALID"}, idempotencyKey="dup-key-001")
    ack = relay.submit(a4b)
    _line("submit", "replayed idempotencyKey=dup-key-001")
    _line("ack", json.dumps(ack))
    assert ack["status"] == "rejected" and ack["httpStatus"] == 409
    print()

    # 5. paid + unreachable -------------------------------------------------
    a5 = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [5.0, 5.0]},   # outside +/-3.14 reachable range
        payment={"txHash": "0xVALID2", "amountUSDC": "0.50"})
    _line("submit", f"paid move_to_pose {a5.params['target_qpos']} (unreachable)")
    ack = relay.submit(a5)
    _line("ack", json.dumps(ack))
    r5 = relay.await_result(a5.actionId, timeout=30)
    _line("result", f"status={r5.status} code={r5.code} "
                     f"joint_error={r5.metrics.get('joint_error')}")
    _line("settle", f"settled={relay.is_settled(a5.actionId)}  (expect False)")
    assert ack["status"] == "accepted" and r5.status == "error"
    assert not relay.is_settled(a5.actionId)

    print("\nFULL PAY-TO-ACTUATE FLOW OK: "
          "success settles, every failure/rejection does not.")


if __name__ == "__main__":
    main()
