"""Payment layer (D1 skeleton).

State machine:
    AUTHORIZED -> EXECUTING -> SUCCESS (settle) / FAILED (no settle)

D1 uses MOCK verification + a local settlement ledger.
D7 replaces verify_payment / SettlementLedger with the real x402 facilitator
on Base Sepolia. The interfaces here are the swap points -- nothing else changes.
"""
from enum import Enum


class PaymentState(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    EXECUTING = "EXECUTING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PaymentError(Exception):
    pass


def verify_payment(payment: dict | None) -> dict:
    """Verify a payment receipt against the pick_object x402 challenge.

    D1 used a mock ("any txHash passes"). D7 replaced it with a protocol-level
    x402 verifier (flow/x402.py): the receipt must match the 402 challenge
    (amount / network / asset), txHash must be well-formed, and the txHash
    cannot be replayed. Raises PaymentError on any mismatch so the relay
    answers 402 and never dispatches an unverified action.
    """
    from flow.x402 import X402Verifier   # deferred: avoids import cycle
    return X402Verifier().verify(payment)


class SettlementAuditLog:
    """In-process AUDIT LOG -- NOT on-chain settlement.

    This records the relay's settle/skip *decisions* for the D1 demo and the
    in-process ``LoopbackTransport`` path. It is deliberately NOT the production
    payment boundary: real USDC settlement is performed exclusively by the
    shared RoboPay Go ``tunnel/`` binary (its x402 facilitator), which the
    production bridge (``bridge.FabricZenohBridge``) defers to. See
    tests/test_payment_gate.py, tests/test_x402_no_settlement.py and
    tests/test_bridge_executes.py for the real, on-chain-verifiable boundary.

    ``mode`` documents that an entry here is an audit record, never a chain tx.
    """

    mode = "protocol-audit-local-relay"

    def __init__(self):
        # action_id -> payment (audit record only; no chain side effect)
        self.settled = {}

    def settle(self, action_id: str, payment: dict) -> dict:
        self.settled[action_id] = payment
        return {"settled": True, "actionId": action_id, "mode": self.mode}

    def skip(self, action_id: str) -> dict:
        # Failure path: payment MUST NOT be settled.
        return {"settled": False, "actionId": action_id,
                "reason": "execution_failed", "mode": self.mode}


# Backwards-compatible alias so existing imports keep working.
SettlementLedger = SettlementAuditLog
