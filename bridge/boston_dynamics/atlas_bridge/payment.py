"""Payment settlement ledger for x402 flow.

Tracks settlement decisions: settle on success, no-settle on failure.
Provides audit trail for payment safety compliance.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SettlementStatus(Enum):
    PENDING = "PENDING"
    #: A real on-chain transaction moved the money. Nothing else earns this.
    SETTLED = "SETTLED"
    #: The execution succeeded and the policy authorises payment, but this run
    #: put nothing on chain. Distinct from SETTLED because an artifact that
    #: calls a protocol-level demo "SETTLED" is claiming a transfer that never
    #: happened, and a reviewer reading the artifact alone cannot tell.
    SETTLEMENT_ELIGIBLE = "SETTLEMENT_ELIGIBLE"
    SKIPPED_FAILURE = "SKIPPED_FAILURE"
    SKIPPED_UNPAID = "SKIPPED_UNPAID"
    SKIPPED_REPLAY = "SKIPPED_REPLAY"
    SKIPPED_REJECTED = "SKIPPED_REJECTED"
    SKIPPED_EXPIRED = "SKIPPED_EXPIRED"


@dataclass
class SettlementEntry:
    action_id: str
    skill_id: str
    robot_id: str
    status: SettlementStatus
    #: The payment receipt presented by the caller — an input, not a transfer.
    tx_hash: str = ""
    #: The on-chain settlement transaction, if one was actually made.
    settlement_tx_hash: str = ""
    amount: str = ""
    asset: str = ""
    network: str = ""
    block_number: int = 0
    timestamp: float = field(default_factory=time.time)
    reason: str = ""
    execution_success: bool = False


class SettlementLedger:
    """Tracks payment settlement decisions for audit trail.

    Core invariant: settlement occurs ONLY on successful execution.
    Failed/timed-out/rejected actions must NOT settle.
    """

    def __init__(self) -> None:
        self._entries: list[SettlementEntry] = []
        self._action_settled: set[str] = set()

    def record_unpaid(self, action_id: str, skill_id: str, robot_id: str) -> SettlementEntry:
        entry = SettlementEntry(
            action_id=action_id,
            skill_id=skill_id,
            robot_id=robot_id,
            status=SettlementStatus.SKIPPED_UNPAID,
            reason="No payment provided. HTTP 402 returned.",
        )
        self._entries.append(entry)
        return entry

    def record_rejected(
        self,
        action_id: str,
        skill_id: str,
        robot_id: str,
        reason: str,
        status: SettlementStatus = SettlementStatus.SKIPPED_REJECTED,
    ) -> SettlementEntry:
        """Record a payment the gate refused.

        The caller passes the status that actually applies. Labelling every
        rejection a replay — as an earlier revision did — made a wrong amount
        read as a replayed receipt in the audit trail.
        """
        entry = SettlementEntry(
            action_id=action_id,
            skill_id=skill_id,
            robot_id=robot_id,
            status=status,
            reason=reason,
        )
        self._entries.append(entry)
        return entry

    def record_execution_start(
        self,
        action_id: str,
        skill_id: str,
        robot_id: str,
        tx_hash: str,
        amount: str,
        asset: str,
        network: str,
    ) -> SettlementEntry:
        entry = SettlementEntry(
            action_id=action_id,
            skill_id=skill_id,
            robot_id=robot_id,
            status=SettlementStatus.PENDING,
            tx_hash=tx_hash,
            amount=amount,
            asset=asset,
            network=network,
        )
        self._entries.append(entry)
        return entry

    def settle_on_success(
        self,
        action_id: str,
        block_number: int = 0,
        settlement_tx_hash: str = "",
    ) -> SettlementEntry | None:
        """Mark a successful execution as paid for.

        ``SETTLED`` requires a real settlement transaction — its hash and the
        block that contains it. Without one the entry becomes
        ``SETTLEMENT_ELIGIBLE``: the execution succeeded and the policy would
        pay, but no value moved, and the ledger says so rather than publishing
        a transfer that did not happen. The payment receipt that authorised the
        run is not a settlement transaction and never fills this in.
        """
        if action_id in self._action_settled:
            return self._find_entry(action_id)
        entry = self._find_pending(action_id)
        if entry is None:
            return None
        entry.execution_success = True
        if settlement_tx_hash and block_number:
            entry.status = SettlementStatus.SETTLED
            entry.settlement_tx_hash = settlement_tx_hash
            entry.block_number = block_number
            entry.reason = "Execution succeeded. Settled on chain."
        else:
            entry.status = SettlementStatus.SETTLEMENT_ELIGIBLE
            entry.settlement_tx_hash = ""
            entry.block_number = 0
            entry.reason = (
                "Execution succeeded and settlement is authorised by policy. "
                "No on-chain transaction was made in this run."
            )
        self._action_settled.add(action_id)
        return entry

    def skip_on_failure(
        self,
        action_id: str,
        reason: str = "Execution failed. No settlement.",
    ) -> SettlementEntry | None:
        entry = self._find_pending(action_id)
        if entry is None:
            return None
        entry.status = SettlementStatus.SKIPPED_FAILURE
        entry.execution_success = False
        entry.reason = reason
        return entry

    def _find_pending(self, action_id: str) -> SettlementEntry | None:
        for entry in reversed(self._entries):
            if entry.action_id == action_id and entry.status == SettlementStatus.PENDING:
                return entry
        return None

    def _find_entry(self, action_id: str) -> SettlementEntry | None:
        for entry in reversed(self._entries):
            if entry.action_id == action_id:
                return entry
        return None

    def get_entry(self, action_id: str) -> SettlementEntry | None:
        for entry in reversed(self._entries):
            if entry.action_id == action_id:
                return entry
        return None

    def get_all(self) -> list[SettlementEntry]:
        return list(self._entries)

    def to_dict(self) -> dict:
        return {
            "entries": [
                {
                    "action_id": e.action_id,
                    "skill_id": e.skill_id,
                    "robot_id": e.robot_id,
                    "status": e.status.value,
                    "receipt_tx_hash": e.tx_hash,
                    "settlement_tx_hash": e.settlement_tx_hash or None,
                    "settled_on_chain": bool(e.settlement_tx_hash),
                    "amount": e.amount,
                    "asset": e.asset,
                    "network": e.network,
                    "block_number": e.block_number,
                    "timestamp": e.timestamp,
                    "reason": e.reason,
                    "execution_success": e.execution_success,
                }
                for e in self._entries
            ],
            "total": len(self._entries),
            "settled_on_chain": sum(
                1 for e in self._entries if e.status == SettlementStatus.SETTLED
            ),
            "settlement_eligible_not_on_chain": sum(
                1 for e in self._entries
                if e.status == SettlementStatus.SETTLEMENT_ELIGIBLE
            ),
            "skipped_failure": sum(1 for e in self._entries if e.status == SettlementStatus.SKIPPED_FAILURE),
            "skipped_unpaid": sum(1 for e in self._entries if e.status == SettlementStatus.SKIPPED_UNPAID),
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
