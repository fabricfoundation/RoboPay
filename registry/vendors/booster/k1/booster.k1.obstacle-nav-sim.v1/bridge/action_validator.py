"""Payment/security gate for incoming RoboPay action envelopes.
Rejects anything missing, tampered, unpaid, expired, or already
settled -- there is no path around this into the simulator."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json


REQUIRED_FIELDS = [
    "actionId", "robotId", "skillId", "params",
    "paramsHash", "idempotencyKey", "payment",
]

REQUIRED_PAYMENT_FIELDS = [
    "provider", "authorizationId", "verified", "status", "settled",
    "network", "asset", "amount", "payTo", "issuedAt", "expiresAt",
]

EXPECTED_SKILL_ID = "k1_navigate_avoid_obstacles"
EXPECTED_NETWORK = "eip155:84532"
EXPECTED_ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
EXPECTED_AMOUNT = "1000"


class ValidationError(Exception):
    """Carries a machine-readable code so the bridge can publish a precise error result."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass
class ValidatedAction:
    action_id: str
    robot_id: str
    skill_id: str
    params: dict
    idempotency_key: str
    authorization_id: str


def canonical_params_hash(params: dict) -> str:
    """sha256(canonical-json(params)) per execution-mapping.yaml."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_fields(envelope: dict, fields: list, where: str):
    missing = [f for f in fields if f not in envelope or envelope[f] is None]
    if missing:
        raise ValidationError(
            "missing_fields", f"{where} missing required field(s): {', '.join(missing)}"
        )


def validate_envelope(envelope: dict, now: datetime = None) -> ValidatedAction:
    """Raises ValidationError on the first check that fails."""
    if now is None:
        now = datetime.now(timezone.utc)

    _require_fields(envelope, REQUIRED_FIELDS, "action envelope")

    if envelope["skillId"] != EXPECTED_SKILL_ID:
        raise ValidationError(
            "unknown_skill",
            f"this bridge only serves skillId={EXPECTED_SKILL_ID!r}, got {envelope['skillId']!r}",
        )

    params = envelope["params"]
    expected_hash = canonical_params_hash(params)
    if envelope["paramsHash"] != expected_hash:
        raise ValidationError(
            "params_hash_mismatch",
            f"paramsHash does not match canonical hash of params "
            f"(expected {expected_hash}, got {envelope['paramsHash']})",
        )

    if "goal_x" not in params or "goal_y" not in params:
        raise ValidationError("invalid_params", "params must include goal_x and goal_y")

    payment = envelope["payment"]
    _require_fields(payment, REQUIRED_PAYMENT_FIELDS, "payment")

    if payment["provider"] != "x402":
        raise ValidationError("invalid_payment_provider", f"expected x402, got {payment['provider']!r}")

    if payment["network"] != EXPECTED_NETWORK:
        raise ValidationError("invalid_network", f"expected {EXPECTED_NETWORK}, got {payment['network']!r}")

    if payment["asset"] != EXPECTED_ASSET:
        raise ValidationError("invalid_asset", f"expected {EXPECTED_ASSET}, got {payment['asset']!r}")

    if payment["amount"] != EXPECTED_AMOUNT:
        raise ValidationError("invalid_amount", f"expected {EXPECTED_AMOUNT}, got {payment['amount']!r}")

    if payment["verified"] is not True:
        raise ValidationError("payment_not_verified", "payment.verified must be true")

    if payment["status"] != "authorized":
        raise ValidationError(
            "payment_not_authorized",
            f"execution requires payment.status='authorized', got {payment['status']!r}",
        )

    if payment["settled"] is not False:
        raise ValidationError(
            "payment_already_settled",
            "payment.settled must be false before execution (rejectAlreadySettled policy)",
        )

    try:
        expires_at = datetime.fromisoformat(payment["expiresAt"].replace("Z", "+00:00"))
    except ValueError as e:
        raise ValidationError("invalid_expiry_format", f"expiresAt is not valid ISO 8601: {e}")

    if now >= expires_at:
        raise ValidationError(
            "payment_expired", f"payment authorization expired at {payment['expiresAt']} (now={now.isoformat()})"
        )

    return ValidatedAction(
        action_id=envelope["actionId"],
        robot_id=envelope["robotId"],
        skill_id=envelope["skillId"],
        params=params,
        idempotency_key=envelope["idempotencyKey"],
        authorization_id=payment["authorizationId"],
    )
