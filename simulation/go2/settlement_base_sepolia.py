#!/usr/bin/env python3
"""
Optional Base Sepolia Settlement Module

Provides on-chain settlement capability for Base Sepolia testnet using EIP-3009
(EIP-3009: TransferWithAuthorization). This is an OPTIONAL module that can be
enabled when BASE_SEPOLIA_RPC_URL and PRIVATE_KEY environment variables are set.

If not configured, the payment gate falls back to the local facilitator (default).

This module mirrors the settlement logic in the Go tunnel's x402 middleware
(x402-foundation/x402/go) but implemented in Python for CI/CD compatibility.

Usage:
    export BASE_SEPOLIA_RPC_URL="https://sepolia.base.org"
    export PRIVATE_KEY="0x..."  # payee private key (NOT commited!)
    export USDC_CONTRACT="0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Base Sepolia USDC
    export FACILITATOR_URL="https://x402.org/facilitator"  # optional, for verification

The settlement follows EIP-3009 (TransferWithAuthorization):
1. Payer signs EIP-3009 authorization off-chain
2. Facilitator verifies signature
3. Facilitator calls transferWithAuthorization on USDC contract
4. Settlement receipt returned with txHash

Security:
- Private key NEVER logged, committed, or printed
- Loaded only from environment variable
- Settlement only executed on SUCCESS status
- No-settle-on-failure enforced at payment_gate level
"""

import os
import json
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

try:
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None
    Account = None

logger = logging.getLogger(__name__)

# Base Sepolia USDC (testnet)
DEFAULT_USDC_CONTRACT = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
DEFAULT_CHAIN_ID = 84532
DEFAULT_RPC_URL = "https://sepolia.base.org"

# EIP-3009 TransferWithAuthorization ABI (minimal)
USDC_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "from", "type": "address"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "uint256", "name": "validAfter", "type": "uint256"},
            {"internalType": "uint256", "name": "validBefore", "type": "uint256"},
            {"internalType": "bytes", "name": "authorization", "type": "bytes"}
        ],
        "name": "transferWithAuthorization",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "address", "name": "spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "account", "type": "address"}
        ],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "name",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "nonces",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "version",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# EIP-712 Domain for USDC (EIP-3009)
def build_eip712_domain(chain_id: int, verifying_contract: str, name: str = "USD Coin", version: str = "2") -> Dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"}
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "uint256"}
            ]
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": name,
            "version": version,
            "chainId": chain_id,
            "verifyingContract": verifying_contract
        },
        "message": {}  # filled per-transaction
    }


@dataclass
class SettlementConfig:
    """Configuration for on-chain settlement."""
    rpc_url: str
    private_key: str
    usdc_contract: str
    chain_id: int
    facilitator_url: str
    payee_address: str

    @classmethod
    def from_env(cls) -> Optional["SettlementConfig"]:
        """Load config from environment variables. Returns None if not configured."""
        rpc_url = os.getenv("BASE_SEPOLIA_RPC_URL", DEFAULT_RPC_URL)
        private_key = os.getenv("PRIVATE_KEY")
        usdc_contract = os.getenv("USDC_CONTRACT", DEFAULT_USDC_CONTRACT)
        chain_id = int(os.getenv("BASE_SEPOLIA_CHAIN_ID", str(DEFAULT_CHAIN_ID)))
        facilitator_url = os.getenv("FACILITATOR_URL", "https://x402.org/facilitator")
        payee_address = os.getenv("PAYEE_ADDRESS")

        if not private_key:
            return None

        # Derive payee address from private key if not provided
        if not payee_address and WEB3_AVAILABLE:
            acct = Account.from_key(private_key)
            payee_address = acct.address

        return cls(
            rpc_url=rpc_url,
            private_key=private_key,
            usdc_contract=usdc_contract,
            chain_id=chain_id,
            facilitator_url=facilitator_url,
            payee_address=payee_address or ""
        )

    def is_configured(self) -> bool:
        return bool(self.private_key) and WEB3_AVAILABLE


@dataclass
class SettlementReceipt:
    """Result of a settlement attempt."""
    success: bool
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    gas_used: Optional[int] = None
    error: Optional[str] = None
    facilitator_verified: bool = False


