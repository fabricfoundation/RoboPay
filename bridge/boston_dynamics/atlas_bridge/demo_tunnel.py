"""End-to-end payment-validated action over the real Zenoh transport.

This is the flow the RoboPay README describes, with nothing stubbed between the
payment gate and the simulator::

    payment-validated action request
        -> x402 verification (tunnel side)
        -> Zenoh  robot/tunnel/action
        -> Atlas bridge
        -> MuJoCo inspection episode
        -> Zenoh  robot/tunnel/result
        -> correlation by action_id
        -> settlement, only on success

``demo_e2e.py`` covers the same payment invariants in-process and runs in
milliseconds; this module proves the transport itself. It needs
``eclipse-zenoh`` installed and opens a peer-mode session, so no external router
is required.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from pathlib import Path

from .bridge import ACTION_TOPIC, RESULT_TOPIC, ROBOT_ID, AtlasZenohBridge
from .facilitator import FacilitatorClient, payment_requirements
from .payment import SettlementLedger, SettlementStatus
from .task import PAYMENT_NETWORK, SKILL_PRICE_RAW
from .x402 import PaymentPolicy, X402Verifier

SKILL_ID = "inspect_shelf"
PAYEE = "0x7b9163254A21b249a0D3E34300fC81BB0A43C3e8"
RESOURCE = "https://robopay.invalid/atlas/inspect_shelf"
#: Duration for each episode in the demo; short so the walkthrough stays quick.
EPISODE_SECONDS = 8.0
RESULT_TIMEOUT_S = 180.0


def _rejection_status(error) -> SettlementStatus:
    """Map an x402 rejection onto the ledger status that actually describes it."""
    from .x402 import X402Error

    if error is X402Error.REPLAY_DETECTED:
        return SettlementStatus.SKIPPED_REPLAY
    if error is X402Error.EXPIRED:
        return SettlementStatus.SKIPPED_EXPIRED
    return SettlementStatus.SKIPPED_REJECTED


def _receipt(tx_hash: str, amount: str = SKILL_PRICE_RAW) -> dict:
    return {
        "amount": amount,
        "asset": "USDC",
        "network": PAYMENT_NETWORK,
        "txHash": tx_hash,
    }


def _envelope(action_id: str, payment: dict | None, params: dict | None = None) -> bytes:
    return json.dumps({
        "payload": {
            "action": SKILL_ID,
            "skill_id": SKILL_ID,
            "robot_id": ROBOT_ID,
            "action_id": action_id,
            "idempotency_key": f"idem-{action_id}",
            "params": params if params is not None else {"maxDurationSec": EPISODE_SECONDS},
        },
        "transaction_details": {"payment_payload": payment} if payment else {},
        "timestamp": "2026-08-19T00:00:00Z",
    }).encode("utf-8")


class TunnelSide:
    """The paying side: verifies x402, publishes, correlates, then settles."""

    def __init__(self, session, facilitator=None, requirements=None) -> None:
        self.verifier = X402Verifier(
            PaymentPolicy(network=PAYMENT_NETWORK, asset="USDC", amount=SKILL_PRICE_RAW),
            facilitator=facilitator,
            payment_requirements=requirements,
        )
        self.verifies_authorization = self.verifier.verifies_authorization
        self.ledger = SettlementLedger()
        self._results: dict[str, dict] = {}
        self._arrived = threading.Event()
        self._publisher = session.declare_publisher(ACTION_TOPIC)
        self._subscriber = session.declare_subscriber(RESULT_TOPIC, self._on_result)
        self.steps: list[dict] = []

    def _on_result(self, sample) -> None:
        envelope = json.loads(bytes(sample.payload.to_bytes()).decode("utf-8"))
        self._results[envelope["action_id"]] = envelope
        self._arrived.set()

    def _await_result(self, action_id: str, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if action_id in self._results:
                return self._results[action_id]
            self._arrived.wait(0.2)
            self._arrived.clear()
        return None

    def request(self, name: str, payment: dict | None, params: dict | None = None) -> dict:
        """Run one request the whole way through and record what happened."""
        action_id = f"act-{name}-{uuid.uuid4().hex[:8]}"
        print(f"\n  [{name}] action_id={action_id}")

        verification = self.verifier.verify(payment)
        if not verification.valid:
            if payment is None:
                self.ledger.record_unpaid(action_id, SKILL_ID, ROBOT_ID)
            else:
                self.ledger.record_rejected(
                    action_id, SKILL_ID, ROBOT_ID, verification.message,
                    status=_rejection_status(verification.error),
                )
            step = {
                "step": name,
                "http_status": 402 if payment is None else 400,
                "published_to_zenoh": False,
                "executed": False,
                "settlement_eligible": False,
                "settled_on_chain": False,
                "error_code": verification.error.value if verification.error else None,
                "message": verification.message,
            }
            print(f"    x402 rejected -> HTTP {step['http_status']} ({step['error_code']})")
            print("    nothing published to Zenoh, simulator never touched")
            self.steps.append(step)
            return step

        receipt = verification.receipt
        self.ledger.record_execution_start(
            action_id=action_id, skill_id=SKILL_ID, robot_id=ROBOT_ID,
            tx_hash=receipt.tx_hash, amount=receipt.amount,
            asset=receipt.asset, network=receipt.network,
        )
        print(f"    x402 verified -> publishing on {ACTION_TOPIC}")
        self._publisher.put(_envelope(action_id, payment, params))
        result = self._await_result(action_id, RESULT_TIMEOUT_S)

        if result is None:
            self.ledger.skip_on_failure(action_id, "No correlated result arrived.")
            step = {
                "step": name, "http_status": 504, "published_to_zenoh": True,
                "executed": False, "settlement_eligible": False,
                "settled_on_chain": False,
                "error_code": "RESULT_TIMEOUT", "message": "No correlated result arrived.",
            }
            print("    no correlated result within the timeout")
            self.steps.append(step)
            return step

        succeeded = result["status"] == "success"
        print(f"    result correlated on {RESULT_TOPIC}: status={result['status']}")
        if succeeded:
            inner = result["result"]
            print(
                f"    targets={inner.get('targets_completed')}/{inner.get('targets_total')}"
                f"  collisions={inner.get('shelf_contacts')}  fall={inner.get('fall_detected')}"
            )

        if succeeded:
            # No wallet here, so the run becomes eligible for settlement.
            # Claiming "settled" would assert a transfer this demo never makes.
            self.ledger.settle_on_success(action_id)
            self.verifier.record_settlement(receipt.tx_hash, receipt.amount)
            print("    settlement eligible (nothing moved on chain)")
        else:
            self.ledger.skip_on_failure(
                action_id, f"Correlated tunnel result reported {result['status']}."
            )
            print("    not eligible for settlement")

        step = {
            "step": name,
            "action_id": action_id,
            "http_status": 200,
            "published_to_zenoh": True,
            "executed": True,
            "settlement_eligible": succeeded,
            "settled_on_chain": False,
            "settlement_tx_hash": None,
            "correlation": {
                key: result.get(key)
                for key in ("action_id", "robot_id", "skill_id", "params_hash",
                            "idempotency_key", "profile_id")
            },
            "result_status": result["status"],
            "targets_completed": result["result"].get("targets_completed"),
            "targets_total": result["result"].get("targets_total"),
            "shelf_contacts": result["result"].get("shelf_contacts"),
            "fall_detected": result["result"].get("fall_detected"),
        }
        self.steps.append(step)
        return step

    def close(self) -> None:
        self._subscriber.undeclare()
        self._publisher.undeclare()


def run_demo(json_output: Path | None = None) -> dict:
    import zenoh

    print("=" * 68)
    print("  Atlas payment-validated action over the real Zenoh transport")
    print("=" * 68)

    bridge = AtlasZenohBridge()
    print(f"  bridge listening on {bridge.action_topic} as {bridge.robot_id}")

    session = zenoh.open(zenoh.Config())
    tunnel = TunnelSide(session)
    print(
        "  payment verification: protocol checks"
        + (" + live facilitator" if tunnel.verifies_authorization else " only")
    )
    time.sleep(1.5)  # let the peers discover each other

    paid_hash = "0x" + "5b" * 32
    try:
        # The strongest gate the bridge has: ask the live x402 facilitator to
        # verify a structurally perfect but unsigned authorization. Only the
        # facilitator can tell the difference, and it must refuse.
        forged = {
            "x402Version": 1,
            "scheme": "exact",
            "network": "base-sepolia",
            "payload": {
                "signature": "0x" + "11" * 65,
                "authorization": {
                    "from": "0x520C3Ff276456A217c0dFadABeEb2d7081d6cCd4",
                    "to": PAYEE,
                    "value": SKILL_PRICE_RAW,
                    "validAfter": "0",
                    "validBefore": "9999999999",
                    "nonce": "0x" + "22" * 32,
                },
            },
        }
        verdict = FacilitatorClient().verify(
            forged, payment_requirements(pay_to=PAYEE, resource=RESOURCE)
        )
        print("\n  [forged-authorization] asking the live x402 facilitator")
        print(f"    facilitator reachable : {verdict.reachable}")
        print(f"    isValid               : {verdict.is_valid}")
        print(f"    reason                : {verdict.reason or '-'}")
        print("    nothing published to Zenoh, simulator never touched")
        tunnel.steps.append({
            "step": "forged-authorization",
            "http_status": 402,
            "published_to_zenoh": False,
            "executed": False,
            "settlement_eligible": False,
            "settled_on_chain": False,
            "facilitator_reachable": verdict.reachable,
            "facilitator_is_valid": verdict.is_valid,
            "facilitator_reason": verdict.reason,
        })

        tunnel.request("unpaid", None)
        tunnel.request("wrong-amount", _receipt("0x" + "ab" * 32, amount="500"))
        tunnel.request("bad-tx-hash", _receipt("0xnot-a-transaction-hash"))
        tunnel.request("paid", _receipt(paid_hash))
        tunnel.request("replay", _receipt(paid_hash))
        tunnel.request("bad-params", _receipt("0x" + "cd" * 32), params={"speedScale": 0.5})
    finally:
        tunnel.close()
        session.close()
        bridge.close()

    ledger = tunnel.ledger.to_dict()
    settled = [s for s in tunnel.steps if s.get("settlement_eligible")]
    evidence = {
        "demo": "atlas_tunnel_e2e",
        "transport": "Zenoh (peer mode)",
        "action_topic": ACTION_TOPIC,
        "result_topic": RESULT_TOPIC,
        "robot_id": ROBOT_ID,
        "skill_id": SKILL_ID,
        "price_raw": SKILL_PRICE_RAW,
        "network": PAYMENT_NETWORK,
        "steps": tunnel.steps,
        "settlement_ledger": ledger,
        "payment_verification": (
            "protocol_checks_and_facilitator" if tunnel.verifies_authorization
            else "protocol_checks_only"
        ),
        # Stated next to the steps rather than only in prose, so an artifact
        # read on its own cannot be mistaken for proof that value moved.
        "settlement": "eligible_not_on_chain",
        "settlement_tx_hash": None,
        "accepted_receipt": (
            "synthetic; this demo proves the transport and the refusal paths. "
            "real-paid-run.json is the artifact where USDC actually moves."
        ),
    }

    print("\n" + "=" * 68)
    print("  INVARIANTS")
    print("=" * 68)
    checks = [
        ("exactly one request became eligible for settlement", len(settled) == 1),
        ("the eligible request is the payment-validated one",
         bool(settled) and settled[0]["step"] == "paid"),
        ("the payment-validated request completed every target",
         bool(settled) and settled[0]["targets_completed"] == settled[0]["targets_total"]),
        ("unverified payments never reached Zenoh",
         all(not s["published_to_zenoh"] for s in tunnel.steps if not s.get("executed"))),
        ("every executed request was correlated by action_id",
         all(s["correlation"]["action_id"] == s["action_id"]
             for s in tunnel.steps if s.get("executed"))),
        ("the live facilitator refused a forged authorization",
         any(s["step"] == "forged-authorization" and s["facilitator_is_valid"] is False
             for s in tunnel.steps)),
    ]
    for label, ok in checks:
        print(f"  [{'OK' if ok else '!!'}] {label}")
    evidence["invariants"] = {label: ok for label, ok in checks}

    # Reproducing the walkthrough must not rewrite the committed evidence, so
    # the artefact is only written where the caller explicitly asks for it.
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"\n  evidence written to {json_output}")
    evidence["all_invariants_hold"] = all(ok for _, ok in checks)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="Paid Atlas action over real Zenoh.")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    evidence = run_demo(args.json_output)
    raise SystemExit(0 if evidence["all_invariants_hold"] else 1)


if __name__ == "__main__":
    main()
