"""The paid action envelope, and the rules for refusing one.

Everything the robot is ever asked to do arrives as one of these. The bounty's
acceptance criteria are specific about what has to survive the trip and what
has to be rejected, so those rules live here rather than being scattered
through the bridge:

  * the Zenoh payload must preserve actionId, robotId, skillId,
    idempotencyKey, paramsHash and payment;
  * invalid, expired or replayed requests must not publish to Zenoh or
    actuate the robot;
  * a duplicate idempotency key must not execute twice.

`paramsHash` is the part worth explaining. It is a hash of the parameters the
payer actually signed for. Recomputing it here and comparing means a request
whose parameters were altered in flight -- paid to nudge the puck 5cm, edited
to shove it off the table -- is rejected before it reaches the simulator. The
robot never has to trust that the routing layer left the body alone.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: Fields the criteria require to survive routing, in the order we report them.
REQUIRED_FIELDS = (
    "actionId",
    "robotId",
    "skillId",
    "idempotencyKey",
    "paramsHash",
    "payment",
)


class RejectionCode:
    """Stable reason codes. These end up in logs and in the result envelope."""

    MALFORMED = "MALFORMED_ENVELOPE"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_ROBOT = "UNKNOWN_ROBOT"
    UNKNOWN_SKILL = "UNKNOWN_SKILL"
    PARAMS_TAMPERED = "PARAMS_HASH_MISMATCH"
    EXPIRED = "ACTION_EXPIRED"
    REPLAYED = "IDEMPOTENCY_REPLAY"
    PAYMENT_MISSING = "PAYMENT_REQUIRED"
    PAYMENT_INVALID = "PAYMENT_INVALID"
    PARAMS_OUT_OF_RANGE = "PARAMS_OUT_OF_RANGE"


class ActionRejected(Exception):
    """Raised when an envelope must not reach the robot."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def canonical_params_hash(params: dict[str, Any]) -> str:
    """Hash parameters the way the payer is expected to have hashed them.

    Canonical JSON -- sorted keys, no incidental whitespace -- so that two
    semantically identical parameter sets always produce the same digest
    regardless of how they were serialised upstream.
    """
    blob = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Payment:
    """The payment attached to an action, as presented by the tunnel."""

    provider: str
    amount: str
    asset: str
    network: str
    #: Set once the tunnel has verified the x402 authorisation.
    verified: bool = False
    #: Settlement reference, present for a verified payment.
    tx_hash: str | None = None

    @classmethod
    def from_json(cls, raw: Any) -> "Payment":
        if not isinstance(raw, dict):
            raise ActionRejected(
                RejectionCode.PAYMENT_MISSING, "payment block is missing"
            )
        try:
            return cls(
                provider=str(raw["provider"]),
                amount=str(raw["amount"]),
                asset=str(raw["asset"]),
                network=str(raw["network"]),
                verified=bool(raw.get("verified", False)),
                tx_hash=(str(raw["txHash"]) if raw.get("txHash") else None),
            )
        except KeyError as exc:
            raise ActionRejected(
                RejectionCode.PAYMENT_MISSING,
                f"payment block is missing {exc.args[0]!r}",
            ) from exc

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "provider": self.provider,
            "amount": self.amount,
            "asset": self.asset,
            "network": self.network,
            "verified": self.verified,
        }
        if self.tx_hash:
            out["txHash"] = self.tx_hash
        return out


@dataclass(frozen=True)
class ActionEnvelope:
    """One paid action request, already checked for structural validity."""

    action_id: str
    robot_id: str
    skill_id: str
    params: dict[str, Any]
    idempotency_key: str
    params_hash: str
    payment: Payment
    expires_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # -- parsing ----------------------------------------------------------

    @classmethod
    def from_bytes(cls, payload: bytes) -> "ActionEnvelope":
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ActionRejected(
                RejectionCode.MALFORMED, f"payload is not valid JSON: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise ActionRejected(
                RejectionCode.MALFORMED, "payload is not a JSON object"
            )
        return cls.from_json(raw)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "ActionEnvelope":
        missing = [f for f in REQUIRED_FIELDS if not raw.get(f)]
        if missing:
            raise ActionRejected(
                RejectionCode.MISSING_FIELD,
                f"envelope is missing required field(s): {', '.join(missing)}",
            )
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise ActionRejected(
                RejectionCode.MALFORMED, "params must be a JSON object"
            )
        expires = raw.get("expiresAt")
        expires_at = _parse_time(expires) if expires else None
        return cls(
            action_id=str(raw["actionId"]),
            robot_id=str(raw["robotId"]),
            skill_id=str(raw["skillId"]),
            params=params,
            idempotency_key=str(raw["idempotencyKey"]),
            params_hash=str(raw["paramsHash"]),
            payment=Payment.from_json(raw["payment"]),
            expires_at=expires_at,
            raw=raw,
        )

    def to_json(self) -> dict[str, Any]:
        """Re-serialise, preserving every field the criteria require."""
        out: dict[str, Any] = {
            "actionId": self.action_id,
            "robotId": self.robot_id,
            "skillId": self.skill_id,
            "params": dict(self.params),
            "idempotencyKey": self.idempotency_key,
            "paramsHash": self.params_hash,
            "payment": self.payment.to_json(),
        }
        if self.expires_at is not None:
            out["expiresAt"] = self.expires_at.isoformat()
        return out

    # -- checks -----------------------------------------------------------

    def require_robot(self, robot_id: str) -> None:
        if self.robot_id != robot_id:
            raise ActionRejected(
                RejectionCode.UNKNOWN_ROBOT,
                f"action addressed to {self.robot_id!r}, this robot is {robot_id!r}",
            )

    def require_untampered_params(self) -> None:
        expected = canonical_params_hash(self.params)
        if expected != self.params_hash:
            raise ActionRejected(
                RejectionCode.PARAMS_TAMPERED,
                "params do not hash to the value the payer authorised "
                f"(declared {self.params_hash}, computed {expected})",
            )

    def require_unexpired(self, now: datetime | None = None) -> None:
        if self.expires_at is None:
            return
        moment = now or datetime.now(timezone.utc)
        if moment > self.expires_at:
            raise ActionRejected(
                RejectionCode.EXPIRED,
                f"action expired at {self.expires_at.isoformat()}",
            )

    def require_verified_payment(self) -> None:
        if not self.payment.verified:
            raise ActionRejected(
                RejectionCode.PAYMENT_MISSING,
                "payment has not been verified by the tunnel",
            )
        if not self.payment.tx_hash:
            raise ActionRejected(
                RejectionCode.PAYMENT_INVALID,
                "verified payment carries no settlement reference",
            )


def _parse_time(value: Any) -> datetime:
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ActionRejected(
            RejectionCode.MALFORMED, f"expiresAt is not an ISO-8601 time: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
