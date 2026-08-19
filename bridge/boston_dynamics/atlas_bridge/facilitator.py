"""x402 facilitator verification.

Checking a receipt's shape is not the same as knowing a payment happened. The
protocol checks in :mod:`x402` catch the wrong amount, the wrong asset, the
wrong network, an expired receipt and a replay — but they cannot tell a real
authorization from a well-formed forgery. Only the facilitator can, because only
it recovers the signer and checks the authorization on chain.

This client asks the facilitator the one question that matters::

    POST {facilitator}/verify  {paymentPayload, paymentRequirements}
    ->   {"isValid": true|false, "invalidReason": ...}

It **fails closed**: a network error, a timeout, a malformed answer or anything
other than an explicit ``isValid: true`` is treated as *not verified*, so a
facilitator that is merely unreachable can never authorise an action.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .task import PAYMENT_NETWORK, SKILL_PRICE_RAW, USDC_BASE_SEPOLIA

#: The facilitator named by the profile's payment policy.
DEFAULT_FACILITATOR_URL = "https://x402.org/facilitator"
#: The facilitator speaks x402's own network names rather than CAIP-2.
FACILITATOR_NETWORK = "base-sepolia"
REQUEST_TIMEOUT_S = 25.0


@dataclass(frozen=True)
class FacilitatorVerdict:
    """What the facilitator said, plus why we are treating it that way."""

    is_valid: bool
    reason: str = ""
    payer: str = ""
    reachable: bool = True

    @property
    def summary(self) -> str:
        if self.is_valid:
            return "facilitator verified the payment"
        if not self.reachable:
            return f"facilitator unreachable, failing closed: {self.reason}"
        return f"facilitator rejected the payment: {self.reason}"


def payment_requirements(
    pay_to: str,
    resource: str,
    amount: str = SKILL_PRICE_RAW,
    asset: str = USDC_BASE_SEPOLIA,
) -> dict:
    """The requirements half of a verification request, from the profile."""
    return {
        "scheme": "exact",
        "network": FACILITATOR_NETWORK,
        "maxAmountRequired": amount,
        "resource": resource,
        "description": "Boston Dynamics Atlas shelf inspection",
        "mimeType": "application/json",
        "payTo": pay_to,
        "maxTimeoutSeconds": 60,
        "asset": asset,
        "extra": {"name": "USDC", "version": "2"},
    }


class FacilitatorClient:
    """Minimal, fail-closed client for the x402 facilitator's verify endpoint."""

    def __init__(
        self, url: str = DEFAULT_FACILITATOR_URL, timeout: float = REQUEST_TIMEOUT_S
    ) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def verify(self, payment_payload: dict, requirements: dict) -> FacilitatorVerdict:
        body = json.dumps({
            "x402Version": 1,
            "paymentPayload": payment_payload,
            "paymentRequirements": requirements,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/verify",
            data=body,
            headers={
                "content-type": "application/json",
                "user-agent": "robopay-atlas-bridge/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                answer = json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                answer = json.loads(error.read())
            except Exception:  # noqa: BLE001 - any unreadable body is a rejection
                return FacilitatorVerdict(False, f"HTTP {error.code}", reachable=True)
        except Exception as error:  # noqa: BLE001 - unreachable means not verified
            return FacilitatorVerdict(False, str(error), reachable=False)

        if not isinstance(answer, dict):
            return FacilitatorVerdict(False, "facilitator returned a non-object")

        return FacilitatorVerdict(
            is_valid=answer.get("isValid") is True,
            reason=str(answer.get("invalidReason") or answer.get("error") or ""),
            payer=str(answer.get("payer") or ""),
        )


def network_for(caip2: str = PAYMENT_NETWORK) -> str:
    """Map the profile's CAIP-2 network onto the facilitator's own name."""
    return {"eip155:84532": FACILITATOR_NETWORK}.get(caip2, caip2)
