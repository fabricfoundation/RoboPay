"""The whole path, with nothing stood in for.

``demo_go_tunnel.py`` drives the real Go tunnel but stands in for the hosted
Fabric backend with a local WebSocket proxy. ``real_paid_run.py`` settles a real
payment but reaches the robot over Zenoh directly. This module removes both
substitutions and runs the path the bounty actually describes::

    client
      -> Fabric relay        https://api.fabric.foundation/api/core   (hosted, real)
      -> Go tunnel           this repository's binary, dialled out over WSS
      -> x402 middleware     -> live facilitator
      -> Zenoh               robot/tunnel/action
      -> Atlas bridge        -> MuJoCo, three inspection targets
      -> Zenoh               robot/tunnel/result
      -> Fabric relay        terminal status, correlated by action_id
      -> settlement          USDC on Base Sepolia

Four things are demonstrated here that no other artifact in this profile shows:

* **Robot discovery** — the relay is asked what robot is connected.
* **Skill discovery and price discovery** — the payment is built from the price
  the relay advertises, not from a constant compiled into this script.
* **The relay's own refusal** — an unpaid action is refused with `402` by the
  hosted service, and the payment requirements come back in its response.
* **The relay's terminal status** — the result is read back from the relay
  rather than from Zenoh, correlated by `action_id`.

The authorization nonce is ``keccak256(action_id)``, as in ``real_paid_run.py``,
so the settlement stays verifiably bound to the action it paid for.

The signing key is read from ``SETTLEMENT_PRIVATE_KEY`` or
``SETTLEMENT_MNEMONIC`` and is never printed, logged, or written to any file.

``--dry-run`` stops after the 402 and signs nothing, which is enough to prove
discovery and the relay's refusal without spending anything.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .bridge import AtlasZenohBridge
from .task import (
    PAYMENT_NETWORK,
    SKILL_PRICE_RAW,
    SKILL_PRICE_USDC,
    USDC_BASE_SEPOLIA,
)

FABRIC_API_BASE = os.environ.get(
    "FABRIC_API_BASE_URL", "https://api.fabric.foundation/api/core"
)
FABRIC_WS_URL = os.environ.get(
    "PROXY_WS_URL", "wss://api.fabric.foundation/api/core/ws/robot"
)
DEFAULT_PAYEE = "0x7b9163254A21b249a0D3E34300fC81BB0A43C3e8"
SKILL_ID = "inspect_shelf"
PROFILE_ID = "boston-dynamics.atlas.mujoco-pybullet-webots-shelf-inspection.v1"
PROFILE_DIR = (
    Path(__file__).resolve().parents[3]
    / "registry" / "vendors" / "boston-dynamics" / "atlas" / PROFILE_ID
)
RPC_URL = "https://sepolia.base.org"
EXPLORER = "https://sepolia.basescan.org"
USER_AGENT = "robopay-atlas-bridge/1.0"

EPISODE_SECONDS = 20.0
TUNNEL_CONNECT_TIMEOUT_S = 90.0
STATUS_TIMEOUT_S = 300.0
#: POST /action answers 202 straight away, but the status polling that follows
#: has to outlast the episode budget plus the tunnel's own margin.
HTTP_TIMEOUT_S = 240.0
TERMINAL_STATES = {"succeeded", "failed", "timeout", "settlement_failed"}
#: A settled action needs one more poll than a finished one: the tunnel
#: settles after the result arrives, so "succeeded" can precede "settled".
SETTLEMENT_POLL_S = 90.0


# -- HTTP -------------------------------------------------------------------
def _request(method: str, url: str, body: dict | None = None,
             headers: dict | None = None) -> tuple[int, dict, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("user-agent", USER_AGENT)
    if data is not None:
        request.add_header("content-type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            raw = response.read().decode() or "{}"
            return response.status, _json(raw), dict(response.headers)
    except urllib.error.HTTPError as error:
        raw = error.read().decode() or "{}"
        return error.code, _json(raw), dict(error.headers)


def _json(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:400]}
    return parsed if isinstance(parsed, dict) else {"body": parsed}


def _decode_header(value: str | None) -> dict:
    """x402 sends requirements base64-encoded, occasionally as plain JSON."""
    if not value:
        return {}
    try:
        return json.loads(base64.b64decode(value).decode())
    except Exception:  # noqa: BLE001 - fall back to a plain body
        return _json(value)


def _header(headers: dict, name: str) -> str | None:
    for key, value in headers.items():
        if key.upper() == name.upper():
            return value
    return None


def _rpc(method: str, params: list):
    request = urllib.request.Request(
        RPC_URL,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"content-type": "application/json", "user-agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return json.loads(response.read()).get("result")


# -- the tunnel -------------------------------------------------------------
class Tunnel:
    """This repository's Go tunnel, dialled out to the hosted Fabric relay."""

    def __init__(self, binary: Path, robot_id: str, payee: str, workdir: Path) -> None:
        self.log_path = workdir / "tunnel.log"
        (workdir / "config.json").write_text(json.dumps({
            "robot_id": robot_id,
            "evm_payee_address": payee,
            "price": f"${SKILL_PRICE_USDC}",
            "network": PAYMENT_NETWORK,
        }, indent=2), encoding="utf-8")

        environment = dict(os.environ)
        environment["PROXY_WS_URL"] = FABRIC_WS_URL
        # zenohc.dll sits beside the binary on Windows.
        environment["PATH"] = f"{binary.parent}{os.pathsep}{environment.get('PATH', '')}"
        self._log = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(binary)], cwd=str(workdir), env=environment,
            stdout=self._log, stderr=subprocess.STDOUT, text=True,
        )

    def wait_until_connected(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return False
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
            if "ws connected to proxy" in text or "connected to proxy" in text:
                return True
            time.sleep(1.0)
        return False

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self._log.close()


# -- the payment ------------------------------------------------------------
def sign_for(action_id: str, accepted: dict) -> tuple[dict, str, str]:
    """Sign an authorization matching the requirements the *relay* sent back.

    The amount, payee, asset and network all come from the 402 response rather
    than from constants here, so the payment is for the price the robot
    actually advertises. The nonce is derived from the action id, which is what
    keeps the eventual settlement bound to this action.
    """
    from eth_account import Account
    from eth_utils import keccak

    key = os.environ.get("SETTLEMENT_PRIVATE_KEY", "").strip()
    mnemonic = os.environ.get("SETTLEMENT_MNEMONIC", "").strip()
    if key:
        account = Account.from_key(key)
    elif mnemonic:
        Account.enable_unaudited_hdwallet_features()
        index = int(os.environ.get("SETTLEMENT_ACCOUNT_INDEX", "0"))
        account = Account.from_mnemonic(mnemonic, account_path=f"m/44'/60'/0'/0/{index}")
    else:
        raise SystemExit(
            "Set SETTLEMENT_PRIVATE_KEY or SETTLEMENT_MNEMONIC in your own shell."
        )

    nonce = keccak(text=action_id)
    # x402 v2 calls it "amount"; v1 called it "maxAmountRequired".
    value = int(accepted.get("amount") or accepted.get("maxAmountRequired")
                or SKILL_PRICE_RAW)
    payee = accepted.get("payTo") or DEFAULT_PAYEE
    asset = accepted.get("asset") or USDC_BASE_SEPOLIA
    extra = accepted.get("extra") or {"name": "USDC", "version": "2"}
    valid_before = int(time.time()) + max(
        int(accepted.get("maxTimeoutSeconds") or 0), 1800
    )

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
        "domain": {
            "name": extra.get("name", "USDC"),
            "version": extra.get("version", "2"),
            "chainId": 84532,
            "verifyingContract": asset,
        },
        "message": {
            "from": account.address, "to": payee, "value": value,
            "validAfter": 0, "validBefore": valid_before, "nonce": nonce,
        },
    }
    signature = account.sign_typed_data(full_message=typed).signature
    authorization = {
        "from": account.address, "to": payee, "value": str(value),
        "validAfter": "0", "validBefore": str(valid_before),
        "nonce": "0x" + nonce.hex(),
    }
    return authorization, "0x" + signature.hex().lstrip("0x"), account.address


