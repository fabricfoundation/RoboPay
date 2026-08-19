"""Action relay with x402 payment gating.

Orchestrates the full flow:
  Request → 402 check → verify payment → execute skill → settle/skip

This module connects x402 verification to the Atlas bridge execution,
providing the payment safety layer required by Fabric.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from .x402 import X402Verifier, X402Error, X402VerificationResult
from .payment import SettlementLedger


LOGGER = logging.getLogger("robopay.atlas.relay")


@dataclass(frozen=True)
class ActionRequest:
    action_id: str
    robot_id: str
    skill_id: str
    params: dict
    payment_header: str | dict | None
    idempotency_key: str = ""


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    status: str
    skill_id: str
    result: dict
    settlement_status: str
    http_status: int


class ActionRelay:
    """Relay that gates skill execution behind x402 payment verification.

    Flow:
    1. Receive action request
    2. Check payment header presence
       - Missing → HTTP 402, no execution, no settlement
    3. Verify payment receipt
       - Invalid → HTTP 402/400, no execution, no settlement
    4. Execute skill
    5. On success → settlement approved
       - On failure → no settlement
    """

    def __init__(
        self,
        verifier: X402Verifier,
        ledger: SettlementLedger,
        skill_executor: Callable[[ActionRequest], dict],
        robot_id: str = "atlas-sim-01",
    ):
        self._verifier = verifier
        self._ledger = ledger
        self._skill_executor = skill_executor
        self._robot_id = robot_id

    def handle_action(self, request: ActionRequest) -> ActionResult:
        if request.robot_id != self._robot_id:
            return ActionResult(
                action_id=request.action_id,
                status="error",
                skill_id=request.skill_id,
                result={"error_code": "ROBOT_MISMATCH", "message": "Robot ID mismatch."},
                settlement_status="skipped",
                http_status=400,
            )

        verification = self._verifier.verify(
            request.payment_header,
            action_id=request.action_id,
        )

        if not verification.valid:
            return self._handle_payment_failure(request, verification)

        if verification.receipt:
            self._ledger.record_execution_start(
                action_id=request.action_id,
                skill_id=request.skill_id,
                robot_id=request.robot_id,
                tx_hash=verification.receipt.tx_hash,
                amount=verification.receipt.amount,
                asset=verification.receipt.asset,
                network=verification.receipt.network,
            )

        try:
            exec_result = self._skill_executor(request)
        except Exception as error:
            LOGGER.exception("Skill execution failed for %s", request.action_id)
            self._ledger.skip_on_failure(
                request.action_id,
                reason=f"Execution exception: {error}",
            )
            return ActionResult(
                action_id=request.action_id,
                status="error",
                skill_id=request.skill_id,
                result={
                    "error_code": "EXECUTION_FAILED",
                    "message": str(error),
                },
                settlement_status="skipped_failure",
                http_status=500,
            )

        success = exec_result.get("success", False)

        if success:
            self._ledger.settle_on_success(request.action_id)
            return ActionResult(
                action_id=request.action_id,
                status="success",
                skill_id=request.skill_id,
                result=exec_result,
                settlement_status="settled",
                http_status=200,
            )
        else:
            self._ledger.skip_on_failure(
                request.action_id,
                reason=exec_result.get("error_code", "Skill execution returned failure."),
            )
            return ActionResult(
                action_id=request.action_id,
                status="error",
                skill_id=request.skill_id,
                result=exec_result,
                settlement_status="skipped_failure",
                http_status=200,
            )

    def _handle_payment_failure(
        self,
        request: ActionRequest,
        verification: X402VerificationResult,
    ) -> ActionResult:
        error = verification.error
        if error == X402Error.MISSING_PAYMENT:
            self._ledger.record_unpaid(
                request.action_id, request.skill_id, request.robot_id,
            )
            http_status = 402
        elif error == X402Error.REPLAY_DETECTED:
            self._ledger.record_rejected(
                request.action_id, request.skill_id, request.robot_id,
                reason=verification.message,
            )
            http_status = 409
        else:
            self._ledger.record_rejected(
                request.action_id, request.skill_id, request.robot_id,
                reason=verification.message,
            )
            http_status = 400

        return ActionResult(
            action_id=request.action_id,
            status="error",
            skill_id=request.skill_id,
            result={
                "error_code": error.value if error else "PAYMENT_ERROR",
                "message": verification.message,
            },
            settlement_status="skipped",
            http_status=http_status,
        )
