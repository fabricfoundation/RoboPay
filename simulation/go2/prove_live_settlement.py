"""Live Base Sepolia settlement proof (EIP-3009 transferWithAuthorization).

Runs ONLY when a funded key is provided; otherwise it prints NOT CONFIGURED
and exits 0 so CI never fails on this optional step.  Two phases:

  SETTLEMENT_PROOF_MODE=success  (default)
    Builds a real EIP-3009 TransferWithAuthorization for the configured
    payer/payee, verifies the signature OFFLINE first, then submits the
    transferWithAuthorization transaction to the USDC contract on Base
    Sepolia, waits for the receipt, and confirms the on-chain
    authorizationState was consumed.  Writes settlement-proof.json with
    chainId / token / payer / payee / amount / nonce / txHash / block /
    gasUsed / settled=true.

  SETTLEMENT_PROOF_MODE=failure
    Proves the no-settle-on-failure rule in the OPPOSITE direction with the
    SAME live configuration: a non-success result must NOT broadcast anything.
    settle_if_success short-circuits before any write; the payer nonce read
    before and after is unchanged.  Writes settlement-proof-failure.json with
    settled=false, txHash=null and the nonce evidence.

Environment (required for success mode):
  PRIVATE_KEY                      payer (and relay) account - MUST hold USDC
                                   and a little ETH for gas on Base Sepolia
  PAYEE_ADDRESS                    where the USDC goes (or SETTLEMENT_PROOF_PAYEE_KEY)
  BASE_SEPOLIA_RPC_URL             default https://sepolia.base.org
  USDC_CONTRACT                    default Base Sepolia USDC
  AMOUNT_USDC                      default "0.005"
  SETTLEMENT_PROOF_MODE            "success" | "failure" | "both" (default both)

The private key is only ever used in memory by this script and is never
printed, logged or written to any file.
"""

import json
import logging
import os
import pathlib
import secrets
import sys

HERE = pathlib.Path(__file__).resolve().parent
DOCS = HERE.parent / "docs"
sys.path.insert(0, str(HERE))

from settlement_base_sepolia import (  # noqa: E402
    SettlementConfig,
    build_auth_digest,
    build_eip712_domain,
    settle_if_success,
    to_bytes32,
    verify_authorization,
    DEFAULT_CHAIN_ID,
    DEFAULT_USDC_CONTRACT,
    DEFAULT_RPC_URL,
)

logging.basicConfig(level=logging.INFO)


def _require_settler():
    try:
        from web3 import Web3  # noqa: F401
    except ImportError:
        print("web3.py not installed - cannot run live proof")
        sys.exit(2)
    config = SettlementConfig.from_env()
    if not config or not config.is_configured():
        print("NOT CONFIGURED")
        print("  set BASE_SEPOLIA_RPC_URL, PRIVATE_KEY, PAYEE_ADDRESS to enable")
        return None, None
    from settlement_base_sepolia import BaseSepoliaSettler
    settler = BaseSepoliaSettler(config)
    return settler, config


def _resolve_payee(config):
    payee = getattr(config, "payee_address", "") or ""
    if payee:
        return payee
    alt_key = os.environ.get("SETTLEMENT_PROOF_PAYEE_KEY", "")
    if alt_key:
        from eth_account import Account
        return Account.from_key(alt_key).address
    raise RuntimeError(
        "PAYEE_ADDRESS is required for the live success proof")


def _build_auth(payer, payee, amount_usdc, chain_id, contract):
    from eth_account import Account
    value = int(float(amount_usdc) * 1_000_000)  # 6 USDC decimals
    nonce = "0x" + secrets.token_hex(32)
    msg = {
        "from": payer,
        "to": payee,
        "value": value,
        "validAfter": 0,
        "validBefore": 2 ** 64 - 1,
        "nonce": to_bytes32(nonce),
    }
    typed = build_eip712_domain(chain_id, contract)
    typed["message"] = msg
    from eth_account.messages import encode_typed_data
    enc = encode_typed_data(full_message=typed)
    signature = Account.from_key(os.environ["PRIVATE_KEY"]).sign_message(
        enc).signature.hex()
    return {
        "from": payer,
        "to": payee,
        "value": value,
        "validAfter": 0,
        "validBefore": 2 ** 64 - 1,
        "nonce": to_bytes32(nonce),
        "signature": "0x" + signature,
    }


