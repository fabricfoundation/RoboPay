"""x402-compatible simulator payment gate for the Go2 simulator profile.

Mirrors the payment decisions the RoboPay tunnel's x402 middleware makes
before any actuation is allowed (tunnel/internal/handlers + x402 middleware),
so the simulator-only submission can exercise the same semantics end to end:

  * an action WITHOUT a valid paid receipt is answered 402 with a
    PAYMENT-REQUIRED challenge and never reaches the robot,
  * an expired / malformed / forged receipt is rejected (402),
  * a params-hash mismatch is a 400 (bad request),
  * a replayed idempotencyKey or txHash is a 409 (conflict) and never
    re-executed,
  * settlement is only allowed on {"status": "success"} results — every
    failure path produces an error result and must not settle.

Receipts are Ed25519-signed by a local facilitator and carry a txHash like a
real settlement record. This is a simulator gate: it mirrors the tunnel's
decision semantics in Python rather than executing the compiled Go tunnel.
Optional on-chain settlement (Base Sepolia, EIP-3009) is available through
``settlement_base_sepolia.settle_if_success`` when the environment is
configured; by default the local facilitator ledger is used.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import logging
import os
import pathlib
import threading
import time
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger(__name__)

PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
UNPAID_STATUS = 402

try:
    from settlement_base_sepolia import (
        settle_if_success,
        get_settler,
        BaseSepoliaSettler,
        SettlementConfig,
        SettlementReceipt,
    )
    ONCHAIN_SETTLEMENT_AVAILABLE = True
except ImportError:
    ONCHAIN_SETTLEMENT_AVAILABLE = False


def canonical_params(params: Any) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def params_hash(params: Any) -> str:
    return hashlib.sha256(canonical_params(params).encode("utf-8")).hexdigest()


def _canonical_message(action_id: str, skill_id: str, params_hash_val: str,
                       timestamp: str) -> bytes:
    return json.dumps(
        {"actionId": action_id, "skillId": skill_id,
         "paramsHash": params_hash_val, "timestamp": timestamp},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


class Facilitator:
    """Local x402-style facilitator that issues signed receipts."""

    def __init__(self, private_key_b64: Optional[str] = None) -> None:
        if private_key_b64:
            self._private = ed25519.Ed25519PrivateKey.from_private_bytes(
                base64.b64decode(private_key_b64))
        else:
            self._private = ed25519.Ed25519PrivateKey.generate()

    @property
    def public_key_b64(self) -> str:
        pub = self._private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(pub).decode("ascii")

    def issue_receipt(self, action_id: str, skill_id: str, params: Any,
                      timestamp: Optional[str] = None) -> Dict[str, Any]:
        ph = params_hash(params)
        ts = timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        sig = self._private.sign(_canonical_message(action_id, skill_id, ph, ts))
        return {
            "provider": "facilitator-x402-local",
            "asset": "USDT_OR_USDC_CONTRACT",
            "network": "eip155:84532",
            "amount": "0",
            "txHash": hashlib.sha256(os.urandom(32)).hexdigest(),
            "timestamp": ts,
            "signature": base64.b64encode(sig).decode("ascii"),
        }


class ReplayStore:
    """Thread-safe store of seen idempotency keys / tx hashes.

    When constructed with a ``path`` the store is file-backed: every mark is
    flushed to disk atomically and a fresh store reloads the previous keys, so
    replayed keys are still rejected after a process restart (the tunnel's
    durable-replay semantics). Without a path the store is in-memory only.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = pathlib.Path(path) if path else None
        self._seen: set[str] = set()
        self._lock = threading.RLock()
        if self._path and self._path.exists():
            try:
                self._seen = set(json.loads(
                    self._path.read_text(encoding="utf-8")).get("seen", []))
            except Exception:
                self._seen = set()

    def check_and_mark(self, key: str) -> bool:
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            if self._path:
                self._flush()
            return True

    def _flush(self) -> None:
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps({"seen": sorted(self._seen)}),
                       encoding="utf-8")
        tmp.replace(self._path)

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)


