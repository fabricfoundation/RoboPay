#!/usr/bin/env python3
"""
Optional Base Sepolia Settlement Module (EIP-3009 TransferWithAuthorization)

Provides on-chain settlement for Base Sepolia testnet USDC
(0x036CbD53842c5426634e7929541eC2318f3dCF7e) using EIP-3009. This is an
OPTIONAL module enabled only when BASE_SEPOLIA_RPC_URL and PRIVATE_KEY are set;
otherwise the payment gate stays on the local facilitator ledger.

EIP-3009 (TransferWithAuthorization) for USDC on Base Sepolia — from Circle's
documented signing flow (developers.circle.com, chainId 84532):

  domain:
    name:            "USDC"
    version:         "2"
    chainId:         84532
    verifyingContract: 0x036CbD53842c5426634e7929541eC2318f3dCF7e

  TransferWithAuthorization(address from,address to,uint256 value,
                            uint256 validAfter,uint256 validBefore,bytes32 nonce)

  contract call:
    transferWithAuthorization(address from, address to, uint256 value,
      uint256 validAfter, uint256 validBefore, bytes32 nonce,
      uint8 v, bytes32 r, bytes32 s)

  nonce: a random 32-byte value chosen by the PAYER (not the contract's
  sequential EIP-2612 nonces()); it is per-authorization and bound to the
  payer's address in the contract's authorizationState mapping.

The signature is 65 bytes, ordered r (32) || s (32) || v (1).

The module exposes an OFFLINE correctness proof (build_auth_digest /
verify_authorization) that runs without an RPC and is exercised on CI, so the
EIP-712 domain, typehash and ABI are verifiable without broadcasting anything.
The live on-chain call runs only when configured.

Security:
- Private key is NEVER logged, committed, or printed.
- Settlement executes only for results with status "success" (no-settle rule).
"""

import os
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any

try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None

try:
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from eth_utils import to_checksum_address as _to_checksum_address
    ETH_ACCOUNT_AVAILABLE = True
except ImportError:
    ETH_ACCOUNT_AVAILABLE = False
    Account = None
    _to_checksum_address = None


def checksum_address(address: str) -> str:
    """EIP-55 checksum; returns the input unchanged when no helper exists."""
    if _to_checksum_address is not None:
        return _to_checksum_address(address)
    return address

# ---------------------------------------------------------------------------
# keccak-256 (Ethereum flavour, NOT sha3-256). web3 is preferred; fall back to
# PyCryptodome's original-Keccak implementation when web3 is absent so the
# offline proof can still run without the full web3 dependency tree.
# ---------------------------------------------------------------------------

def keccak256(data: bytes) -> bytes:
    if WEB3_AVAILABLE:
        return bytes(Web3.keccak(data))
    try:
        from Crypto.Hash import keccak as pycryptodome_keccak
        h = pycryptodome_keccak.new(digest_bits=256)
        h.update(data)
        return h.digest()
    except ImportError:
        raise RuntimeError(
            "keccak-256 requires either web3.py or pycryptodome")


def keccak_text(text: str) -> bytes:
    return keccak256(text.encode("utf-8"))


logger = logging.getLogger(__name__)

# Base Sepolia USDC (testnet) — Circle-documented deployment for chainId 84532
DEFAULT_USDC_CONTRACT = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
DEFAULT_CHAIN_ID = 84532
DEFAULT_RPC_URL = "https://sepolia.base.org"

# EIP-712 domain used by this USDC deployment (Circle's Base Sepolia flow)
DEFAULT_DOMAIN_NAME = "USDC"
DEFAULT_DOMAIN_VERSION = "2"

TRANSFER_WITH_AUTHORIZATION_STRUCT = (
    "TransferWithAuthorization(address from,address to,uint256 value,"
    "uint256 validAfter,uint256 validBefore,bytes32 nonce)")