def payment_header(authorization: dict, signature: str, accepted: dict,
                   x402_version: int) -> str:
    """Build the payment header the tunnel's own middleware will accept.

    x402 v2 matches an incoming payment against the advertised requirements on
    scheme, network, amount, asset **and** payTo, all read from an ``accepted``
    object on the payload. Sending scheme and network at the top level — the v1
    shape — matches nothing, and the middleware answers "No matching payment
    requirements". Echoing the requirements object verbatim is therefore not
    redundancy: it is what says which of the advertised options is being paid.
    """
    payload = {
        "x402Version": x402_version,
        "payload": {"signature": signature, "authorization": authorization},
    }
    if x402_version >= 2:
        payload["accepted"] = accepted
    else:
        payload["scheme"] = accepted.get("scheme", "exact")
        payload["network"] = accepted.get("network", PAYMENT_NETWORK)
    return base64.b64encode(json.dumps(payload).encode()).decode()


# -- on-chain confirmation --------------------------------------------------
def confirm_on_chain(tx_hash: str, action_id: str) -> dict:
    from eth_utils import keccak

    TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    AUTH_USED = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"

    receipt = None
    for _ in range(40):
        receipt = _rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt:
            break
        time.sleep(3)
    if not receipt:
        return {"confirmed": False, "reason": "no receipt"}

    expected = "0x" + keccak(text=action_id).hex()
    transfer, used_nonce = {}, ""
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if topics and topics[0].lower() == TRANSFER and len(topics) >= 3:
            transfer = {
                "token_contract": log["address"],
                "from": "0x" + topics[1][-40:],
                "to": "0x" + topics[2][-40:],
                "raw_amount": int(log["data"], 16),
            }
        elif topics and topics[0].lower() == AUTH_USED and len(topics) >= 3:
            used_nonce = topics[2]

    raw = transfer.get("raw_amount", 0)
    return {
        "confirmed": int(receipt.get("status", "0x0"), 16) == 1,
        "block_number": int(receipt.get("blockNumber", "0x0"), 16),
        "submitted_by": (_rpc("eth_getTransactionByHash", [tx_hash]) or {}).get("from", ""),
        "explorer": f"{EXPLORER}/tx/{tx_hash}",
        "transfer": {**transfer, "amount_usdc": raw / 1_000_000 if raw else 0},
        "authorization_nonce": used_nonce,
        "expected_nonce_from_action_id": expected,
        "nonce_binds_settlement_to_action": used_nonce.lower() == expected.lower(),
        "asset_is_declared_usdc": transfer.get("token_contract", "").lower()
        == USDC_BASE_SEPOLIA.lower(),
    }


