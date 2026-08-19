"""RoboPay bridge relay (D1 in-process payment gateway + transport client).

Orchestrates: request -> payment verify -> transport(action) -> result -> settle/no-settle.

PRODUCTION PAYMENT BOUNDARY IS THE GO TUNNEL, NOT THIS FILE.
This ``Relay`` is the legacy D1 path used by ``flow.demo`` and the loopback
tests. It records settle/skip *decisions* in an in-process audit log
(``flow.payment.SettlementAuditLog``) and never touches a chain. The real
x402 payment gate AND USDC settlement are enforced by the shared RoboPay Go
``tunnel/`` binary, which the production bridge (``bridge.FabricZenohBridge``)
defers to -- see tests/test_payment_gate.py, tests/test_x402_no_settlement.py
and tests/test_bridge_executes.py. Treat any ``settled: True`` from this relay
as an audit record, verified on-chain only via the Tunnel's facilitator.

The transport is the swappable seam: real Zenoh in production, Loopback/Local
in tests. Payment + idempotency + settlement logic is independent of the
transport, so changing the medium never touches the payment contract.
"""
from flow.envelope import TaskEnvelope
from flow.payment import verify_payment, PaymentError, PaymentState, SettlementLedger
from flow.zenoh_transport import LoopbackTransport

try:
    from flow.x402 import X402Verifier, X402Error
except Exception:                      # pragma: no cover - optional module
    X402Verifier = None
    X402Error = PaymentError

try:
    from flow import profiles
except Exception:                       # pragma: no cover - profiles are optional
    profiles = None


class Relay:
    def __init__(self, executor=None, transport=None, ledger=None):
        if transport is None:
            if executor is None:
                raise ValueError("provide executor or transport")
            # D1 backward-compat: wrap an executor in the in-process transport.
            transport = LoopbackTransport(executor)
        self.transport = transport
        self.ledger = ledger or SettlementLedger()
        self.processed_keys = {}  # idempotency_key -> action_id
        # One verifier per relay: replay protection must span the relay's
        # lifetime (a txHash can never be settled twice by this robot).
        self.x402 = X402Verifier() if X402Verifier is not None else None

    # -- profile-driven 402 -------------------------------------------------
    def _payment_required(self, skill_id: str, error: str | None = None) -> dict:
        """402 challenge built from profiles/payment-policy.yaml + skills.yaml.

        If the manifests cannot be read we still answer 402: a missing YAML may
        never turn into a free execution.
        """
        if profiles is not None:
            try:
                return profiles.payment_required(skill_id, error)
            except Exception:
                pass
        body = {"status": 402, "paymentRequired": True}
        if error:
            body["error"] = error
        return body

    def handle(self, request: dict) -> dict:
        skill_id = request.get("skill")

        # 1) Idempotency: reject replayed keys. No re-execution, no re-settle.
        key = request.get("idempotencyKey")
        if key and key in self.processed_keys:
            return {
                "status": "rejected",
                "reason": "duplicate_idempotency_key",
                "actionId": self.processed_keys[key],
            }

        # 2) Payment required -> 402, do NOT execute.
        if not request.get("payment"):
            return self._payment_required(skill_id)

        # 3) Verify payment through the x402 challenge (protocol-level:
        #    amount/network/asset match + well-formed txHash + no replay).
        #    Unverified -> 402, robot never touched.
        try:
            if self.x402 is not None:
                self.x402.verify(request["payment"])
            else:
                verify_payment(request["payment"])
        except (PaymentError, X402Error) as e:
            return self._payment_required(skill_id, str(e))

        # 3b) Validate the request against skills.yaml BEFORE touching the
        #     robot. A malformed request is rejected, never executed, never
        #     settled, and never consumes the idempotency key.
        if profiles is not None:
            try:
                profiles.validate_params(skill_id, request.get("params"))
            except profiles.ParamError as e:
                return {"status": "rejected", "reason": f"invalid_params:{e}",
                        "settled": False}
            except profiles.ProfileError as e:
                return {"status": "rejected", "reason": str(e), "settled": False}

        # 4) AUTHORIZED -> build action envelope.
        env = TaskEnvelope.from_request(request)
        state = PaymentState.AUTHORIZED

        # 5) EXECUTING: dispatch over the transport (Zenoh / loopback).
        state = PaymentState.EXECUTING
        result = self.transport.send_action(env.to_action_dict())

        # 6) Settlement decision by execution outcome.
        if result.get("status") == "completed":
            state = PaymentState.SUCCESS
            self.ledger.settle(env.action_id, env.payment)
            status = "completed"
        else:
            state = PaymentState.FAILED
            self.ledger.skip(env.action_id)  # NO settlement on failure
            status = "failed"

        # 7) Record idempotency AFTER a real execution attempt.
        self.processed_keys[key] = env.action_id

        return {
            "actionId": env.action_id,
            "skill": env.skill_id,
            "status": status,
            "message": result.get("message"),
            # Simulator state the reviewer can check: object displacement,
            # measured contact force, stage reached, engine used.
            "metrics": result.get("metrics") or {},
            "paymentState": state.value,
            # Audit record only. On-chain settlement is performed by the RoboPay
            # Tunnel facilitator (see bridge.FabricZenohBridge); this relay's
            # ledger is a local decision log, never a chain tx.
            "settled": env.action_id in self.ledger.settled,
            "verification": self.ledger.mode,
        }