EIP712_DOMAIN_STRUCT = (
    "EIP712Domain(string name,string version,uint256 chainId,"
    "address verifyingContract)")

# Canonical typehashes (EIP-3009 / EIP-712) — asserted by test_settlement.py
TRANSFER_WITH_AUTHORIZATION_TYPEHASH = keccak_text(
    TRANSFER_WITH_AUTHORIZATION_STRUCT).hex()
EIP712_DOMAIN_TYPEHASH = keccak_text(EIP712_DOMAIN_STRUCT).hex()

# EIP-3009 TransferWithAuthorization ABI (minimal) — v/r/s variant only
USDC_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "from", "type": "address"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "uint256", "name": "validAfter", "type": "uint256"},
            {"internalType": "uint256", "name": "validBefore", "type": "uint256"},
            {"internalType": "bytes32", "name": "nonce", "type": "bytes32"},
            {"internalType": "uint8", "name": "v", "type": "uint8"},
            {"internalType": "bytes32", "name": "r", "type": "bytes32"},
            {"internalType": "bytes32", "name": "s", "type": "bytes32"}
        ],
        "name": "transferWithAuthorization",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "authorizer", "type": "address"},
            {"internalType": "bytes32", "name": "nonce", "type": "bytes32"}
        ],
        "name": "authorizationState",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
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
        "name": "version",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
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
    }
]


def build_eip712_domain(chain_id: int, verifying_contract: str,
                        name: str = DEFAULT_DOMAIN_NAME,
                        version: str = DEFAULT_DOMAIN_VERSION) -> Dict[str, Any]:
    """The EIP-712 domain object for this USDC deployment."""
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
                {"name": "nonce", "type": "bytes32"}
            ]
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": name,
            "version": version,
            "chainId": chain_id,
            "verifyingContract": verifying_contract
        },
        "message": {}
    }


def domain_separator(chain_id: int, verifying_contract: str,
                     name: str = DEFAULT_DOMAIN_NAME,
                     version: str = DEFAULT_DOMAIN_VERSION) -> str:
    """EIP-712 domain separator bytes32 (hex)."""
    address_bytes = bytes.fromhex(
        checksum_address(verifying_contract)[2:].rjust(64, "0"))
    enc = (bytes.fromhex(EIP712_DOMAIN_TYPEHASH)
           + keccak_text(name)
           + keccak_text(version)
           + (chain_id).to_bytes(32, "big")
           + address_bytes)
    return keccak256(enc).hex()


def build_auth_digest(auth: Dict[str, Any], chain_id: int,
                      verifying_contract: str,
                      name: str = DEFAULT_DOMAIN_NAME,
                      version: str = DEFAULT_DOMAIN_VERSION) -> str:
    """The EIP-712 digest (bytes32, hex) for a TransferWithAuthorization.

    Pure-function proof of the signing scheme: the same payload + domain must
    always produce the same digest, and any deviation in the domain fields
    (e.g. name "USD Coin" vs "USDC") changes the digest — which is exactly
    why the domain constants matter.
    """
    message = {
        "from": checksum_address(auth["from"]),
        "to": checksum_address(auth["to"]),
        "value": int(auth["value"]),
        "validAfter": int(auth["validAfter"]),
        "validBefore": int(auth["validBefore"]),
        "nonce": to_bytes32(auth["nonce"]),
    }
    full = build_eip712_domain(chain_id, verifying_contract, name, version)
    full["message"] = message
    if ETH_ACCOUNT_AVAILABLE:
        encoded = encode_typed_data(full_message=full)
        return keccak256(b"\x19\x01" + encoded.header + encoded.body).hex()
    # web3-less structural digest (used by tests to prove field sensitivity)
    struct_hash = keccak256(
        bytes.fromhex(TRANSFER_WITH_AUTHORIZATION_TYPEHASH)
        + abi_encode_address(message["from"])
        + abi_encode_address(message["to"])
        + int(message["value"]).to_bytes(32, "big")
        + int(message["validAfter"]).to_bytes(32, "big")
        + int(message["validBefore"]).to_bytes(32, "big")
        + bytes.fromhex(to_bytes32(message["nonce"])[2:])
    )
    return keccak256(b"\x19\x01"
                     + bytes.fromhex(domain_separator(
                         chain_id, verifying_contract, name, version))
                     + struct_hash).hex()