def proof_success(settler, config):
    amount = os.environ.get("AMOUNT_USDC", "0.005")
    payee = _resolve_payee(config)
    payer = settler.account.address

    auth = _build_auth(payer, payee, amount, config.chain_id,
                       config.usdc_contract)
    digest = build_auth_digest(auth, config.chain_id, config.usdc_contract)
    offline_ok = verify_authorization(auth, config.chain_id,
                                      config.usdc_contract)
    print(f"payer:  {payer}")
    print(f"payee:  {payee}")
    print(f"amount: {amount} USDC (value={auth['value']})")
    print(f"nonce:  {auth['nonce']}")
    print(f"digest: 0x{digest}")
    print(f"offline signature verification: {offline_ok}")

    balance_before = settler.get_balance()
    print(f"payee USDC balance before: {balance_before / 1_000_000:.6f}")

    payload = {"authorization": auth}
    receipt = settler.settle(payload, amount)

    evidence = {
        "phase": "success",
        "chainId": config.chain_id,
        "token": config.usdc_contract,
        "payer": payer,
        "payee": payee,
        "amountUSDC": amount,
        "value": auth["value"],
        "nonce": auth["nonce"],
        "digest": "0x" + digest,
        "offlineSignatureVerified": offline_ok,
        "settled": receipt.success,
        "txHash": receipt.tx_hash,
        "blockNumber": receipt.block_number,
        "gasUsed": receipt.gas_used,
        "authorizationStateConsumed": receipt.authorization_verified,
        "explorer": (f"https://sepolia.basescan.org/tx/{receipt.tx_hash}"
                     if receipt.tx_hash else None),
        "error": receipt.error,
        "balanceBeforeUSDC": balance_before / 1_000_000,
    }
    if receipt.success:
        # Public RPC nodes can lag the balance write by a moment; re-read a
        # few times so the reported delta is the confirmed on-chain value.
        import time
        balance_after = settler.get_balance()
        for _ in range(5):
            if balance_after > balance_before:
                break
            time.sleep(1.5)
            balance_after = settler.get_balance()
        evidence["balanceAfterUSDC"] = balance_after / 1_000_000
        evidence["deltaUSDC"] = (balance_after - balance_before) / 1_000_000
    _write("settlement-proof.json", evidence)
    return receipt.success


def proof_failure(settler, config):
    w3 = settler.w3
    nonce_before = w3.eth.get_transaction_count(settler.account.address)
    result = settle_if_success("timeout", {"authorization": {"placeholder": 1}},
                               "0.005")
    nonce_after = w3.eth.get_transaction_count(settler.account.address)
    evidence = {
        "phase": "failure",
        "chainId": config.chain_id,
        "resultStatus": "timeout",
        "settlementReturned": None if result is None else result.success,
        "settled": False,
        "txHash": None,
        "relayNonceBefore": nonce_before,
        "relayNonceAfter": nonce_after,
        "nonceUnchanged": nonce_after == nonce_before,
        "explainer": "settle_if_success short-circuits on any non-success "
                     "result before a transaction can be built or broadcast; "
                     "the unchanged relay nonce is the on-chain evidence",
        "error": None,
    }
    _write("settlement-proof-failure.json", evidence)
    return evidence["nonceUnchanged"]


def _write(name, evidence):
    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / name
    path.write_text(json.dumps(evidence, indent=2))
    print(f"proof written to {path}")


def main():
    mode = os.environ.get("SETTLEMENT_PROOF_MODE", "both").lower()
    settler, config = _require_settler()
    if settler is None:
        return

    ok = True
    if mode in ("success", "both"):
        ok = proof_success(settler, config) and ok
    if mode in ("failure", "both"):
        ok = proof_failure(settler, config) and ok

    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