def authorization_used_on_chain(payer: str, action_id: str,
                                settled_in_block: int = 0) -> dict:
    """Ask the token contract whether this authorization was ever spent.

    Proving that a failed action settled is easy — there is a transaction to
    point at. Proving it did *not* is harder, because "we recorded no hash" is
    an absence of evidence rather than evidence of absence. EIP-3009 tokens keep
    their own map of spent authorization nonces and expose it as
    ``authorizationState(authorizer, nonce)``, so the question can be put to the
    contract instead: for a nonce derived from the action id, a false answer is
    the token itself saying nobody was charged for this action.

    When a settlement exists, the question is pinned to the block that contains
    it and asked only once the chain head has moved past that block. A public
    endpoint will serve a receipt before it has applied the block's state, and
    answering from that window reports a spent authorization as unspent — which
    is exactly the wrong direction for this check to be wrong in. Pinning to a
    block keeps the answer deterministic rather than retrying until it agrees.
    """
    from eth_utils import keccak

    nonce = keccak(text=action_id)
    selector = "0x" + keccak(text="authorizationState(address,bytes32)").hex()[:8]
    data = selector + payer[2:].lower().rjust(64, "0") + nonce.hex()

    tag = "latest"
    if settled_in_block:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                head = int(_rpc("eth_blockNumber", []), 16)
            except Exception:  # noqa: BLE001 - keep waiting for a usable answer
                head = 0
            if head >= settled_in_block + 2:
                break
            time.sleep(3)
        tag = hex(settled_in_block)

    try:
        raw = _rpc("eth_call", [{"to": USDC_BASE_SEPOLIA, "data": data}, tag])
        used = bool(int(raw, 16))
        answered = True
    except Exception:  # noqa: BLE001 - an unanswered question is not a proof
        used, answered = False, False
    return {
        "authorizer": payer,
        "nonce": "0x" + nonce.hex(),
        "nonce_derivation": "keccak256(action_id)",
        "contract": USDC_BASE_SEPOLIA,
        "method": "authorizationState(address,bytes32)",
        "queried_at_block": tag,
        "used": used,
        "queried": answered,
    }