def abi_encode_address(address: str) -> bytes:
    return bytes.fromhex(address.replace("0x", "")[:40].rjust(64, "0"))


def to_bytes32(value: Any) -> str:
    """Normalize a nonce to a 32-byte hex string (accepts hex or int)."""
    if isinstance(value, int):
        return f"0x{value.to_bytes(32, 'big').hex()}"
    v = str(value)
    if not v.startswith("0x"):
        v = "0x" + v
    raw = bytes.fromhex(v[2:])
    if len(raw) > 32:
        raise ValueError("EIP-3009 nonce must fit in 32 bytes")
    return "0x" + raw.rjust(32, b"\x00").hex()


def split_signature(signature: str):
    """Split a 65-byte EIP-3009 signature into (v, r, s)."""
    sig = signature.replace("0x", "")
    if len(sig) != 130:
        raise ValueError(
            f"EIP-3009 signature must be 65 bytes, got {len(sig) // 2}")
    r = "0x" + sig[0:64]
    s = "0x" + sig[64:128]
    v = int(sig[128:130], 16)
    if v in (0, 1):
        v += 27
    return v, r, s


def verify_authorization(auth: Dict[str, Any], chain_id: int,
                         verifying_contract: str,
                         name: str = DEFAULT_DOMAIN_NAME,
                         version: str = DEFAULT_DOMAIN_VERSION) -> bool:
    """Offline EIP-3009 signature verification (recover signer == from).

    Requires eth_account; returns False when unavailable so an unverifiable
    payload is never trusted for a live transfer.
    """
    if not ETH_ACCOUNT_AVAILABLE:
        return False
    try:
        message = {
            "from": checksum_address(auth["from"]),
            "to": checksum_address(auth["to"]),
            "value": int(auth["value"]),
            "validAfter": int(auth["validAfter"]),
            "validBefore": int(auth["validBefore"]),
            "nonce": to_bytes32(auth["nonce"]),
        }
        full = build_eip712_domain(chain_id, verifying_contract, name, version)
        full["message"] = message
        encoded = encode_typed_data(full_message=full)
        recovered = Account.recover_message(encoded, signature=auth["signature"])
        return recovered.lower() == message["from"].lower()
    except Exception:
        return False


