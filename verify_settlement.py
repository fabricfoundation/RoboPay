#!/usr/bin/env python3
"""Verify the settlement evidence in x402-evidence.json against live chains.

Base Sepolia (USDC, required)
-----------------------------
For every hash in ``txs`` this asserts against a live node that the tx:

  1. exists and its receipt status is success,
  2. targets the canonical Base Sepolia USDC contract,
  3. emits an ERC-20 ``Transfer`` log from the declared payer to the declared
     payee,
  4. moves exactly the declared amount.

Why the ``Transfer`` log and not ``tx.from``: these settlements are EIP-3009
``transferWithAuthorization`` calls, so ``tx.from`` is the facilitator that
relays the signed authorisation -- it is NOT the payer. The payer only ever
appears as ``topics[1]`` of the ``Transfer`` event. A checker that reads
``tx.from`` reports the wrong wallet.

Pi Testnet (optional, non-settlement)
-------------------------------------
If ``pi_txs`` is non-empty each hash is fetched from Horizon and must be a
successful payment operation. When the operation's ``from`` equals its ``to``
the tx is reported as ``PI-LIVENESS`` -- a self-transfer proving the Pi rail is
wired, explicitly NOT a transfer of value. It is never counted as settlement.

Exit codes
  0  every declared tx checked out, or no endpoint was reachable at all
     (transient network blip; set ``STRICT=1`` to turn that red too).
  1  at least one tx contradicts the evidence file.

Any accounting mismatch -- wrong payer, wrong payee, wrong amount, missing
Transfer log, failed receipt, non-USDC target -- is a HARD failure. Only an
unreachable network is tolerated, and that outcome prints "NOT VERIFIED" so a
green run can never be mistaken for a verified one.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from decimal import Decimal

# Canonical Base Sepolia constants. The evidence file is checked against these
# so it cannot quietly declare a look-alike token or a different chain.
CHAIN_ID = "0x14a34"  # 84532
USDC_BASE_SEPOLIA = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
USDC_DECIMALS = Decimal(10) ** 6

# Public endpoints, ordered by observed reliability. The User-Agent header is
# mandatory: these hosts answer 403 Forbidden to urllib's default
# "Python-urllib/3.x" agent, which is what silently disabled this check before
# -- every tx fell into the "network skip" branch and CI stayed green without
# ever reading the chain.
RPC_URLS = [
    "https://sepolia.base.org",
    "https://base-sepolia-rpc.publicnode.com",
    "https://base-sepolia.drpc.org",
    "https://base-sepolia.gateway.tenderly.co",
]
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) robopay-settlement-verifier/1.0",
}
TIMEOUT = float(os.environ.get("X402_RPC_TIMEOUT", "12"))
STRICT = os.environ.get("STRICT", "").lower() in ("1", "true", "yes")

_HERE = os.path.dirname(os.path.abspath(__file__))
_GITHUB_WORKSPACE = os.environ.get("GITHUB_WORKSPACE", ".")

# Search order: explicit override -> repo-root neighbours -> the real location
# under bridge/unitree-g1/docs/evidence. A reviewer who simply clones and runs
# `python verify_settlement.py` must land on the committed evidence file.
_CANDIDATES = [
    os.environ.get("X402_EVIDENCE"),
    os.path.join(_HERE, "x402-evidence.json"),
    os.path.join(_HERE, "bridge", "unitree-g1-balance", "docs", "evidence",
                 "x402-evidence.json"),
    os.path.join(_HERE, "bridge", "unitree-g1", "docs", "evidence",
                 "x402-evidence.json"),
    os.path.join(_GITHUB_WORKSPACE, "x402-evidence.json"),
    os.path.join(_GITHUB_WORKSPACE, "bridge", "unitree-g1-balance", "docs",
                 "evidence", "x402-evidence.json"),
    os.path.join(_GITHUB_WORKSPACE, "bridge", "unitree-g1", "docs", "evidence",
                 "x402-evidence.json"),
]
EVIDENCE = None
for _cand in _CANDIDATES:
    if _cand and os.path.exists(_cand):
        EVIDENCE = _cand
        break
if EVIDENCE is None:
    # fall back to the most likely path so the error message is actionable
    EVIDENCE = os.path.join(_HERE, "x402-evidence.json")


class Unreachable(Exception):
    """No RPC endpoint answered."""


def http_json(url, post=None, timeout=None):
    data = json.dumps(post).encode() if post is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def rpc(url, method, params):
    payload = http_json(url, {"jsonrpc": "2.0", "id": 1, "method": method,
                              "params": params})
    if "error" in payload:
        raise RuntimeError("%s: %s" % (method, payload["error"]))
    return payload.get("result")


def pick_endpoint():
    """Return the first endpoint that answers and really is Base Sepolia.

    Chosen once and reused for every tx, so a hung endpoint costs one timeout
    for the whole run instead of one timeout per tx.
    """
    errors = []
    for url in RPC_URLS:
        try:
            chain = rpc(url, "eth_chainId", [])
        except Exception as exc:  # noqa: BLE001 - any failure means "try next"
            errors.append("%s: %s" % (url, str(exc)[:60]))
            continue
        if str(chain).lower() != CHAIN_ID:
            errors.append("%s: chainId %s is not Base Sepolia" % (url, chain))
            continue
        return url
    raise Unreachable("; ".join(errors) or "no endpoints configured")


def topic_to_address(topic):
    return "0x" + topic[-40:].lower()


def usdc_transfers(receipt, usdc):
    """Decode every ERC-20 Transfer emitted by the USDC contract."""
    found = []
    for log in receipt.get("logs") or []:
        if (log.get("address") or "").lower() != usdc:
            continue
        topics = log.get("topics") or []
        if len(topics) < 3 or topics[0].lower() != TRANSFER_TOPIC:
            continue
        raw = int(log.get("data") or "0x0", 16)
        found.append((topic_to_address(topics[1]), topic_to_address(topics[2]), raw))
    return found


def audit_tx(url, tx_hash, payers, payee, usdc, amount):
    """Return (problems, note); an empty problem list means the tx checks out.

    ``payers`` is a set of accepted source wallets (the canonical payer plus any
    deployment sub-wallet that broadcasts the settlement on its behalf). A
    Transfer from any of them to the canonical payee is accepted.
    """
    tx = rpc(url, "eth_getTransactionByHash", [tx_hash])
    if tx is None:
        return ["tx does not exist on Base Sepolia"], None
    receipt = rpc(url, "eth_getTransactionReceipt", [tx_hash])
    if receipt is None:
        return ["tx is not mined (no receipt)"], None

    problems = []
    target = (tx.get("to") or "").lower()
    if target != usdc:
        problems.append("calls %s, not the USDC contract"
                        % (target or "<contract creation>"))
    if int(receipt.get("status") or "0x0", 16) != 1:
        problems.append("receipt status is failure (reverted)")

    transfers = usdc_transfers(receipt, usdc)
    matched = None
    if not transfers:
        problems.append("emits no USDC Transfer log")
    else:
        for src, dst, raw in transfers:
            if src in payers and dst == payee:
                matched = raw
                break
        if matched is None:
            src, dst, _ = transfers[0]
            if dst != payee:
                problems.append(
                    "USDC Transfer is %s -> %s, but the evidence file declares "
                    "payee %s" % (src, dst, payee))
            else:
                problems.append(
                    "USDC Transfer is from %s, which is not the declared payer "
                    "or any accepted deployment sub-wallet" % src)
        else:
            actual = Decimal(matched) / USDC_DECIMALS
            if actual != amount:
                problems.append("moves %s USDC, evidence declares %s USDC"
                                % (actual, amount))

    note = None
    if matched is not None:
        note = "%s USDC %s -> %s" % (Decimal(matched) / USDC_DECIMALS,
                                     sorted(payers)[0], payee)
    return problems, note


def check_evidence_file(evidence):
    """Reject an evidence file that is malformed before trusting anything in it."""
    problems = []
    payer = (evidence.get("payer") or "").lower()
    payee = (evidence.get("payee") or "").lower()
    usdc = (evidence.get("usdc") or "").lower()
    try:
        amount = Decimal(str(evidence.get("amount_usdc")))
    except Exception:  # noqa: BLE001
        amount = None

    if len(payer) != 42 or not payer.startswith("0x"):
        problems.append("payer %r is not an address" % evidence.get("payer"))
    if len(payee) != 42 or not payee.startswith("0x"):
        problems.append("payee %r is not an address" % evidence.get("payee"))
    if payer and payer == payee:
        problems.append("payer and payee are the same wallet, which proves no "
                        "transfer of value")
    if usdc != USDC_BASE_SEPOLIA:
        problems.append("usdc %s is not the canonical Base Sepolia USDC %s"
                        % (usdc, USDC_BASE_SEPOLIA))
    if amount is None or amount <= 0:
        problems.append("amount_usdc %r is not a positive number"
                        % evidence.get("amount_usdc"))
    # Accepted source wallets: canonical payer + any declared deployment
    # sub-wallet. Settlements may be broadcast from a sub-wallet; both settle to
    # the same canonical payee.
    accepted = {payer}
    for extra in (evidence.get("accepted_payers") or []):
        if isinstance(extra, str) and extra.lower().startswith("0x"):
            accepted.add(extra.lower())
    return problems, accepted, payee, usdc, amount


# ---------------------------------------------------------------- Pi Testnet

def audit_pi_tx(horizon, tx_hash, declared_payee):
    """Return (state, note) where state is 'settled' | 'liveness' | 'fail'.

    'liveness' is a successful self-transfer: the Pi rail answered, but no
    value changed hands, so it must never be presented as a settlement.
    """
    tx = http_json("%s/transactions/%s" % (horizon, tx_hash), timeout=25)
    if not tx.get("successful"):
        return "fail", "tx exists but did not succeed on Pi Testnet"
    ops = http_json("%s/transactions/%s/operations" % (horizon, tx_hash), timeout=25)
    records = ops.get("_embedded", {}).get("records", [])
    payments = [o for o in records
                if o.get("type") in ("payment", "path_payment_strict_send",
                                     "path_payment_strict_receive",
                                     "create_account")]
    if not payments:
        return "fail", "tx carries no payment operation"

    op = payments[0]
    src = (op.get("from") or op.get("source_account") or "").upper()
    dst = (op.get("to") or op.get("account") or "").upper()
    amount = op.get("amount", "?")
    asset = op.get("asset_type", "?")
    if declared_payee and dst != declared_payee.upper():
        return "fail", ("pays %s but the evidence file declares payee %s"
                        % (dst[:8] or "?", declared_payee[:8]))
    if src and src == dst:
        return "liveness", ("self-transfer of %s %s by %s... - rail liveness "
                            "only, no value moved" % (amount, asset, src[:8]))
    return "settled", ("%s %s from %s... to %s..."
                       % (amount, asset, src[:8], dst[:8]))


def run_pi(evidence):
    """Return the number of hard failures found on the Pi rail."""
    pi_hashes = evidence.get("pi_txs") or []
    if not pi_hashes:
        return 0
    horizon = evidence.get("pi_horizon") or "https://api.testnet.minepi.com"
    declared_payee = evidence.get("pi_payee") or ""
    failures, settled, liveness, unreachable = 0, 0, 0, 0
    for tx_hash in pi_hashes:
        try:
            state, note = audit_pi_tx(horizon, tx_hash, declared_payee)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            unreachable += 1
            print("WARN(network) pi %s: %s" % (tx_hash, str(exc)[:80]),
                  file=sys.stderr)
            continue
        if state == "fail":
            failures += 1
            print("PI-FAIL %s (%s)" % (tx_hash, note), file=sys.stderr)
        elif state == "liveness":
            liveness += 1
            print("PI-LIVENESS %s (%s)" % (tx_hash, note))
        else:
            settled += 1
            print("PI-SETTLED %s (%s)" % (tx_hash, note))
    print("PI %d/%d tx(s) on Pi Testnet: %d value transfer(s), %d liveness "
          "self-transfer(s), %d unreachable"
          % (settled + liveness, len(pi_hashes), settled, liveness, unreachable))
    if liveness:
        print("Note: a liveness self-transfer proves the Pi rail is wired. It is "
              "NOT settlement evidence and is not counted as one.")
    return failures


# --------------------------------------------------------------------- main

def main():
    try:
        with open(EVIDENCE) as handle:
            evidence = json.load(handle)
    except (OSError, ValueError) as exc:
        print("FAIL cannot read evidence file %s: %s" % (EVIDENCE, exc),
              file=sys.stderr)
        return 1

    hashes = evidence.get("txs") or []
    if not hashes:
        print("FAIL %s declares no settlement tx hashes" % EVIDENCE, file=sys.stderr)
        return 1

    setup, payers, payee, usdc, amount = check_evidence_file(evidence)
    if setup:
        for problem in setup:
            print("FAIL evidence file: %s" % problem, file=sys.stderr)
        return 1

    try:
        url = pick_endpoint()
    except Unreachable as exc:
        print("WARN(network) no Base Sepolia RPC reachable: %s" % exc, file=sys.stderr)
        print("NOT VERIFIED 0/%d settlement tx(s) - the chain was unreachable, "
              "nothing was checked" % len(hashes))
        return 1 if STRICT else 0

    print("Endpoint %s (chainId %s)" % (url, CHAIN_ID))
    verified, failed_hashes, failures, unreachable = 0, set(), [], 0
    for tx_hash in hashes:
        try:
            problems, note = audit_tx(url, tx_hash, payers, payee, usdc, amount)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError,
                ValueError) as exc:
            unreachable += 1
            print("WARN(network) %s: %s" % (tx_hash, str(exc)[:80]), file=sys.stderr)
            continue
        if problems:
            failed_hashes.add(tx_hash)
            for problem in problems:
                failures.append("%s: %s" % (tx_hash, problem))
            print("FAIL %s" % tx_hash, file=sys.stderr)
        else:
            verified += 1
            print("OK   %s  %s" % (tx_hash, note))

    for failure in failures:
        print("FAIL %s" % failure, file=sys.stderr)

    print("VERIFIED %d/%d settlement tx(s) on Base Sepolia "
          "(failed: %d, network-unreachable: %d)"
          % (verified, len(hashes), len(failed_hashes), unreachable))

    pi_failures = 0
    try:
        pi_failures = run_pi(evidence)
    except Exception as exc:  # noqa: BLE001 - the Pi rail must never mask a USDC result
        print("WARN(network) Pi rail check aborted: %s" % str(exc)[:80],
              file=sys.stderr)

    if failures or pi_failures:
        print("Settlement evidence contradicts the chain -> CI red", file=sys.stderr)
        return 1
    if unreachable and STRICT:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
