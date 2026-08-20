"""x402 payment verification for Fabric RoboPay.

Implements protocol-level x402 challenge/verification:
- Validates payment receipt structure
- Checks amount, network, asset, expiry, replay protection
- Supports optional facilitator integration for on-chain verification
"""

from __future__ import annotations

import re

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


#: An EVM transaction hash is 32 bytes: "0x" followed by 64 hex digits. A
#: receipt whose hash cannot be one is rejected before anything is executed,
#: so a settlement reference can never be an arbitrary string.
TX_HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")


class X402Error(Enum):
    MISSING_PAYMENT = "MISSING_PAYMENT"
    INVALID_FORMAT = "INVALID_FORMAT"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    NETWORK_MISMATCH = "NETWORK_MISMATCH"
    ASSET_MISMATCH = "ASSET_MISMATCH"
    EXPIRED = "EXPIRED"
    MALFORMED_TX_HASH = "MALFORMED_TX_HASH"
    FACILITATOR_REJECTED = "FACILITATOR_REJECTED"
    REPLAY_DETECTED = "REPLAY_DETECTED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


@dataclass(frozen=True)
class X402Receipt:
    amount: str
    asset: str
    network: str
    tx_hash: str
    payer: str = ""
    payee: str = ""
    expiry: float = 0.0
    block_number: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> "X402Receipt":
        return cls(
            amount=str(data.get("amount", "")),
            asset=str(data.get("asset", "")),
            network=str(data.get("network", "")),
            tx_hash=str(data.get("txHash", data.get("tx_hash", ""))),
            payer=str(data.get("payer", "")),
            payee=str(data.get("payee", "")),
            expiry=float(data.get("expiry", 0)),
            block_number=int(data.get("blockNumber", data.get("block_number", 0))),
        )


@dataclass
class X402VerificationResult:
    valid: bool
    error: Optional[X402Error] = None
    message: str = ""
    receipt: Optional[X402Receipt] = None


@dataclass(frozen=True)
class PaymentPolicy:
    network: str
    asset: str
    amount: str
    settle_on_failure: bool = False
    replay_protection: bool = True


class X402Verifier:
    """x402 payment verifier.

    Two layers, in this order:

    1. **Protocol checks** — structure, amount, asset, network, expiry, the
       shape of the settlement reference, and replay. These are cheap and they
       reject the obvious cases before anything else happens.
    2. **Facilitator verification** — the only step that can tell a real
       authorization from a well-formed forgery, because only the facilitator
       recovers the signer. Pass a :class:`~.facilitator.FacilitatorClient` to
       enable it.

    Without a facilitator the verifier is explicit about what it is: a protocol
    check. :attr:`verifies_authorization` says which of the two you have, so a
    caller can never mistake one for the other.
    """

    def __init__(
        self,
        policy: PaymentPolicy,
        facilitator=None,
        payment_requirements: dict | None = None,
    ):
        self._policy = policy
        self._facilitator = facilitator
        self._payment_requirements = payment_requirements
        self._seen_hashes: set[str] = set()

    @property
    def verifies_authorization(self) -> bool:
        """True only when a facilitator actually checks the signature."""
        return self._facilitator is not None

    @property
    def policy(self) -> PaymentPolicy:
        return self._policy

    def verify(
        self,
        payment_header: str | dict | None,
        action_id: str = "",
    ) -> X402VerificationResult:
        if payment_header is None:
            return X402VerificationResult(
                valid=False,
                error=X402Error.MISSING_PAYMENT,
                message="No payment header provided. Payment required.",
            )

        receipt = self._parse_receipt(payment_header)
        if receipt is None:
            return X402VerificationResult(
                valid=False,
                error=X402Error.INVALID_FORMAT,
                message="Malformed x402 payment receipt.",
            )

        if receipt.amount != self._policy.amount:
            return X402VerificationResult(
                valid=False,
                error=X402Error.AMOUNT_MISMATCH,
                message=f"Expected amount {self._policy.amount}, got {receipt.amount}.",
                receipt=receipt,
            )

        if receipt.network != self._policy.network:
            return X402VerificationResult(
                valid=False,
                error=X402Error.NETWORK_MISMATCH,
                message=f"Expected network {self._policy.network}, got {receipt.network}.",
                receipt=receipt,
            )

        if receipt.asset != self._policy.asset:
            return X402VerificationResult(
                valid=False,
                error=X402Error.ASSET_MISMATCH,
                message=f"Expected asset {self._policy.asset}, got {receipt.asset}.",
                receipt=receipt,
            )

        if not TX_HASH_PATTERN.match(receipt.tx_hash):
            return X402VerificationResult(
                valid=False,
                error=X402Error.MALFORMED_TX_HASH,
                message=(
                    "Settlement reference is not an EVM transaction hash: "
                    f"{receipt.tx_hash!r}."
                ),
                receipt=receipt,
            )

        if receipt.expiry > 0 and time.time() > receipt.expiry:
            return X402VerificationResult(
                valid=False,
                error=X402Error.EXPIRED,
                message="Payment receipt has expired.",
                receipt=receipt,
            )

        # Only the facilitator can distinguish a real authorization from a
        # well-formed forgery, so it runs before replay is recorded and before
        # anything is executed.
        if self._facilitator is not None:
            authorization = self._authorization_payload(payment_header)
            verdict = self._facilitator.verify(
                authorization, self._payment_requirements or {}
            )
            if not verdict.is_valid:
                return X402VerificationResult(
                    valid=False,
                    error=X402Error.FACILITATOR_REJECTED,
                    message=verdict.summary,
                    receipt=receipt,
                )

        if self._policy.replay_protection and receipt.tx_hash:
            tx_key = f"{receipt.tx_hash}:{receipt.amount}"
            if tx_key in self._seen_hashes:
                return X402VerificationResult(
                    valid=False,
                    error=X402Error.REPLAY_DETECTED,
                    message=f"Replay detected for tx {receipt.tx_hash}.",
                    receipt=receipt,
                )
            self._seen_hashes.add(tx_key)

        return X402VerificationResult(
            valid=True,
            message="Payment verified.",
            receipt=receipt,
        )

    def _parse_receipt(self, payment_header: str | dict) -> X402Receipt | None:
        try:
            if isinstance(payment_header, str):
                data = json.loads(payment_header)
            elif isinstance(payment_header, dict):
                data = payment_header
            else:
                return None

            if not all(k in data for k in ("amount", "network")):
                return None

            if "txHash" not in data and "tx_hash" not in data:
                return None

            return X402Receipt.from_dict(data)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    @staticmethod
    def _authorization_payload(payment_header) -> dict:
        """The x402 payment payload the facilitator expects to verify."""
        if isinstance(payment_header, dict):
            nested = payment_header.get("paymentPayload")
            if isinstance(nested, dict):
                return nested
            return payment_header
        return {}

    def record_settlement(self, tx_hash: str, amount: str) -> None:
        tx_key = f"{tx_hash}:{amount}"
        self._seen_hashes.add(tx_key)
