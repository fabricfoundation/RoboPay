"""EIP-3009 settlement on Base Sepolia (USDC).

Provides real on-chain settlement via transferWithAuthorization.
Writes evidence artifacts for bounty verification.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path


LOGGER = logging.getLogger("robopay.atlas.settlement")

from .task import BASE_SEPOLIA_CHAIN_ID, USDC_BASE_SEPOLIA  # noqa: F401


@dataclass(frozen=True)
class SettlementResult:
    success: bool
    tx_hash: str = ""
    block_number: int = 0
    payer: str = ""
    payee: str = ""
    amount: str = ""
    asset: str = ""
    network: str = ""
    chain_id: int = 0
    timestamp: float = field(default_factory=time.time)
    error: str = ""


class OnChainSettlement:
    """Settles x402 payments via EIP-3009 transferWithAuthorization on Base Sepolia.

    Requires WEB3_PROVIDER_URL env var and SETTLEMENT_PRIVATE_KEY env var.
    """

    def __init__(
        self,
        provider_url: str | None = None,
        private_key: str | None = None,
        usdc_address: str = USDC_BASE_SEPOLIA,
        chain_id: int = BASE_SEPOLIA_CHAIN_ID,
    ):
        self._provider_url = provider_url or os.environ.get("WEB3_PROVIDER_URL", "")
        self._private_key = private_key or os.environ.get("SETTLEMENT_PRIVATE_KEY", "")
        self._usdc_address = usdc_address
        self._chain_id = chain_id

    @property
    def available(self) -> bool:
        return bool(self._provider_url and self._private_key)

    def settle(
        self,
        payer: str,
        payee: str,
        amount: str,
        valid_after: int = 0,
        valid_before: int | None = None,
        nonce: str | None = None,
    ) -> SettlementResult:
        if not self.available:
            return SettlementResult(
                success=False,
                error="Settlement not configured. Set WEB3_PROVIDER_URL and SETTLEMENT_PRIVATE_KEY.",
            )

        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(self._provider_url))
            if not w3.is_connected():
                return SettlementResult(success=False, error="Cannot connect to provider.")

            if valid_before is None:
                valid_before = int(time.time()) + 300

            if nonce is None:
                nonce = "0x" + os.urandom(32).hex()

            account = w3.eth.account.from_key(self._private_key)

            usdc_abi = [
                {
                    "name": "transferWithAuthorization",
                    "type": "function",
                    "inputs": [
                        {"name": "from", "type": "address"},
                        {"name": "to", "type": "address"},
                        {"name": "value", "type": "uint256"},
                        {"name": "validAfter", "type": "uint256"},
                        {"name": "validBefore", "type": "uint256"},
                        {"name": "nonce", "type": "bytes32"},
                    ],
                }
            ]

            usdc = w3.eth.contract(
                address=Web3.to_checksum_address(self._usdc_address),
                abi=usdc_abi,
            )

            value = int(float(amount) * 1_000_000)

            tx = usdc.functions.transferWithAuthorization(
                Web3.to_checksum_address(payer),
                Web3.to_checksum_address(payee),
                value,
                valid_after,
                valid_before,
                bytes.fromhex(nonce[2:]) if isinstance(nonce, str) else nonce,
            ).build_transaction({
                "from": account.address,
                "chainId": self._chain_id,
                "nonce": w3.eth.get_transaction_count(account.address),
                "gas": 200000,
                "maxFeePerGas": w3.to_wei("1", "gwei"),
                "maxPriorityFeePerGas": w3.to_wei("0.1", "gwei"),
            })

            signed = w3.eth.account.sign_transaction(tx, self._private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            return SettlementResult(
                success=receipt.status == 1,
                tx_hash=tx_hash.hex(),
                block_number=receipt.blockNumber,
                payer=payer,
                payee=payee,
                amount=amount,
                asset="USDC",
                network=f"eip155:{self._chain_id}",
                chain_id=self._chain_id,
            )

        except Exception as error:
            LOGGER.exception("Settlement failed")
            return SettlementResult(success=False, error=str(error))


def write_evidence(
    result: SettlementResult,
    evidence_dir: Path,
    action_id: str = "",
    policy_id: str = "",
) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "action_id": action_id,
        "policy_id": policy_id,
        "settlement": {
            "success": result.success,
            "tx_hash": result.tx_hash,
            "block_number": result.block_number,
            "payer": result.payer,
            "payee": result.payee,
            "amount": result.amount,
            "asset": result.asset,
            "network": result.network,
            "chain_id": result.chain_id,
            "timestamp": result.timestamp,
            "error": result.error,
        },
    }
    path = evidence_dir / "settlement-evidence.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return path
