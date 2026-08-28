"""Payment-safety layer.

All checks run on the relay BEFORE an action is published to robot/tunnel/action,
so an invalid, unpaid, tampered, or replayed request never actuates the robot.

Rules enforced:
  - unpaid / missing receipt      -> 402, no publish
  - paramsHash must match params  -> 400 (tamper protection)
  - duplicate idempotencyKey      -> 409 (no double execution)
  - payment settles ONLY on a successful terminal result; failure/timeout never
    settles. Settlement is driven by the result handler, not by submission.
"""
import time

from .envelope import ActionEnvelope, params_hash


class PaymentError(Exception):
    """Raised when a request fails a payment-safety check."""
    def __init__(self, http_status: int, code: str, message: str):
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message


class PaymentGuard:
    def __init__(self, dedup_window_seconds: int = 3600):
        self._dedup_window = dedup_window_seconds
        self._seen: dict[str, float] = {}      # idempotencyKey -> first-seen ts
        self._settled: dict[str, bool] = {}     # actionId -> settled (success only)

    def verify_request(self, action: ActionEnvelope) -> None:
        """Validate a request before publishing. Raises PaymentError if unsafe."""
        payment = action.payment or {}
        if not payment.get("txHash"):
            raise PaymentError(402, "PAYMENT_REQUIRED",
                               "no payment receipt on request")

        if action.paramsHash != params_hash(action.params):
            raise PaymentError(400, "PARAMS_TAMPERED",
                               "paramsHash does not match params")

        self._purge_expired()
        if action.idempotencyKey in self._seen:
            raise PaymentError(409, "DUPLICATE_REQUEST",
                               "idempotencyKey already used")
        self._seen[action.idempotencyKey] = time.time()

    def settle(self, action_id: str) -> None:
        """Mark payment settled. Called ONLY after a successful terminal result."""
        self._settled[action_id] = True

    def is_settled(self, action_id: str) -> bool:
        return self._settled.get(action_id, False)

    def _purge_expired(self) -> None:
        cutoff = time.time() - self._dedup_window
        self._seen = {k: t for k, t in self._seen.items() if t >= cutoff}
