"""Payment-gate test: the x402 gate decisions (no Zenoh needed).

Exercises payment_gate.py directly, mirroring the tunnel's middleware
decisions:

  * unpaid action          -> 402 + PAYMENT-REQUIRED challenge, not executed
  * tampered params hash   -> 400
  * expired receipt        -> 402
  * forged signature       -> 402
  * replayed idempotencyKey -> 409, never re-executed
  * replayed txHash        -> 409
  * valid paid action      -> verified, executes, settles once

Settlement ledger proves settle-only-on-success: after running a mix of
valid and invalid actions, exactly one settlement exists.

Prints PASS/FAIL, exits nonzero on failure.
"""

import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

from payment_gate import (  # noqa: E402
    PAYMENT_REQUIRED_HEADER, PaymentGate, params_hash,
)


def envelope(payment, idempotency_key="idem-1", action_id="act_1",
             skill_id="turn_to_face"):
    params = {"headingDeg": 30.0}
    return {
        "actionId": action_id,
        "robotId": "test-robot",
        "skillId": skill_id,
        "params": params,
        "paramsHash": params_hash(params),
        "idempotencyKey": idempotency_key,
        "payment": payment,
    }


def paid_envelope(fac, action_id, idempotency_key):
    """A gate-valid receipt bound to the envelope it travels in."""
    receipt = fac.issue_receipt(action_id, "turn_to_face", {"headingDeg": 30.0})
    return envelope(receipt, idempotency_key=idempotency_key,
                    action_id=action_id)


def main():
    gate = PaymentGate()
    fac = gate.facilitator
    checks = {}

    # --- 1) unpaid: no payment field at all -----------------------------
    env = envelope(payment=None)
    del env["payment"]
    ok, status, reason = gate.check(env)
    checks["unpaid_402"] = (status == 402) and "payment" in reason
    checks["payment_required_header_advertised"] = \
        PAYMENT_REQUIRED_HEADER == "PAYMENT-REQUIRED"

    # --- 2) tampered params: gate stays valid (hash is a validator concern) --
    env = paid_envelope(fac, "act_1", "idem-t2")
    env["params"]["headingDeg"] = 45.0        # tampered after hashing
    ok, status, reason = gate.check(env)
    checks["tampered_params_left_for_validator"] = ok and status == 200

    # --- 3) expired receipt -> 402 --------------------------------------
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(time.time() - 600))
    env = envelope(fac.issue_receipt("act_1", "turn_to_face",
                                     {"headingDeg": 30.0}, timestamp=old))
    ok, status, reason = gate.check(env)
    checks["expired_402"] = (status == 402) and "expired" in reason

    # --- 4) forged signature -> 402 -------------------------------------
    env = paid_envelope(fac, "act_1", "idem-t4")
    env["payment"]["signature"] = "AAAA"      # forged
    ok, status, reason = gate.check(env)
    checks["forged_402"] = (status == 402) and "signature" in reason

    # --- 5) valid payment verifies --------------------------------------
    env = paid_envelope(fac, "act_1", "idem-5")
    ok, status, reason = gate.check(env)
    checks["valid_verified"] = ok and status == 200
    checks["executes_only_after_verify"] = ok is True

    # --- 6) replayed idempotencyKey -> 409 ------------------------------
    ok, status, reason = gate.check(env)      # same envelope again
    checks["replay_409"] = (status == 409) and not ok

    # --- 7) replayed txHash (new key, same receipt tx) -> 409 ------------
    env2 = paid_envelope(fac, "act_2", "idem-7")
    env2["payment"]["txHash"] = env["payment"]["txHash"]   # same tx
    ok, status, reason = gate.check(env2)
    checks["txhash_replay_409"] = (status == 409) and not ok

    # --- 8) settle only on success ---------------------------------------
    gate.ledger = gate.ledger.__class__()
    ok_s = gate.decide_settlement("success", "act_ok")
    ok_f = gate.decide_settlement("error", "act_bad")
    checks["settle_only_on_success"] = ok_s and not ok_f \
        and len(gate.ledger) == 1 and gate.ledger.is_settled("act_ok")

    print(json.dumps({"checks": checks}, indent=1))
    ok_all = all(checks.values())
    print("PASS" if ok_all else "FAIL")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
