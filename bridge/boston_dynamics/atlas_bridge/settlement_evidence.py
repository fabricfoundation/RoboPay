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

#: The settlement this profile's evidence refers to.
SETTLEMENT_TX = "0x5b04259e0d9cfe319a6ffec3d7f6b9118b70e09ae4a832625bed5ecd48326b6e"
#: The faucet request that funded the payer wallet beforehand.
FUNDING_TX = "0xb37252fda0bc30de9ce98bd1b306c131eda11a4b3fabd9ae11d487d8773fdbbb"


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


def verify_settlement(tx_hash: str = SETTLEMENT_TX) -> dict:
    """Read the settlement transaction back from chain and decode its transfer."""
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

    return {
        "hash": tx_hash,
        "succeeded": receipt["status"] == "0x1",
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
    settlement = verify_settlement(SETTLEMENT_TX)
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
    print(f"  succeeded   : {settlement['succeeded']}")
    print(f"  block       : {settlement['block_number']}")
    print(f"  transfer    : {transfer['amount']} {transfer['token']}")
    print(f"  from        : {transfer['from']}")
    print(f"  to          : {transfer['to']}")
    print(f"  explorer    : {settlement['explorer']}")
    raise SystemExit(0 if settlement["succeeded"] else 1)


if __name__ == "__main__":
    main()