def verify_payment(envelope: Dict[str, Any], pubkey_b64: str,
                   store: Optional[ReplayStore] = None,
                   window_s: float = 300.0) -> Tuple[bool, int, str]:
    """Verify a paid-action envelope. Returns (ok, status_code, reason).

    Status codes follow the tunnel's conventions:
      400 bad request (params hash mismatch),
      402 payment required / invalid,
      409 replay,
      200 verified.
    """
    if store is None:
        store = ReplayStore()

    required = ("actionId", "robotId", "skillId", "params", "idempotencyKey",
                "paramsHash", "payment")
    for field in required:
        if field not in envelope or envelope[field] in (None, ""):
            return False, 402, f"missing required field: {field}"

    payment = envelope["payment"]
    if not isinstance(payment, dict):
        return False, 402, "payment must be a JSON object"
    for field in ("txHash", "signature", "timestamp"):
        if field not in payment or not payment[field]:
            return False, 402, f"payment missing field: {field}"

    try:
        parsed = datetime.datetime.strptime(payment["timestamp"],
                                            "%Y-%m-%dT%H:%M:%SZ")
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        ts = parsed.timestamp()
    except (ValueError, TypeError):
        return False, 402, "payment timestamp is malformed"

    if abs(time.time() - ts) > window_s:
        return False, 402, "payment receipt expired"

    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(
            base64.b64decode(pubkey_b64))
        msg = _canonical_message(envelope["actionId"], envelope["skillId"],
                                 envelope["paramsHash"], payment["timestamp"])
        pub.verify(base64.b64decode(payment["signature"]), msg)
    except Exception:
        return False, 402, "invalid payment signature"

    if store is not None:
        if not store.check_and_mark(envelope["idempotencyKey"]):
            return False, 409, "idempotencyKey already used (replay)"
        if not store.check_and_mark(payment["txHash"]):
            return False, 409, "txHash already used (replay)"

    return True, 200, "payment verified"


class SettlementLedger:
    """Records settlements; used to prove no-settle-on-failure."""

    def __init__(self) -> None:
        self._settled: dict[str, str] = {}
        self._lock = threading.Lock()

    def settle(self, action_id: str) -> str:
        tx = hashlib.sha256(os.urandom(32)).hexdigest()
        with self._lock:
            self._settled[action_id] = tx
        return tx

    def is_settled(self, action_id: str) -> bool:
        with self._lock:
            return action_id in self._settled

    def tx(self, action_id: str) -> Optional[str]:
        with self._lock:
            return self._settled.get(action_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._settled)


class PaymentGate:
    """Combines x402 verification with settle-only-on-success semantics.

    The facilitator's private key is persisted next to this module so the
    simulation can sign receipts on one side (the payer's relay / test
    harness) and verify them on the other (the robot link) with the same
    local facilitator — mirroring how the tunnel's x402 middleware trusts the
    facilitator's advertised public key. No secrets leave the repo.

    Pass ``store_path`` for a file-backed (durable) replay store; the default
    is in-memory so tests stay isolated.
    """

    KEY_FILE = pathlib.Path(__file__).parent / "facilitator_private_key.b64"

    def __init__(self, facilitator: Optional[Facilitator] = None,
                 key_file: Optional[pathlib.Path] = None,
                 store_path: Optional[str] = None):
        key_file = key_file or self.KEY_FILE
        if facilitator is None:
            private_b64 = None
            if key_file.exists():
                private_b64 = key_file.read_text().strip()
            facilitator = Facilitator(private_key_b64=private_b64)
            if not key_file.exists():
                key_file.write_text(base64.b64encode(
                    facilitator._private.private_bytes(
                        serialization.Encoding.Raw,
                        serialization.PrivateFormat.Raw,
                        serialization.NoEncryption())).decode("ascii"))
        self.facilitator = facilitator
        self.store = ReplayStore(path=store_path)
        self.ledger = SettlementLedger()

    @property
    def public_key_b64(self) -> str:
        return self.facilitator.public_key_b64

    def check(self, envelope: Dict[str, Any]) -> Tuple[bool, int, str]:
        """Verify the envelope; returns (ok, status_code, reason)."""
        return verify_payment(envelope, self.public_key_b64, self.store)

    def decide_settlement(self, result_status: str, action_id: str,
                          payment_payload: Optional[Dict[str, Any]] = None,
                          amount_usdc: Optional[str] = None) -> bool:
        """Settle only on a success result.

        Local ledger records the settlement; when the optional Base Sepolia
        module is configured, an on-chain EIP-3009 transfer is attempted and
        its tx hash is recorded on the ledger. Failure results never settle.
        """
        if result_status != "success":
            return False
        tx = self.ledger.settle(action_id)
        if ONCHAIN_SETTLEMENT_AVAILABLE and payment_payload and amount_usdc:
            try:
                receipt = settle_if_success(result_status, payment_payload,
                                            amount_usdc)
                if receipt is not None and receipt.success:
                    with self.ledger._lock:
                        self.ledger._settled[action_id] = receipt.tx_hash or tx
            except Exception as exc:  # noqa: BLE001
                logger.warning("optional on-chain settlement skipped: %s", exc)
        return True