class BaseSepoliaSettler:
    """
    Handles on-chain settlement on Base Sepolia using EIP-3009.

    This mirrors the Go tunnel's x402 middleware settlement logic but in Python.
    Only executes settlement when payment_gate confirms SUCCESS status.
    """

    def __init__(self, config: SettlementConfig):
        if not config.is_configured():
            raise RuntimeError("BaseSepoliaSettler requires web3.py and valid config")

        self.config = config
        self.w3 = Web3(Web3.HTTPProvider(config.rpc_url))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self.account = Account.from_key(config.private_key)
        self.usdc = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.usdc_contract),
            abi=USDC_ABI
        )
        self.payee = Web3.to_checksum_address(config.payee_address)

        logger.info(f"BaseSepoliaSettler initialized for payee: {self.payee}")

    def verify_payment_with_facilitator(self, payment_payload: Dict[str, Any]) -> bool:
        """
        Verify payment with x402 facilitator before settlement.
        Mirrors the Go tunnel's facilitator verification.
        """
        try:
            import requests
            resp = requests.post(
                f"{self.config.facilitator_url}/verify",
                json=payment_payload,
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("isValid", False)
            return False
        except Exception as e:
            logger.warning(f"Facilitator verification failed: {e}")
            return False

    def settle(self, payment_payload: Dict[str, Any], amount_usdc: str) -> SettlementReceipt:
        """
        Execute on-chain settlement via EIP-3009 TransferWithAuthorization.

        Args:
            payment_payload: The x402 payment payload from the payer (includes authorization)
            amount_usdc: Amount in USDC (e.g., "0.002")

        Returns:
            SettlementReceipt with tx_hash on success
        """
        try:
            # 1. Verify with facilitator first (mirrors Go tunnel)
            if not self.verify_payment_with_facilitator(payment_payload):
                return SettlementReceipt(
                    success=False,
                    error="Facilitator verification failed"
                )

            # 2. Extract EIP-3009 authorization from payment payload
            # Expected format: {"authorization": {...}, "x402Version": 1, ...}
            auth = payment_payload.get("authorization") or payment_payload.get("payment", {}).get("authorization")
            if not auth:
                return SettlementReceipt(success=False, error="Missing EIP-3009 authorization")

            # 3. Build transaction
            amount_raw = int(float(amount_usdc) * 1_000_000)  # USDC has 6 decimals
            nonce = self.usdc.functions.nonces(self.payee).call()

            # Extract authorization components (EIP-3009)
            # auth should contain: from, to, value, validAfter, validBefore, nonce, signature
            from_addr = Web3.to_checksum_address(auth.get("from", ""))
            to_addr = Web3.to_checksum_address(auth.get("to", ""))
            value = int(auth.get("value", amount_raw))
            valid_after = int(auth.get("validAfter", 0))
            valid_before = int(auth.get("validBefore", 2**64 - 1))
            auth_nonce = int(auth.get("nonce", nonce))
            signature = auth.get("signature")

            if not all([from_addr, to_addr, value, signature]):
                return SettlementReceipt(success=False, error="Incomplete authorization fields")

            # 4. Call transferWithAuthorization
            tx = self.usdc.functions.transferWithAuthorization(
                from_addr,
                to_addr,
                value,
                valid_after,
                valid_before,
                auth_nonce,
                bytes.fromhex(signature.replace("0x", ""))
            ).build_transaction({
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
                "gas": 200000,
                "gasPrice": self.w3.eth.gas_price,
                "chainId": self.config.chain_id
            })

            # 5. Sign and send
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.config.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if tx_receipt.status == 1:
                logger.info(f"Settlement successful: {tx_hash.hex()}")
                return SettlementReceipt(
                    success=True,
                    tx_hash=tx_hash.hex(),
                    block_number=tx_receipt.blockNumber,
                    gas_used=tx_receipt.gasUsed,
                    facilitator_verified=True
                )
            else:
                return SettlementReceipt(
                    success=False,
                    error=f"Transaction reverted: {tx_hash.hex()}"
                )

        except Exception as e:
            logger.error(f"Settlement failed: {e}")
            return SettlementReceipt(success=False, error=str(e))

    def get_balance(self) -> int:
        """Get USDC balance of payee."""
        return self.usdc.functions.balanceOf(self.payee).call()

    def get_allowance(self, spender: str) -> int:
        """Get USDC allowance for spender."""
        return self.usdc.functions.allowance(self.payee, Web3.to_checksum_address(spender)).call()


def get_settler() -> Optional[BaseSepoliaSettler]:
    """Factory function to create settler if configured, else None."""
    config = SettlementConfig.from_env()
    if config and config.is_configured():
        try:
            return BaseSepoliaSettler(config)
        except Exception as e:
            logger.warning(f"Failed to initialize BaseSepoliaSettler: {e}")
            return None
    return None


# Integration with payment_gate.py
def settle_if_success(result_status: str, payment_payload: Dict[str, Any],
                       amount_usdc: str) -> Optional[SettlementReceipt]:
    """
    Convenience function to settle only on SUCCESS status.

    This enforces the wiki requirement: "If the robot action succeeds, the relay may
    settle the payment. If the robot action fails, times out, or returns an error,
    the relay must not settle the payment."

    Args:
        result_status: "success" or "error" from ActionResult
        payment_payload: x402 payment payload from payer
        amount_usdc: Amount in USDC (e.g., "0.002")

    Returns:
        SettlementReceipt if settled, None if not settled or not configured
    """
    if result_status != "success":
        logger.info(f"Skipping settlement: result status is '{result_status}' (not success)")
        return None

    settler = get_settler()
    if not settler:
        logger.info("Base Sepolia settlement not configured (env vars missing or web3 unavailable). Using local facilitator.")
        return None

    logger.info("Executing on-chain settlement on Base Sepolia...")
    receipt = settler.settle(payment_payload, amount_usdc)

    if receipt.success:
        logger.info(f"On-chain settlement successful: {receipt.tx_hash}")
    else:
        logger.error(f"On-chain settlement failed: {receipt.error}")

    return receipt


if __name__ == "__main__":
    # Quick test: check if configured
    import sys
    logging.basicConfig(level=logging.INFO)

    config = SettlementConfig.from_env()
    if config and config.is_configured():
        print("✅ Base Sepolia settlement configured")
        print(f"   Payee: {config.payee_address}")
        print(f"   RPC: {config.rpc_url}")
        print(f"   USDC: {config.usdc_contract}")
        print(f"   Chain: {config.chain_id}")

        settler = BaseSepoliaSettler(config)
        bal = settler.get_balance()
        print(f"   USDC Balance: {bal / 1_000_000:.6f} USDC")
    else:
        print("⚠️ Base Sepolia settlement NOT configured")
        print("   Set BASE_SEPOLIA_RPC_URL, PRIVATE_KEY, PAYEE_ADDRESS to enable")
        sys.exit(1)