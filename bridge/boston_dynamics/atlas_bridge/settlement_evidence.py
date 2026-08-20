"""Re-verify the on-chain settlement directly against Base Sepolia.

The bridge's x402 gate decides *whether* an action may settle; this module
records that a settlement of the gated skill really happened on chain. It reads
the transaction back from a public RPC endpoint and rebuilds the evidence from
what the chain returns, so the artefact cannot drift from reality — run it again
and it either reproduces or fails.

Nothing here holds a key. Settlement is executed by the operator's own wallet;
this is the read-only receipt.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from .task import (
    BASE_SEPOLIA_CHAIN_ID as CHAIN_ID,
    PAYMENT_NETWORK as NETWORK,
    USDC_BASE_SEPOLIA as USDC_ADDRESS,
    USDC_DECIMALS,
)

RPC_URL = "https://sepolia.base.org"
EXPLORER = "https://sepolia.basescan.org"

#: keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

#: keccak256("AuthorizationUsed(address,bytes32)")
AUTHORIZATION_USED_TOPIC = (
    "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"
)

#: The paid run whose settlement this verifies. Read from the artifact rather
#: than pinned here, so the check follows the evidence instead of drifting from
#: it — an earlier revision verified a 1.0 USDC transfer that had nothing to do
#: with any action while the profile's real settlement was 0.001 USDC.
PAID_RUN_ARTIFACT = Path("docs/evidence/real-paid-run.json")
#: The faucet request that funded the first test wallet.
FUNDING_TX = "0xb37252fda0bc30de9ce98bd1b306c131eda11a4b3fabd9ae11d487d8773fdbbb"


def settlement_under_test() -> tuple[str, str]:
    """The transaction and action id to verify, taken from the paid-run artifact."""
    if not PAID_RUN_ARTIFACT.is_file():
        raise SystemExit(f"{PAID_RUN_ARTIFACT} is missing; nothing to verify against")
    artifact = json.loads(PAID_RUN_ARTIFACT.read_text(encoding="utf-8"))
    on_chain = artifact.get("on_chain") or {}
    tx_hash = (on_chain.get("explorer") or "").rsplit("/", 1)[-1]
    action_id = artifact.get("action_id", "")
    if not tx_hash or not action_id:
        raise SystemExit(f"{PAID_RUN_ARTIFACT} records no settlement to verify")
    return tx_hash, action_id


def expectations() -> dict:
    """What the profile says a settlement of this skill must look like.

    Taken from the profile and the paid run rather than restated here, so a
    price or payee changed in one place cannot leave this check agreeing with
    a stale copy of itself.
    """
    from .task import SKILL_PRICE_RAW

    artifact = json.loads(PAID_RUN_ARTIFACT.read_text(encoding="utf-8"))
    payment = artifact.get("payment") or {}
    return {
        "amount_raw": int(SKILL_PRICE_RAW),
        "asset": USDC_ADDRESS.lower(),
        "payer": str(payment.get("payer", "")).lower(),
        "payee": str(payment.get("payee", "")).lower(),
        "network": NETWORK,
    }


def _rpc(method: str, params: list) -> dict | None:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    request = urllib.request.Request(
        RPC_URL,
        data=payload.encode(),
        headers={
            "content-type": "application/json",
            # The public endpoint rejects requests without a user agent.
            "user-agent": "robopay-atlas-bridge/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read()).get("result")


def _address(topic: str) -> str:
    return "0x" + topic[-40:]


def verify_settlement(tx_hash: str = "", action_id: str = "") -> dict:
    """Read the settlement transaction back from chain and decode its transfer.

    When an ``action_id`` is given the authorization nonce is checked too: the
    profile derives it as ``keccak256(action_id)``, so a matching nonce in the
    token's ``AuthorizationUsed`` event is what makes this transfer the one that
    paid for that action rather than merely a transfer of the right size.
    """
    if not tx_hash:
        tx_hash, action_id = settlement_under_test()
    receipt = _rpc("eth_getTransactionReceipt", [tx_hash])
    if receipt is None:
        raise RuntimeError(f"Transaction {tx_hash} not found on Base Sepolia")

    transfers = [
        log
        for log in receipt["logs"]
        if log["topics"] and log["topics"][0].lower() == TRANSFER_TOPIC
        and log["address"].lower() == USDC_ADDRESS.lower()
    ]
    if not transfers:
        raise RuntimeError(f"{tx_hash} carries no USDC Transfer event")
    transfer = transfers[0]
    raw = int(transfer["data"], 16)

    nonce = ""
    for log in receipt["logs"]:
        topics = log.get("topics") or []
        if topics and topics[0].lower() == AUTHORIZATION_USED_TOPIC and len(topics) >= 3:
            nonce = topics[2]

    expected_nonce = ""
    if action_id:
        from eth_utils import keccak

        expected_nonce = "0x" + keccak(text=action_id).hex()

    expected = expectations()
    mismatches = []
    if raw != expected["amount_raw"]:
        mismatches.append(
            f"amount {raw} is not the declared price {expected['amount_raw']}")
    if transfer["address"].lower() != expected["asset"]:
        mismatches.append(f"asset {transfer['address']} is not {USDC_ADDRESS}")
    if expected["payer"] and _address(transfer["topics"][1]).lower() != expected["payer"]:
        mismatches.append(
            f"payer {_address(transfer['topics'][1])} is not {expected['payer']}")
    if expected["payee"] and _address(transfer["topics"][2]).lower() != expected["payee"]:
        mismatches.append(
            f"payee {_address(transfer['topics'][2])} is not {expected['payee']}")

    return {
        "hash": tx_hash,
        "action_id": action_id,
        "expected": expected,
        "mismatches": mismatches,
        "matches_profile": not mismatches,
        "succeeded": receipt["status"] == "0x1",
        "authorization_nonce": nonce,
        "expected_nonce_from_action_id": expected_nonce,
        "nonce_binds_settlement_to_action": bool(nonce)
        and nonce.lower() == expected_nonce.lower(),
        "block_number": int(receipt["blockNumber"], 16),
        "gas_used": int(receipt["gasUsed"], 16),
        "contract": receipt["to"],
        "explorer": f"{EXPLORER}/tx/{tx_hash}",
        "transfer": {
            "event": "Transfer(address,address,uint256)",
            "token": "USDC",
            "token_contract": USDC_ADDRESS,
            "from": _address(transfer["topics"][1]),
            "to": _address(transfer["topics"][2]),
            "raw_amount": raw,
            "decimals": USDC_DECIMALS,
            "amount": raw / 10**USDC_DECIMALS,
        },
    }


def collect() -> dict:
    """Build the settlement evidence entirely from what the chain returns."""
    settlement = verify_settlement()
    funding_receipt = _rpc("eth_getTransactionReceipt", [FUNDING_TX])

    return {
        "evidence": "on_chain_settlement",
        "network": {"name": "Base Sepolia", "chain_id": CHAIN_ID, "caip2": NETWORK},
        "asset": {
            "symbol": "USDC",
            "contract": USDC_ADDRESS,
            "decimals": USDC_DECIMALS,
            "explorer": f"{EXPLORER}/token/{USDC_ADDRESS}",
        },
        "settlement_transaction": settlement,
        "funding_transaction": {
            "hash": FUNDING_TX,
            "succeeded": bool(funding_receipt) and funding_receipt["status"] == "0x1",
            "block_number": int(funding_receipt["blockNumber"], 16) if funding_receipt else None,
            "explorer": f"{EXPLORER}/tx/{FUNDING_TX}",
            "description": (
                "Coinbase Developer Platform faucet request that funded the payer "
                "wallet with testnet USDC before the settlement above."
            ),
        },
        "wallets": {
            "payer": {
                "address": settlement["transfer"]["from"],
                "explorer": f"{EXPLORER}/address/{settlement['transfer']['from']}",
            },
            "payee": {
                "address": settlement["transfer"]["to"],
                "explorer": f"{EXPLORER}/address/{settlement['transfer']['to']}",
            },
        },
        "notes": [
            "Testnet only. Base Sepolia USDC has no monetary value.",
            "This artefact deliberately records no balances: balances change after "
            "the fact, while the transaction and its Transfer event do not.",
            "The payer wallet is a disposable test wallet and is treated as "
            "compromised; no key material is stored in this repository.",
        ],
        "reproduce": (
            "python -m bridge.boston_dynamics.atlas_bridge.settlement_evidence"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the on-chain settlement.")
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("docs/evidence/onchain-settlement.json"),
    )
    args = parser.parse_args()

    evidence = collect()
    settlement = evidence["settlement_transaction"]
    transfer = settlement["transfer"]

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    print(f"settlement tx : {settlement['hash']}")
    print(f"  for action  : {settlement['action_id']}")
    print(f"  succeeded   : {settlement['succeeded']}")
    print(f"  bound to it : {settlement['nonce_binds_settlement_to_action']}"
          f"  (nonce = keccak256(action_id))")
    print(f"  matches     : {settlement['matches_profile']}"
          f"  (amount, asset, payer, payee against the profile)")
    for mismatch in settlement["mismatches"]:
        print(f"    !! {mismatch}")
    print(f"  block       : {settlement['block_number']}")
    print(f"  transfer    : {transfer['amount']} {transfer['token']}")
    print(f"  from        : {transfer['from']}")
    print(f"  to          : {transfer['to']}")
    print(f"  explorer    : {settlement['explorer']}")
    # A settlement that is not bound to the action it paid for proves the
    # asset moved, not that this action was the reason.
    raise SystemExit(
        0 if settlement["succeeded"]
        and settlement["nonce_binds_settlement_to_action"]
        and settlement["matches_profile"] else 1
    )


if __name__ == "__main__":
    main()
