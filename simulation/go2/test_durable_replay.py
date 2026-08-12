"""Durable replay protection: keys survive a store restart (tunnel semantics).

The tunnel keeps a durable idempotency store so a replayed idempotencyKey /
txHash is rejected even after a restart. This test proves the same for the
simulator gate: it marks a key with one store instance, then opens a brand-new
store on the same file (equivalent to a process restart) and asserts the key is
still rejected -> 409 semantics are preserved.

Prints PASS/FAIL, exits nonzero on failure.
"""

import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

from payment_gate import ReplayStore, PaymentGate, params_hash  # noqa: E402


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="replay_store_"))
    store_file = tmp / "replay_store.json"
    checks = {}

    # --- 1) mark a key, then reload from disk ---------------------------
    store1 = ReplayStore(path=str(store_file))
    checks["fresh_key_accepted"] = store1.check_and_mark("idem-restart-A") is True
    checks["second_mark_same_instance_rejected"] = \
        store1.check_and_mark("idem-restart-A") is False

    # --- 2) simulate restart: brand-new store on the same file ----------
    store2 = ReplayStore(path=str(store_file))
    checks["key_rejected_after_restart"] = \
        store2.check_and_mark("idem-restart-A") is False
    checks["txhash_rejected_after_restart"] = \
        store2.check_and_mark("txhash-restart-1") is False
    checks["new_key_accepted_after_restart"] = \
        store2.check_and_mark("idem-restart-B") is True

    # --- 3) same semantics through the gate (two gate instances) --------
    gate1 = PaymentGate(store_path=str(tmp / "gate_store.json"))
    receipt = gate1.facilitator.issue_receipt(
        "act_restart", "turn_to_face", {"headingDeg": 30.0})
    env = {
        "actionId": "act_restart", "robotId": "test-robot",
        "skillId": "turn_to_face", "params": {"headingDeg": 30.0},
        "paramsHash": params_hash({"headingDeg": 30.0}),
        "idempotencyKey": "idem-gate-restart",
        "payment": receipt,
    }
    ok1, status1, _ = gate1.check(env)
    checks["gate_first_verified"] = ok1 and status1 == 200

    gate2 = PaymentGate(store_path=str(tmp / "gate_store.json"))
    ok2, status2, _ = gate2.check(env)
    checks["gate_replay_after_restart_409"] = (not ok2) and status2 == 409

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print(json.dumps({"checks": checks}, indent=1))
    ok_all = all(checks.values())
    print("PASS" if ok_all else "FAIL")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
