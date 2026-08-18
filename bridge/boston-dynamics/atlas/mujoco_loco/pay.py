#!/usr/bin/env python3
"""pay.py -- mint a REAL x402 payment receipt for boston-dynamics-atlas on Base Sepolia.

This is the ONE honest step the sandbox cannot do for you: broadcast a genuine
USDC transferWithAuthorization (EIP-3009) from your funded payer wallet to the
official RoboPay payee, then write docs/evidence/x402-evidence.json with the
real txHash so acceptance criterion #7 (independently verifiable on-chain
receipt) is satisfied. Reusing another robot's txHash is replay fraud and will
be rejected -- each robot needs its OWN real tx.

Requirements (install in your venv):  web3
Environment:
  BOSTON_DYNAMICS_ATLAS_PRIVATE_KEY   payer private key (funded with Base Sepolia
                                    USDC + a little ETH for gas)
  BASE_SEPOLIA_RPC       optional, default https://sepolia.base.org

Run:  python pay.py
"""
from __future__ import annotations
import os, sys, json, time, uuid
from pathlib import Path

ROBOT_ID = "boston-dynamics-atlas"
PAYEE = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
AMOUNT_USDC = 0.10
RESOURCE = "robopay://boston-dynamics-atlas/{skill}"

HERE = Path(__file__).resolve().parent
EV = HERE / "docs" / "evidence" / "x402-evidence.json"

USDC_ABI = [{
    "inputs": [
        {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}],
    "name": "transferWithAuthorization",
    "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable",
    "type": "function"}]


def main():
    from web3 import Web3
    pk = os.environ.get("BOSTON_DYNAMICS_ATLAS_PRIVATE_KEY")
    if not pk:
        sys.exit("set BOSTON_DYNAMICS_ATLAS_PRIVATE_KEY (funded Base Sepolia wallet)")
    rpc = os.environ.get("BASE_SEPOLIA_RPC", "https://sepolia.base.org")
    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        sys.exit(f"cannot reach Base Sepolia RPC: {rpc}")
    acct = w3.eth.account.from_key(pk)
    payer = acct.address
    value = int(AMOUNT_USDC * 10 ** 6)
    nonce = os.urandom(32)
    valid_after = 0
    valid_before = int(time.time()) + 3600
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC), abi=USDC_ABI)
    # EIP-3009 domain separator for USDC (name "USD Coin", version "2")
    domain = {
        "name": "USD Coin", "version": "2", "chainId": 84532,
        "verifyingContract": Web3.to_checksum_address(USDC),
    }
    types = {"TransferWithAuthorization": [
        {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}]}
    msg = {"from": payer, "to": Web3.to_checksum_address(PAYEE), "value": value,
           "validAfter": valid_after, "validBefore": valid_before, "nonce": nonce}
    signed = acct.sign_typed_data(domain, types, msg)
    v, r, s = signed["v"], signed["r"], signed["s"]
    tx = usdc.functions.transferWithAuthorization(
        payer, Web3.to_checksum_address(PAYEE), value, valid_after,
        valid_before, nonce, v, r, s).build_transaction({
        "from": payer, "nonce": w3.eth.get_transaction_count(payer),
        "gas": 120000, "gasPrice": w3.eth.gas_price})
    tx_hash = w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if rcpt.status != 1:
        sys.exit("tx reverted")
    action_id = str(uuid.uuid4())
    out = {
        "status": "SETTLED_ON_CHAIN",
        "payer": payer, "payee": Web3.to_checksum_address(PAYEE),
        "usdc": USDC, "network": "base-sepolia", "asset": "USDC",
        "amount_usdc": AMOUNT_USDC, "resource": RESOURCE.format(skill="move_forward"),
        "settledAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "txs": ["0x" + tx_hash.hex()],
        "actionId": action_id,
        "verification": "facilitator",
        "note": "Real EIP-3009 USDC transferWithAuthorization on Base Sepolia; "
                "independently verifiable via Basescan (tx hash above).",
    }
    EV.parent.mkdir(parents=True, exist_ok=True)
    EV.write_text(json.dumps(out, indent=2))
    print("WROTE", EV)
    print("tx:", out["txs"][0])


if __name__ == "__main__":
    main()
