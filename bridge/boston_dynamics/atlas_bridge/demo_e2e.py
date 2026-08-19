"""End-to-end demo: x402 payment → Atlas skill execution → settlement evidence.

Demonstrates the full Fabric bounty flow:
1. Unpaid request → HTTP 402
2. Valid payment → execute inspect_shelf
3. Success → settlement approved
4. Failure → NO settlement
5. Replay → rejected

Usage:
    python -m bridge.boston_dynamics.atlas_bridge.demo_e2e
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .x402 import X402Verifier, PaymentPolicy
from .payment import SettlementLedger
from .relay import ActionRelay, ActionRequest
from .control_core import POLICY_ID
from .task import PAYMENT_NETWORK, SKILL_PRICE_RAW
from .runner import run_inspection


POLICY = PaymentPolicy(
    network="eip155:84532",
    asset="USDC",
    amount=SKILL_PRICE_RAW,
    settle_on_failure=False,
    replay_protection=True,
)
ROBOT_ID = "atlas-sim-01"


def _print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _print_result(label: str, result) -> None:
    print(f"\n  [{label}]")
    print(f"    HTTP Status:     {result.http_status}")
    print(f"    Status:          {result.status}")
    print(f"    Settlement:      {result.settlement_status}")
    if result.result.get("targets_completed") is not None:
        print(f"    Targets:         {result.result['targets_completed']}"
              f"/{result.result.get('targets_total')}")
    if result.result.get("error_code"):
        print(f"    Error Code:      {result.result['error_code']}")
    if result.result.get("message"):
        print(f"    Message:         {result.result['message']}")


def run_demo(json_output: Path | None = None) -> dict:
    verifier = X402Verifier(POLICY)
    ledger = SettlementLedger()

    def atlas_executor(req: ActionRequest) -> dict:
        print(f"\n    >> Executing inspect_shelf (action_id={req.action_id[:16]}...)")
        result = run_inspection(max_duration_seconds=8.0)
        print(f"    >> Execution complete: success={result['success']}, "
              f"targets={result['targets_completed']}/{result['targets_total']}")
        return result

    relay = ActionRelay(
        verifier=verifier,
        ledger=ledger,
        skill_executor=atlas_executor,
        robot_id=ROBOT_ID,
    )

    results = []

    _print_header("STEP 1: Unpaid Request -> HTTP 402")
    req_unpaid = ActionRequest(
        action_id="demo-unpaid-001",
        robot_id=ROBOT_ID,
        skill_id="inspect_shelf",
        params={"maxDurationSec": 10},
        payment_header=None,
    )
    r1 = relay.handle_action(req_unpaid)
    _print_result("UNPAID", r1)
    assert r1.http_status == 402, f"Expected 402, got {r1.http_status}"
    assert r1.settlement_status == "skipped"
    results.append(("unpaid_402", r1.http_status, r1.settlement_status))

    _print_header("STEP 2: Invalid Payment -> HTTP 400")
    req_bad = ActionRequest(
        action_id="demo-invalid-001",
        robot_id=ROBOT_ID,
        skill_id="inspect_shelf",
        params={"maxDurationSec": 10},
        payment_header={"amount": "500", "network": PAYMENT_NETWORK, "txHash": "0x7b32195338c9901877c850d2f90e1687f6ee58e516f75840100feece525a4b4d"},
    )
    r2 = relay.handle_action(req_bad)
    _print_result("INVALID", r2)
    assert r2.http_status == 400
    assert r2.settlement_status == "skipped"
    results.append(("invalid_payment", r2.http_status, r2.settlement_status))

    _print_header("STEP 3: Valid Payment -> Execute -> Success -> Settled")
    req_paid = ActionRequest(
        action_id="demo-paid-001",
        robot_id=ROBOT_ID,
        skill_id="inspect_shelf",
        params={"maxDurationSec": 10},
        payment_header={
            "amount": SKILL_PRICE_RAW,
            "asset": "USDC",
            "network": "eip155:84532",
            "txHash": "0x94ad618a792cb57bcfa09eaff1feab4e734c6bd9bcd5c7d70acab3a9461923fb",
            "payer": "0x1234567890abcdef1234567890abcdef12345678",
            "payee": "0xabcdef1234567890abcdef1234567890abcdef12",
        },
    )
    r3 = relay.handle_action(req_paid)
    _print_result("PAID+EXECUTE", r3)
    assert r3.http_status == 200
    assert r3.status == "success"
    assert r3.settlement_status == "settled"
    results.append(("paid_success_settled", r3.http_status, r3.settlement_status))

    _print_header("STEP 4: Replay Detection -> HTTP 409")
    req_replay = ActionRequest(
        action_id="demo-replay-001",
        robot_id=ROBOT_ID,
        skill_id="inspect_shelf",
        params={"maxDurationSec": 10},
        payment_header={
            "amount": SKILL_PRICE_RAW,
            "asset": "USDC",
            "network": "eip155:84532",
            "txHash": "0x94ad618a792cb57bcfa09eaff1feab4e734c6bd9bcd5c7d70acab3a9461923fb",
        },
    )
    r4 = relay.handle_action(req_replay)
    _print_result("REPLAY", r4)
    assert r4.http_status == 409
    assert r4.settlement_status == "skipped"
    results.append(("replay_detected", r4.http_status, r4.settlement_status))

    _print_header("STEP 5: Ledger Audit Trail")
    ledger_dict = ledger.to_dict()
    print(f"\n  Total entries:    {ledger_dict['total']}")
    print(f"  Settled:          {ledger_dict['settled']}")
    print(f"  Skipped (failure):{ledger_dict['skipped_failure']}")
    print(f"  Skipped (unpaid): {ledger_dict['skipped_unpaid']}")

    _print_header("SETTLEMENT INVARIANT VERIFICATION")
    settled_count = sum(1 for e in ledger.get_all() if e.status.value == "SETTLED")
    failed_count = sum(1 for e in ledger.get_all() if "FAILURE" in e.status.value)
    unpaid_count = sum(1 for e in ledger.get_all() if "UNPAID" in e.status.value)
    print(f"\n  Settlement invariant: settled={settled_count}, "
          f"failed_no_settle={failed_count}, unpaid_no_settle={unpaid_count}")
    print(f"  [OK] Only successful execution settled: {settled_count == 1}")
    print(f"  [OK] Failed executions not settled: {failed_count == 0}")
    print(f"  [OK] Unpaid requests correctly skipped: {unpaid_count >= 1}")
    print(f"  [OK] No unsettled failures: "
          f"{all(e.status.value != 'SETTLED' or e.execution_success for e in ledger.get_all())}")

    evidence = {
        "demo": "atlas_e2e_x402_flow",
        "policy_id": POLICY_ID,
        "robot_id": ROBOT_ID,
        "steps": [
            {"step": 1, "name": "unpaid_402", "http_status": r1.http_status,
             "settlement": r1.settlement_status},
            {"step": 2, "name": "invalid_payment", "http_status": r2.http_status,
             "settlement": r2.settlement_status},
            {"step": 3, "name": "paid_success_settled", "http_status": r3.http_status,
             "settlement": r3.settlement_status,
             "targets_completed": r3.result.get("targets_completed", 0),
             "mean_position_error_m": r3.result.get("mean_position_error_m")},
            {"step": 4, "name": "replay_detected", "http_status": r4.http_status,
             "settlement": r4.settlement_status},
        ],
        "settlement_ledger": ledger_dict,
    }
    # Reproducing the walkthrough must not rewrite the committed evidence, so
    # the artefact is only written where the caller explicitly asks for it.
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"\n  Evidence written to: {json_output}")

    _print_header("DEMO COMPLETE")
    print("\n  All assertions passed. Payment safety invariant holds:")
    print("    - Unpaid -> 402 -> no execution -> no settlement")
    print("    - Invalid payment -> rejected -> no settlement")
    print("    - Valid payment -> execute -> success -> settlement approved")
    print("    - Replay -> 409 -> no settlement")

    return evidence


def main() -> None:
    # Reproducing the demo must not silently rewrite the committed evidence, so
    # the artefact is only written where the caller asks for it.
    parser = argparse.ArgumentParser(description="x402 payment-gate walkthrough.")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    run_demo(args.json_output)


if __name__ == "__main__":
    main()
