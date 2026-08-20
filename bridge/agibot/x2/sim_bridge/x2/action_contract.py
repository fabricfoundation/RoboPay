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


#: Keys that identify the wrapper the Fabric tunnel actually puts on the wire.
#:
#: The tunnel does not publish the action envelope directly. `POST /action`
#: sits behind its x402 middleware, and on a verified payment the handler
#: publishes `{payload, transaction_details, timestamp}` to
#: `robot/tunnel/action`, where `payload` is the client's request body verbatim
#: and `transaction_details` carries the x402 payload and requirements the
#: middleware resolved. See tunnel/internal/handlers/handlers.go.
#:
#: A bridge that only understood the flat envelope would silently ignore every
#: message the real tunnel sends, which is a integration that passes its own
#: tests and works with nothing.
_TUNNEL_WRAPPER_KEYS = ("payload", "transaction_details")


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


def unwrap_tunnel_envelope(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten the Fabric tunnel's wire format, if that is what arrived.

    Messages published straight to the topic -- by the test client, or by any
    relay that already speaks the flat envelope -- are returned untouched, so
    both shapes work and there is one parser rather than two.

    The payment block is *always* rebuilt from `transaction_details` and never
    taken from the client's body, even when the body carries one. That is the
    point of the wrapper: the body is attacker-controlled, while
    `transaction_details` is what the tunnel's x402 middleware resolved before
    it agreed to forward anything. Trusting a `payment.verified` that came from
    the request body would let anyone who can reach the topic assert their own
    payment.

    Note that `verified` here means verified, not settled. The tunnel publishes
    after the facilitator verifies the payment and before it is settled, so
    there is no transaction hash yet -- which is exactly why settlement is the
    robot's decision to report, and why `settle=false` on failure is worth
    anything at all.
    """
    if not all(key in raw for key in _TUNNEL_WRAPPER_KEYS):
        return raw

    inner = raw.get("payload")
    if not isinstance(inner, dict):
        raise ActionRejected(
            RejectionCode.MALFORMED,
            "tunnel envelope carries a non-object payload",
        )

    details = raw.get("transaction_details")
    details = details if isinstance(details, dict) else {}
    payload = details.get("payment_payload")
    payload = payload if isinstance(payload, dict) else None

    # x402 v2 keeps the resolved requirements on the payload as `accepted`;
    # the tunnel reports them separately as well. Either is authoritative.
    accepted = details.get("payment_requirements")
    if not isinstance(accepted, dict) and payload is not None:
        accepted = payload.get("accepted")
    accepted = accepted if isinstance(accepted, dict) else {}

    flat = dict(inner)
    payment: dict[str, Any] = {
        "provider": "x402",
        "amount": str(accepted.get("amount", "")),
        "asset": str(accepted.get("asset", "")),
        "network": str(accepted.get("network", "")),
        "verified": payload is not None,
    }
    if payload is not None:
        # Digest the whole authorisation rather than reaching for a
        # scheme-specific field: x402 keeps `payload` as an opaque
        # scheme-defined map, so a nonce path that works for exact-EVM would
        # break on the next scheme. A digest is stable, scheme-agnostic, and
        # enough to tie this action to one payment.
        payment["authorizationRef"] = _digest(payload)
    if details.get("tx_hash"):
        payment["txHash"] = str(details["tx_hash"])
    flat["payment"] = payment
    return flat


def _digest(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
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
    #: Settlement reference. Absent on arrival under the real x402 lifecycle:
    #: the resource server verifies, runs its handler, and only settles after
    #: the handler succeeds, so a transaction hash does not exist yet at the
    #: moment the robot is asked to move. Populated when a relay settles first.
    tx_hash: str | None = None
    #: Digest of the exact x402 authorisation the tunnel verified. This is the
    #: reference that *is* available on arrival, and it is what lets the robot
    #: tie the action it performed to a specific payment after the fact.
    authorization_ref: str | None = None

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
                authorization_ref=(
                    str(raw["authorizationRef"])
                    if raw.get("authorizationRef")
                    else None
                ),
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
        if self.authorization_ref:
            out["authorizationRef"] = self.authorization_ref
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
        raw = unwrap_tunnel_envelope(raw)
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
        if not (self.payment.tx_hash or self.payment.authorization_ref):
            raise ActionRejected(
                RejectionCode.PAYMENT_INVALID,
                "verified payment carries no reference to the authorisation",
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
