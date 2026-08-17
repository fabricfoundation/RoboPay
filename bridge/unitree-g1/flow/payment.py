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
    """Verify a payment receipt against the unitree-g1 paid-action x402 challenge.

    D1 used a mock ("any txHash passes"). D7 replaced it with a protocol-level
    x402 verifier (flow/x402.py): the receipt must match the 402 challenge
    (amount / network / asset), txHash must be well-formed, and the txHash
    cannot be replayed. Raises PaymentError on any mismatch so the relay
    answers 402 and never dispatches an unverified action.
    """
    from flow.x402 import X402Verifier   # deferred: avoids import cycle
    return X402Verifier().verify(payment)


class SettlementLedger:
    """Local stand-in for on-chain settlement (D7 swaps for real facilitator)."""

    def __init__(self):
        self.settled = {}  # action_id -> payment

    def settle(self, action_id: str, payment: dict) -> dict:
        self.settled[action_id] = payment
        return {"settled": True, "actionId": action_id}

    def skip(self, action_id: str) -> dict:
        # Failure path: payment MUST NOT be settled.
        return {"settled": False, "actionId": action_id, "reason": "execution_failed"}