@dataclass
class SettlementConfig:
    """Configuration for on-chain settlement."""
    rpc_url: str
    private_key: str
    usdc_contract: str
    chain_id: int
    payee_address: str
    domain_name: str = DEFAULT_DOMAIN_NAME
    domain_version: str = DEFAULT_DOMAIN_VERSION
    facilitator_url: str = ""

    @classmethod
    def from_env(cls) -> Optional["SettlementConfig"]:
        """Load config from environment variables. Returns None if not configured."""
        private_key = os.getenv("PRIVATE_KEY")
        if not private_key:
            return None
        rpc_url = os.getenv("BASE_SEPOLIA_RPC_URL", DEFAULT_RPC_URL)
        usdc_contract = os.getenv("USDC_CONTRACT", DEFAULT_USDC_CONTRACT)
        chain_id = int(os.getenv("BASE_SEPOLIA_CHAIN_ID", str(DEFAULT_CHAIN_ID)))
        payee_address = os.getenv("PAYEE_ADDRESS")
        if not payee_address and WEB3_AVAILABLE:
            payee_address = Account.from_key(private_key).address
        return cls(
            rpc_url=rpc_url,
            private_key=private_key,
            usdc_contract=usdc_contract,
            chain_id=chain_id,
            payee_address=payee_address or "",
            domain_name=os.getenv("USDC_DOMAIN_NAME", DEFAULT_DOMAIN_NAME),
            domain_version=os.getenv("USDC_DOMAIN_VERSION", DEFAULT_DOMAIN_VERSION),
            facilitator_url=os.getenv("FACILITATOR_URL", ""),
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
    authorization_verified: bool = False


class BaseSepoliaSettler:
    """Executes on-chain settlement on Base Sepolia via EIP-3009.

    Mirrors the tunnel's x402 middleware settlement flow (payer signs an
    EIP-3009 authorization; the operator's relay verifies it and submits the
    transferWithAuthorization transaction, moving USDC from the payer to the
    operator). Only called for results with status "success".
    """

    def __init__(self, config: SettlementConfig):
        if not config.is_configured():
            raise RuntimeError("BaseSepoliaSettler requires web3.py and valid config")

        self.config = config
        self.w3 = Web3(Web3.HTTPProvider(
            config.rpc_url,
            request_kwargs={"headers": {"User-Agent": "robopay-settlement/1.0"}}))
        self.account = Account.from_key(config.private_key)
        self.usdc = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.usdc_contract),
            abi=USDC_ABI
        )
        self.payee = Web3.to_checksum_address(config.payee_address)

        logger.info(f"BaseSepoliaSettler initialized for payee: {self.payee}")

    # -- offline verification -------------------------------------------
    def verify_payer_signature(self, auth: Dict[str, Any]) -> bool:
        """Recover the signer of the EIP-3009 authorization and compare to 'from'."""
        return verify_authorization(
            auth, self.config.chain_id, self.config.usdc_contract,
            self.config.domain_name, self.config.domain_version)

    def verify_with_facilitator(self, payment_payload: Dict[str, Any]) -> bool:
        """Optional external facilitator verification (only when configured)."""
        url = self.config.facilitator_url.strip()
        if not url:
            return True  # not configured -> nothing to check, proceed
        try:
            import requests
            resp = requests.post(f"{url.rstrip('/')}/verify",
                                 json=payment_payload, timeout=10)
            if resp.status_code == 200:
                return bool(resp.json().get("isValid", False))
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("facilitator verification failed: %s", exc)
            return False

    # -- settlement ------------------------------------------------------
    def settle(self, payment_payload: Dict[str, Any],
               amount_usdc: str) -> SettlementReceipt:
        """Submit the payer's EIP-3009 authorization to the USDC contract."""
        try:
            auth = (payment_payload.get("authorization")
                    or payment_payload.get("payment", {}).get("authorization"))
            if not auth:
                return SettlementReceipt(
                    success=False, error="missing EIP-3009 authorization")

            required = ("from", "to", "value", "validAfter", "validBefore",
                        "nonce", "signature")
            missing = [k for k in required if auth.get(k) in (None, "")]
            if missing:
                return SettlementReceipt(
                    success=False,
                    error=f"incomplete authorization fields: {missing}")

            if not self.verify_payer_signature(auth):
                return SettlementReceipt(
                    success=False,
                    error="EIP-3009 signature did not recover to 'from'")

            if not self.verify_with_facilitator(payment_payload):
                return SettlementReceipt(
                    success=False, error="facilitator verification failed")

            amount_raw = int(float(amount_usdc) * 1_000_000)  # 6 decimals
            from_addr = Web3.to_checksum_address(auth["from"])
            to_addr = Web3.to_checksum_address(auth["to"])
            value = int(auth.get("value", amount_raw))
            valid_after = int(auth["validAfter"])
            valid_before = int(auth["validBefore"])
            nonce = to_bytes32(auth["nonce"])
            v, r, s = split_signature(auth["signature"])

            tx = self.usdc.functions.transferWithAuthorization(
                from_addr, to_addr, value, valid_after, valid_before,
                bytes.fromhex(nonce[2:]), v, r, s
            ).build_transaction({
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
                "gas": 250000,
                "gasPrice": self.w3.eth.gas_price,
                "chainId": self.config.chain_id,
            })

            signed = self.w3.eth.account.sign_transaction(
                tx, self.config.private_key)
            # web3.py v7 renamed rawTransaction -> raw_transaction; keep a
            # fallback so the module works on both API generations.
            raw = getattr(signed, "raw_transaction", None) or getattr(
                signed, "rawTransaction", None)
            tx_hash = self.w3.eth.send_raw_transaction(raw)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash,
                                                               timeout=120)

            if receipt.status == 1:
                # The authorizationState write may lag a public RPC node by a
                # moment; retry a few times so the recorded verdict reflects
                # the confirmed on-chain state rather than a stale read.
                used = False
                for _ in range(5):
                    try:
                        used = bool(self.usdc.functions.authorizationState(
                            from_addr, nonce).call())
                    except Exception:  # noqa: BLE001
                        used = False
                    if used:
                        break
                    time.sleep(1.5)
                logger.info(
                    f"Settlement successful: {tx_hash.hex()} "
                    f"(authorizationState used: {used})")
                block_number = getattr(
                    receipt, "block_number", None) or receipt.blockNumber
                gas_used = getattr(receipt, "gas_used", None) or receipt.gasUsed
                return SettlementReceipt(
                    success=True,
                    tx_hash=tx_hash.hex(),
                    block_number=block_number,
                    gas_used=gas_used,
                    facilitator_verified=bool(self.config.facilitator_url),
                    authorization_verified=used,
                )
            return SettlementReceipt(
                success=False,
                error=f"transaction reverted: {tx_hash.hex()}")

        except Exception as exc:  # noqa: BLE001
            logger.error("settlement failed: %s", exc)
            return SettlementReceipt(success=False, error=str(exc))

    def get_balance(self) -> int:
        """USDC balance of the payee (base units)."""
        return self.usdc.functions.balanceOf(self.payee).call()

    def get_decimals(self) -> int:
        return self.usdc.functions.decimals().call()