# -- the run ----------------------------------------------------------------
def run(binary: Path, robot_id: str, payee: str, dry_run: bool,
        max_duration: float = EPISODE_SECONDS,
        expect_failure: bool = False) -> dict:
    action_id = f"atlas-inspect-{int(time.time())}"
    steps: list[dict] = []
    discovery: dict = {}
    chain = None
    terminal = None

    print("=" * 74)
    print("  Atlas through the hosted Fabric relay — nothing stood in for")
    print("=" * 74)
    print(f"  relay     : {FABRIC_API_BASE}")
    print(f"  robot_id  : {robot_id}")
    print(f"  action_id : {action_id}")

    # The bridge and the tunnel have to agree on the identity the relay
    # routes by; the bridge reads it from the environment.
    os.environ["ROBOT_ID"] = robot_id
    # Discovery answers from the profile's own catalogue, so the price a caller
    # is quoted cannot drift from the price the registry publishes.
    os.environ.setdefault("SKILL_CATALOG_PATH", str(PROFILE_DIR / "skill-catalog.json"))
    os.environ.setdefault("ROBOT_PROFILE_ID", PROFILE_ID)
    bridge = AtlasZenohBridge()
    workdir = Path(tempfile.mkdtemp(prefix="atlas_fabric_"))
    tunnel = Tunnel(binary, robot_id, payee, workdir)
    try:
        if not tunnel.wait_until_connected(TUNNEL_CONNECT_TIMEOUT_S):
            tail = tunnel.log_path.read_text(encoding="utf-8", errors="replace")[-800:]
            raise SystemExit(f"the tunnel never reached the relay:\n{tail}")
        print("  tunnel connected to the hosted relay\n")

        # 1. Robot discovery, then skill and price discovery.
        status, skills_body, _ = _request(
            "GET", f"{FABRIC_API_BASE}/robots/{robot_id}/skills"
        )
        skills = skills_body.get("skills") or []
        discovery = {
            "http_status": status,
            "robot_id": skills_body.get("robot_id") or robot_id,
            "robot_discovered": status == 200,
            "skills": skills,
            "skill_ids": sorted(s.get("skill_id", "") for s in skills),
        }
        chosen = next((s for s in skills if s.get("skill_id") == SKILL_ID), {})
        discovered_price = str(chosen.get("price_usdc") or "")
        discovery["discovered_skill"] = chosen.get("skill_id", "")
        discovery["discovered_price_usdc"] = discovered_price
        print(f"  [discovery] HTTP {status}  skills={discovery['skill_ids']}")
        print(f"              {SKILL_ID} @ {discovered_price or '?'} USDC")
        steps.append({"step": "discovery", **discovery})

        action_url = f"{FABRIC_API_BASE}/robots/{robot_id}/action"
        action_body = {
            "action": SKILL_ID,
            "skill_id": SKILL_ID,
            "robot_id": robot_id,
            "action_id": action_id,
            "idempotency_key": action_id,
            "params": {"maxDurationSec": max_duration},
        }

        # 2. The relay itself refuses an unpaid action.
        status, unpaid_body, headers = _request("POST", action_url, action_body)
        requirements = _decode_header(_header(headers, "PAYMENT-REQUIRED"))
        accepted = (requirements.get("accepts") or [{}])[0]
        x402_version = int(requirements.get("x402Version") or 1)
        print(f"  [unpaid]    HTTP {status}   payTo={accepted.get('payTo')}"
              f"  amount={accepted.get('amount') or accepted.get('maxAmountRequired')}"
              f"  network={accepted.get('network')}  x402Version={x402_version}")
        steps.append({
            "step": "unpaid_action", "http_status": status,
            "payment_required_header": bool(requirements),
            "requirements": accepted, "x402_version": x402_version,
            "body": unpaid_body, "refused_by": "hosted Fabric relay + tunnel x402 middleware",
        })

        if dry_run:
            print("\n  dry run: nothing signed, nothing spent")
            return _evidence(robot_id, action_id, payee, discovery, steps,
                             None, None, dry_run=True)

        # 3. Pay for the price the robot advertised.
        authorization, signature, payer = sign_for(action_id, accepted)
        print(f"  payer     : {payer}")
        header = payment_header(authorization, signature, accepted, x402_version)
        status, paid_body, paid_headers = _request(
            "POST", action_url, action_body, {"PAYMENT-SIGNATURE": header}
        )
        # The middleware settles as part of accepting the payment and reports
        # the result in this header — so in *this* path settlement precedes
        # execution, unlike real_paid_run.py where it follows it.
        payment_response = _decode_header(_header(paid_headers, "PAYMENT-RESPONSE"))
        print(f"  [paid]      HTTP {status}  {paid_body.get('status') or ''}"
              f"  action_id={paid_body.get('action_id') or ''}")
        steps.append({
            "step": "paid_action", "http_status": status,
            "accepted": status == 202,
            "immediate": True,
            "action_id_echoed": paid_body.get("action_id"),
            "status_url": paid_body.get("status_url"),
            "payment_response": payment_response,
            # The tunnel answers as soon as the action is accepted and settles
            # from a background watcher, so acceptance says nothing about the
            # outcome and nothing about payment.
            "settlement_ordering": "settled by the tunnel after the result, only on success",
            "body": paid_body,
        })
        if status != 202 and not expect_failure:
            return _evidence(robot_id, action_id, payee, discovery, steps,
                             None, None, payer=payer)

        # 4. The relay's own terminal status, not Zenoh's.
        status_url = f"{FABRIC_API_BASE}/robots/{robot_id}/action/{action_id}/status"
        deadline = time.monotonic() + STATUS_TIMEOUT_S
        while time.monotonic() < deadline:
            code, candidate, _ = _request("GET", status_url)
            if code == 200 and candidate.get("state") in TERMINAL_STATES:
                terminal = candidate
                # A successful episode settles a moment later, from the tunnel's
                # watcher, so keep reading until the settlement half lands too.
                if candidate.get("state") != "succeeded":
                    break
                settle_deadline = time.monotonic() + SETTLEMENT_POLL_S
                while time.monotonic() < settle_deadline:
                    code, candidate, _ = _request("GET", status_url)
                    if code == 200 and (candidate.get("settled")
                                        or candidate.get("settlement_error")):
                        terminal = candidate
                        break
                    time.sleep(3)
                break
            time.sleep(3)
        if terminal is None:
            print("  no terminal status from the relay within the timeout")
            return _evidence(robot_id, action_id, payee, discovery, steps,
                             None, None, payer=payer)

        result = terminal.get("result") or {}
        print(f"  [relay]     state={terminal.get('state')}"
              f"  settled={terminal.get('settled')}"
              f"  targets={result.get('targets_completed')}/{result.get('targets_total')}")
        settlement = terminal.get("settlement") or {}
        steps.append({
            "step": "terminal_status", "state": terminal.get("state"),
            "action_id": terminal.get("action_id"),
            "correlated": terminal.get("action_id") == action_id,
            "params_hash": terminal.get("params_hash"),
            "idempotency_key": terminal.get("idempotency_key"),
            "targets_completed": result.get("targets_completed"),
            "targets_total": result.get("targets_total"),
            "settled": bool(terminal.get("settled")),
            "settlement": settlement or None,
            "settlement_error": terminal.get("settlement_error") or None,
            "read_from": "hosted Fabric relay",
        })

        # 5. The settlement the tunnel performed — or did not.
        tx_hash = settlement.get("transaction") or ""
        if tx_hash:
            chain = confirm_on_chain(tx_hash, action_id)
            print(f"  [chain]     block {chain['block_number']}"
                  f"  {chain['transfer'].get('amount_usdc')} USDC"
                  f"  bound to action_id: {chain['nonce_binds_settlement_to_action']}")
            steps.append({"step": "settlement", "tx_hash": tx_hash, **chain})

        # Asked last, and deliberately so: a settlement that has been submitted
        # but not yet mined has not spent its authorization, so putting this
        # question before the receipt is confirmed answers about a transaction
        # that has not landed. On the failing path there is no receipt to wait
        # for and the answer is immediate.
        authorization = authorization_used_on_chain(
            payer, action_id, (chain or {}).get("block_number", 0)
        )
        print(f"  [token]     authorization spent on chain: {authorization['used']}")
        steps.append({"step": "authorization_state", **authorization})
        return _evidence(robot_id, action_id, payee, discovery, steps,
                         terminal, chain, payer=payer, expect_failure=expect_failure)
    finally:
        tunnel.close()
        bridge.close()
        print(f"\n  tunnel log: {tunnel.log_path}")


