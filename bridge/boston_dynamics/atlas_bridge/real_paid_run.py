"""One real payment for one real action, end to end.

Every other demo in this package proves the *refusing* side: an unpaid action is
refused, a forged authorization is refused by the live facilitator, a replay is
refused. Those are the safety properties, and they are the easy half to prove
because they need no money.

This module proves the accepting side, which is the half that needs a funded
wallet::

    EIP-3009 authorization signed by a funded wallet
        -> live x402 facilitator /verify  ->  isValid: true
        -> Zenoh  robot/tunnel/action
        -> Atlas bridge -> MuJoCo -> 3/3 targets
        -> Zenoh  robot/tunnel/result, correlated by action_id
        -> live x402 facilitator /settle  ->  real USDC moves on Base Sepolia
        -> the transaction is read back from a public RPC and decoded

Two properties are worth stating because they are what make the artifact mean
something rather than merely look impressive.

**The authorization is bound to the action.** EIP-3009 authorizations carry a
32-byte nonce chosen by the signer. This module sets it to
``keccak256(action_id)``, so the nonce inside the signed authorization — and
inside the ``AuthorizationUsed`` event the token emits on chain — is derivable
from the action identifier alone. A reviewer can recompute it and check that
this settlement paid for *this* action and not some other one. Nothing else in
an x402 receipt ties a payment to the work it bought.

**Settlement happens after execution, never before.** The facilitator is asked
to verify first, the robot runs second, and ``/settle`` is called only if the
episode actually reported every target reached. A failed episode leaves the
authorization signed but unspent, which is the behaviour the payment policy
claims and the one an operator is trusting.

The payer's private key is read from ``SETTLEMENT_PRIVATE_KEY`` and is never
printed, logged, or written to any file — the artifact records the payer's
address, which is public, and the signature, which is what the facilitator
needs anyway.

Usage::

    SETTLEMENT_PRIVATE_KEY=0x... python -m bridge.boston_dynamics.atlas_bridge.real_paid_run \\
        --payer 0x... --json-output docs/evidence/real-paid-run.json
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .bridge import ACTION_TOPIC, RESULT_TOPIC, ROBOT_ID, AtlasZenohBridge
from .facilitator import DEFAULT_FACILITATOR_URL, FACILITATOR_NETWORK
from .task import (
    BASE_SEPOLIA_CHAIN_ID,
    PAYMENT_NETWORK,
    SKILL_PRICE_RAW,
    SKILL_PRICE_USDC,
    USDC_BASE_SEPOLIA,
    USDC_DECIMALS,
)

SKILL_ID = "inspect_shelf"
#: The payee this profile has always settled to; see onchain-settlement.json.
DEFAULT_PAYEE = "0x7b9163254A21b249a0D3E34300fC81BB0A43C3e8"
RESOURCE = "https://robopay.invalid/atlas/inspect_shelf"
RPC_URL = "https://sepolia.base.org"
EXPLORER = "https://sepolia.basescan.org"
USER_AGENT = "robopay-atlas-bridge/1.0"
EPISODE_SECONDS = 12.0
RESULT_TIMEOUT_S = 240.0
HTTP_TIMEOUT_S = 60.0

#: keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
#: keccak256("AuthorizationUsed(address,bytes32)")
AUTHORIZATION_USED_TOPIC = (
    "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"
)


# -- plumbing ---------------------------------------------------------------
def _post(url: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "user-agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except Exception:  # noqa: BLE001 - an unreadable body is still a refusal
            return error.code, {}


def _rpc(method: str, params: list):
    request = urllib.request.Request(
        RPC_URL,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"content-type": "application/json", "user-agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return json.loads(response.read()).get("result")


# -- the payment ------------------------------------------------------------
def sign_authorization(action_id: str, payee: str, valid_before: int) -> tuple[dict, str, str]:
    """Sign an EIP-3009 authorization whose nonce is derived from ``action_id``.

    Returns ``(authorization, signature, payer_address)``. The private key is
    read from the environment and never leaves this function.
    """
    from eth_account import Account
    from eth_utils import keccak

    key = os.environ.get("SETTLEMENT_PRIVATE_KEY", "").strip()
    mnemonic = os.environ.get("SETTLEMENT_MNEMONIC", "").strip()
    if key:
        account = Account.from_key(key)
    elif mnemonic:
        # Accepting the recovery phrase directly saves the operator a
        # conversion step, which is the step where a key usually ends up
        # pasted somewhere it should not be. m/44'/60'/0'/0/<index> is what
        # MetaMask and most wallets use; --account-index reaches the others.
        Account.enable_unaudited_hdwallet_features()
        index = int(os.environ.get("SETTLEMENT_ACCOUNT_INDEX", "0"))
        account = Account.from_mnemonic(
            mnemonic, account_path=f"m/44'/60'/0'/0/{index}"
        )
    else:
        raise SystemExit(
            "Set SETTLEMENT_PRIVATE_KEY or SETTLEMENT_MNEMONIC in your own shell. "
            "Either is read from the environment, used only to sign locally, and "
            "never printed, logged, or written to any file."
        )
    nonce = keccak(text=action_id)
    value = int(SKILL_PRICE_RAW)

    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        # Read off the token contract itself: name() == "USDC", version() == "2".
        "domain": {
            "name": "USDC",
            "version": "2",
            "chainId": BASE_SEPOLIA_CHAIN_ID,
            "verifyingContract": USDC_BASE_SEPOLIA,
        },
        "message": {
            "from": account.address,
            "to": payee,
            "value": value,
            "validAfter": 0,
            "validBefore": valid_before,
            "nonce": nonce,
        },
    }
    signature = account.sign_typed_data(full_message=typed).signature
    authorization = {
        "from": account.address,
        "to": payee,
        "value": str(value),
        "validAfter": "0",
        "validBefore": str(valid_before),
        "nonce": "0x" + nonce.hex(),
    }
    return authorization, "0x" + signature.hex().lstrip("0x"), account.address


def payment_requirements(payee: str, resource: str) -> dict:
    return {
        "scheme": "exact",
        "network": FACILITATOR_NETWORK,
        "maxAmountRequired": SKILL_PRICE_RAW,
        "resource": resource,
        "description": "Boston Dynamics Atlas shelf inspection",
        "mimeType": "application/json",
        "payTo": payee,
        "maxTimeoutSeconds": 60,
        "asset": USDC_BASE_SEPOLIA,
        "extra": {"name": "USDC", "version": "2"},
    }


def payment_payload(authorization: dict, signature: str) -> dict:
    return {
        "x402Version": 1,
        "scheme": "exact",
        "network": FACILITATOR_NETWORK,
        "payload": {"signature": signature, "authorization": authorization},
    }


# -- the robot --------------------------------------------------------------
class Correlator:
    """Publishes one action on Zenoh and waits for its own result back."""

    def __init__(self, session) -> None:
        self._results: dict[str, dict] = {}
        self._arrived = threading.Event()
        self._publisher = session.declare_publisher(ACTION_TOPIC)
        self._subscriber = session.declare_subscriber(RESULT_TOPIC, self._on_result)

    def _on_result(self, sample) -> None:
        envelope = json.loads(bytes(sample.payload.to_bytes()).decode("utf-8"))
        self._results[envelope.get("action_id", "")] = envelope
        self._arrived.set()

    def publish(self, envelope: bytes) -> None:
        self._publisher.put(envelope)

    def await_result(self, action_id: str, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if action_id in self._results:
                return self._results[action_id]
            self._arrived.wait(0.25)
            self._arrived.clear()
        return None

    def close(self) -> None:
        self._subscriber.undeclare()
        self._publisher.undeclare()


def action_envelope(action_id: str, payment: dict, params: dict) -> bytes:
    return json.dumps({
        "payload": {
            "action": SKILL_ID,
            "skill_id": SKILL_ID,
            "robot_id": ROBOT_ID,
            "action_id": action_id,
            "idempotency_key": f"idem-{action_id}",
            "params": params,
        },
        "transaction_details": {
            "payment_payload": payment,
            "payment_requirements": payment.get("requirements"),
        },
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }).encode("utf-8")


# -- on-chain confirmation --------------------------------------------------
def confirm_on_chain(tx_hash: str, action_id: str) -> dict:
    """Read the settlement back from a public RPC and decode what it did."""
    from eth_utils import keccak

    receipt = None
    for _ in range(40):
        receipt = _rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt:
            break
        time.sleep(3)
    if not receipt:
        return {"confirmed": False, "reason": "no receipt after ~2 minutes"}

    expected_nonce = "0x" + keccak(text=action_id).hex()
    transfer: dict = {}
    authorization_nonce = ""
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if not topics:
            continue
        if topics[0].lower() == TRANSFER_TOPIC and len(topics) >= 3:
            transfer = {
                "token_contract": log["address"],
                "from": "0x" + topics[1][-40:],
                "to": "0x" + topics[2][-40:],
                "raw_amount": int(log["data"], 16),
            }
        elif topics[0].lower() == AUTHORIZATION_USED_TOPIC and len(topics) >= 3:
            authorization_nonce = topics[2]

    raw = transfer.get("raw_amount", 0)
    return {
        "confirmed": int(receipt.get("status", "0x0"), 16) == 1,
        "block_number": int(receipt.get("blockNumber", "0x0"), 16),
        "gas_used": int(receipt.get("gasUsed", "0x0"), 16),
        "explorer": f"{EXPLORER}/tx/{tx_hash}",
        "transfer": {
            **transfer,
            "amount_usdc": raw / 10**USDC_DECIMALS if raw else 0,
            "asset": "USDC",
        },
        "authorization_nonce": authorization_nonce,
        "expected_nonce_from_action_id": expected_nonce,
        # The point of the whole exercise: this settlement is provably the one
        # that paid for this action, because the nonce is derived from its id.
        "nonce_binds_settlement_to_action": (
            authorization_nonce.lower() == expected_nonce.lower()
        ),
        # The token really is the USDC the profile declares, not a lookalike.
        "asset_is_declared_usdc": (
            transfer.get("token_contract", "").lower() == USDC_BASE_SEPOLIA.lower()
        ),
        "amount_matches_declared_price": str(raw) == SKILL_PRICE_RAW,
    }


# -- the run ----------------------------------------------------------------
def run(payee: str, expected_payer: str, facilitator_url: str) -> dict:
    import zenoh

    from eth_utils import keccak

    action_id = f"act-paid-{uuid.uuid4().hex[:12]}"
    params = {"maxDurationSec": EPISODE_SECONDS}
    valid_before = int(time.time()) + 1800

    print("=" * 72)
    print("  Atlas — one real payment for one real action")
    print("=" * 72)
    print(f"  action_id : {action_id}")
    print(f"  nonce     : 0x{keccak(text=action_id).hex()}  (= keccak256(action_id))")
    print(f"  price     : {SKILL_PRICE_USDC} USDC ({SKILL_PRICE_RAW} raw) on {PAYMENT_NETWORK}")

    authorization, signature, payer = sign_authorization(action_id, payee, valid_before)
    print(f"  payer     : {payer}")
    if expected_payer and payer.lower() != expected_payer.lower():
        raise SystemExit(
            f"the configured key signs for {payer}, but --payer says "
            f"{expected_payer}. Refusing to continue.\n"
            "If you supplied a recovery phrase, the wallet may use a different "
            "account index — try SETTLEMENT_ACCOUNT_INDEX=1, 2, ... until the "
            "address above matches."
        )

    resource = f"{RESOURCE}?action_id={action_id}"
    requirements = payment_requirements(payee, resource)
    payload = payment_payload(authorization, signature)

    steps: list[dict] = []

    # 1. The live facilitator decides, before anything moves.
    status, verdict = _post(
        f"{facilitator_url}/verify",
        {"x402Version": 1, "paymentPayload": payload, "paymentRequirements": requirements},
    )
    verified = verdict.get("isValid") is True
    print(f"\n  [verify]   HTTP {status}  isValid={verdict.get('isValid')}"
          f"  {verdict.get('invalidReason') or ''}")
    steps.append({
        "step": "facilitator_verify", "http_status": status,
        "is_valid": verified, "reason": verdict.get("invalidReason") or "",
        "payer_recovered_by_facilitator": verdict.get("payer") or "",
        "decided_by": "live x402 facilitator",
    })
    if not verified:
        print("  refused before execution — nothing published, nothing settled")
        return _evidence(action_id, payer, payee, requirements, authorization,
                         steps, None, None, executed=False, settled=False)

    # 2. Only a verified payment reaches the robot.
    bridge = AtlasZenohBridge()
    session = zenoh.open(zenoh.Config())
    correlator = Correlator(session)
    time.sleep(1.0)  # let the peers discover each other before publishing
    try:
        print(f"  [execute]  publishing on {ACTION_TOPIC}")
        correlator.publish(action_envelope(action_id, payload, params))
        result = correlator.await_result(action_id, RESULT_TIMEOUT_S)
    finally:
        correlator.close()
        session.close()
        bridge.close()

    if result is None:
        print("  no result correlated within the timeout — not settling")
        return _evidence(action_id, payer, payee, requirements, authorization,
                         steps, None, None, executed=False, settled=False)

    episode = result.get("result") or {}
    completed = episode.get("targets_completed", 0)
    total = episode.get("targets_total", 0)
    succeeded = bool(episode.get("success")) and completed == total and total > 0
    print(f"  [robot]    {episode.get('status')}  {completed}/{total} targets"
          f"  contacts={episode.get('shelf_contacts')}")
    steps.append({
        "step": "robot_execution", "action_id_echoed": result.get("action_id"),
        "correlated": result.get("action_id") == action_id,
        "status": episode.get("status"), "targets_completed": completed,
        "targets_total": total, "success": succeeded,
        "transport": "Zenoh (peer mode)",
    })

    # 3. Settlement is the consequence of a successful episode, not of payment.
    if not succeeded:
        print("  episode did not succeed — the authorization stays unspent")
        steps.append({"step": "settlement", "attempted": False,
                      "reason": "execution did not report every target reached"})
        return _evidence(action_id, payer, payee, requirements, authorization,
                         steps, result, None, executed=True, settled=False)

    status, settlement = _post(
        f"{facilitator_url}/settle",
        {"x402Version": 1, "paymentPayload": payload, "paymentRequirements": requirements},
    )
    tx_hash = settlement.get("transaction") or settlement.get("txHash") or ""
    settled = settlement.get("success") is True and bool(tx_hash)
    print(f"  [settle]   HTTP {status}  success={settlement.get('success')}  tx={tx_hash}")
    steps.append({
        "step": "facilitator_settle", "http_status": status,
        "success": settled, "tx_hash": tx_hash,
        "error": settlement.get("errorReason") or settlement.get("error") or "",
        "decided_by": "live x402 facilitator",
    })

    chain = confirm_on_chain(tx_hash, action_id) if settled else None
    if chain:
        print(f"  [chain]    block {chain['block_number']}  "
              f"{chain['transfer'].get('amount_usdc')} USDC  "
              f"bound to action_id: {chain['nonce_binds_settlement_to_action']}")
    return _evidence(action_id, payer, payee, requirements, authorization,
                     steps, result, chain, executed=True, settled=settled)


def _evidence(action_id, payer, payee, requirements, authorization,
              steps, result, chain, executed: bool, settled: bool) -> dict:
    from eth_utils import keccak

    evidence = {
        "evidence": "real_paid_action_end_to_end",
        "profile_id": "boston-dynamics.atlas.mujoco-pybullet-webots-shelf-inspection.v1",
        "skill_id": SKILL_ID,
        "robot_id": ROBOT_ID,
        "action_id": action_id,
        "idempotency_key": f"idem-{action_id}",
        "payment": {
            "scheme": "exact",
            "protocol": "x402 + EIP-3009 transferWithAuthorization",
            "network": PAYMENT_NETWORK,
            "asset": USDC_BASE_SEPOLIA,
            "amount_raw": SKILL_PRICE_RAW,
            "amount_usdc": SKILL_PRICE_USDC,
            "payer": payer,
            "payee": payee,
            "authorization": authorization,
            "nonce_derivation": "keccak256(action_id)",
            "expected_nonce": "0x" + keccak(text=action_id).hex(),
            "facilitator": DEFAULT_FACILITATOR_URL,
            "requirements": requirements,
        },
        "steps": steps,
        "execution_result": result,
        "on_chain": chain,
    }
    checks = [
        ("the live facilitator verified the authorization",
         any(s.get("step") == "facilitator_verify" and s.get("is_valid") for s in steps)),
        ("the robot executed only after that verification", executed),
        ("every inspection target was reached",
         any(s.get("step") == "robot_execution" and s.get("success") for s in steps)),
        ("the result came back correlated by action_id",
         any(s.get("step") == "robot_execution" and s.get("correlated") for s in steps)),
        ("the facilitator settled a real transaction", settled),
        ("the settlement is confirmed on Base Sepolia",
         bool(chain and chain.get("confirmed"))),
        ("the settled amount is the declared skill price",
         bool(chain and chain.get("amount_matches_declared_price"))),
        ("the asset is the USDC contract the profile declares",
         bool(chain and chain.get("asset_is_declared_usdc"))),
        ("the on-chain authorization nonce is keccak256(action_id)",
         bool(chain and chain.get("nonce_binds_settlement_to_action"))),
    ]
    print("\n" + "=" * 72)
    print("  INVARIANTS")
    print("=" * 72)
    for label, ok in checks:
        print(f"  [{'OK' if ok else '!!'}] {label}")
    evidence["invariants"] = {label: ok for label, ok in checks}
    evidence["all_invariants_hold"] = all(ok for _, ok in checks)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one real paid Atlas action and settle it on Base Sepolia."
    )
    parser.add_argument("--payer", default="", help="Address the key is expected to sign for.")
    parser.add_argument("--payee", default=DEFAULT_PAYEE)
    parser.add_argument("--facilitator", default=DEFAULT_FACILITATOR_URL)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    evidence = run(args.payee, args.payer, args.facilitator.rstrip("/"))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"\n  evidence written to {args.json_output}")
    raise SystemExit(0 if evidence["all_invariants_hold"] else 1)


if __name__ == "__main__":
    main()