def get_settler() -> Optional[BaseSepoliaSettler]:
    """Factory: build the settler if configured, else None."""
    config = SettlementConfig.from_env()
    if config and config.is_configured():
        try:
            return BaseSepoliaSettler(config)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to initialize BaseSepoliaSettler: %s", exc)
            return None
    return None


# Integration with payment_gate.py
def settle_if_success(result_status: str, payment_payload: Dict[str, Any],
                      amount_usdc: str) -> Optional[SettlementReceipt]:
    """Settle only on SUCCESS results (no-settle-on-failure rule)."""
    if result_status != "success":
        logger.info(
            f"skipping settlement: result status is '{result_status}'")
        return None

    settler = get_settler()
    if not settler:
        logger.info(
            "Base Sepolia settlement not configured (env vars missing or "
            "web3 unavailable) -> local facilitator ledger")
        return None

    logger.info("executing on-chain settlement on Base Sepolia...")
    receipt = settler.settle(payment_payload, amount_usdc)
    if receipt.success:
        logger.info(f"on-chain settlement successful: {receipt.tx_hash}")
    else:
        logger.error(f"on-chain settlement failed: {receipt.error}")
    return receipt


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    config = SettlementConfig.from_env()
    if config and config.is_configured():
        print("Base Sepolia settlement configured")
        print(f"  payee: {config.payee_address}")
        print(f"  rpc:   {config.rpc_url}")
        print(f"  usdc:  {config.usdc_contract}")
        print(f"  chain: {config.chain_id}")
        settler = BaseSepoliaSettler(config)
        print(f"  balance: {settler.get_balance() / 1_000_000:.6f} USDC")
    else:
        print("Base Sepolia settlement NOT configured")
        print("  set BASE_SEPOLIA_RPC_URL, PRIVATE_KEY, PAYEE_ADDRESS to enable")
        sys.exit(1)