def _evidence(robot_id, action_id, payee, discovery, steps, terminal, chain,
              payer: str = "", dry_run: bool = False,
              expect_failure: bool = False) -> dict:
    def step(name: str) -> dict:
        return next((s for s in steps if s.get("step") == name), {})

    evidence = {
        "evidence": "atlas_fabric_relay_end_to_end",
        "profile_id": PROFILE_ID,
        "relay": FABRIC_API_BASE,
        "relay_transport": FABRIC_WS_URL,
        "stood_in_for": "nothing — the relay, the tunnel, Zenoh, the simulator and "
                        "the facilitator are all the real components",
        "robot_id": robot_id,
        "skill_id": SKILL_ID,
        "action_id": action_id,
        "idempotency_key": action_id,
        "payer": payer,
        "payee": payee,
        "network": PAYMENT_NETWORK,
        "asset": USDC_BASE_SEPOLIA,
        "dry_run": dry_run,
        "discovery": discovery,
        "steps": steps,
        "terminal_status": terminal,
        "on_chain": chain,
    }
    settled = bool(next((s for s in steps if s.get("step") == "terminal_status"), {})
                   .get("settled"))
    failed = (terminal or {}).get("state") == "failed"
    if not dry_run:
        evidence["payment_safety"] = {
            "settlement_ordering": "POST /action answers 202 immediately; the tunnel "
                                   "settles from a background watcher and only when "
                                   "the correlated result reports success",
            "execution_failed": failed,
            "settled": settled,
            "settled_despite_failure": failed and settled,
        }
    checks = [
        ("the relay reported the robot connected", discovery.get("robot_discovered") is True),
        ("skill discovery returned the inspection skill",
         discovery.get("discovered_skill") == SKILL_ID),
        ("the price was discovered, not assumed",
         discovery.get("discovered_price_usdc") == SKILL_PRICE_USDC),
        ("the relay quoted the discovered price",
         str((step("unpaid_action").get("requirements") or {}).get("amount")
             or (step("unpaid_action").get("requirements") or {}).get("maxAmountRequired")
             or "") == SKILL_PRICE_RAW),
        ("the relay refused an unpaid action with 402",
         step("unpaid_action").get("http_status") == 402),
        ("the relay advertised payment requirements",
         bool(step("unpaid_action").get("payment_required_header"))),
    ]
    if not dry_run and expect_failure:
        # The unhappy path, and the property that matters most about it: a paid
        # action that does not succeed must not be settled.
        paid = step("paid_action")
        result = (terminal or {}).get("result") or {}
        checks += [
            ("the action was accepted immediately, as the contract says",
             paid.get("http_status") == 202),
            ("the tunnel did not settle a failed action",
             step("terminal_status").get("settled") is False),
            ("no settlement transaction exists",
             not (step("terminal_status").get("settlement") or {}).get("transaction")),
            ("nothing was transferred on chain", chain is None),
            # The token's own record, so the absence is evidence rather than
            # merely an absent record on our side.
            ("the token contract has no record of the authorization being spent",
             step("authorization_state").get("queried") is True
             and step("authorization_state").get("used") is False),
            ("the status endpoint reported the action failed",
             step("terminal_status").get("state") == "failed"),
            ("the status carries the real reason, not a generic one",
             result.get("success") is False and bool(result.get("error_code"))),
            ("the failed action is still correlated by action_id",
             bool(step("terminal_status").get("correlated"))),
        ]
    elif not dry_run:
        checks += [
            ("the paid action was accepted", bool(step("paid_action").get("accepted"))),
            ("the relay reported the action succeeded",
             step("terminal_status").get("state") == "succeeded"),
            ("every inspection target was reached",
             step("terminal_status").get("targets_completed")
             == step("terminal_status").get("targets_total")
             and bool(step("terminal_status").get("targets_total"))),
            ("the terminal status is correlated by action_id",
             bool(step("terminal_status").get("correlated"))),
            ("the relay answered 202 immediately, before the robot finished",
             step("paid_action").get("http_status") == 202),
            ("the tunnel settled only after the result",
             bool(step("terminal_status").get("settled"))),
            ("the settlement is confirmed on Base Sepolia",
             bool(chain and chain.get("confirmed"))),
            ("the on-chain nonce is keccak256(action_id)",
             bool(chain and chain.get("nonce_binds_settlement_to_action"))),
            ("the token contract records the authorization as spent",
             step("authorization_state").get("used") is True),
        ]
    print("\n" + "=" * 74)
    print("  INVARIANTS")
    print("=" * 74)
    for label, ok in checks:
        print(f"  [{'OK' if ok else '!!'}] {label}")
    evidence["invariants"] = {label: ok for label, ok in checks}
    evidence["all_invariants_hold"] = all(ok for _, ok in checks)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Atlas skill through the hosted Fabric relay."
    )
    parser.add_argument("--tunnel", type=Path, required=True)
    parser.add_argument("--robot-id", default=f"atlas-sim-{int(time.time())}")
    parser.add_argument("--payee", default=DEFAULT_PAYEE)
    parser.add_argument("--max-duration", type=float, default=EPISODE_SECONDS,
                        help="Episode budget. Too small a value makes the episode "
                             "fail, which is how the failure path is exercised.")
    parser.add_argument("--expect-failure", action="store_true",
                        help="Assert the episode failed, to prove a failed run is "
                             "reported as failed rather than quietly as success.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Stop after the 402; sign nothing, spend nothing.")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    if not args.tunnel.is_file():
        raise SystemExit(f"tunnel binary not found: {args.tunnel}")

    evidence = run(args.tunnel, args.robot_id, args.payee, args.dry_run,
                   args.max_duration, args.expect_failure)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"  evidence written to {args.json_output}")
    raise SystemExit(0 if evidence["all_invariants_hold"] else 1)


if __name__ == "__main__":
    main()
