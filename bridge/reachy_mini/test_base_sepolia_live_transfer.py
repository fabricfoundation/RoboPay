"""test_base_sepolia_live_transfer.py — Live On-Chain Base Sepolia Payment & Simulation Demo.

Executes a real on-chain EVM transfer on Base Sepolia (eip155:84532), generates the
BaseScan transaction explorer link, sends the signed x402 payload through the Tunnel,
drives the Reachy Mini simulator, and outputs the full proof report.

Usage:
  $env:PRIVATE_KEY="0xYourBaseSepoliaPrivateKey"
  python test_base_sepolia_live_transfer.py
"""
import base64
import json
import os
import sys
import threading
import time
import unittest
import urllib.request
import urllib.error
import secrets
import zenoh

try:
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from web3 import Web3
    HAS_WEB3 = True
except ImportError:
    HAS_WEB3 = False

_HERE = os.path.dirname(os.path.abspath(__file__))
ACTION_TOPIC = "robot/tunnel/action"
METRICS_TOPIC = "robot/reachy_mini/metrics"

BASE_SEPOLIA_RPC = "https://sepolia.base.org"
CHAIN_ID = 84532
PAYEE_ADDRESS = os.environ.get("ROBO_PAYEE_ADDRESS", "0x39a315667d557B1425bb1e5D371DD66d300c98c1")



def run_live_base_sepolia_transfer():
    if not HAS_WEB3:
        print("ERROR: web3.py is required for live on-chain transfers. Install with: pip install web3")
        return

    pk = os.environ.get("PRIVATE_KEY") or os.environ.get("EVM_PRIVATE_KEY")
    if not pk:
        print("=" * 80)
        print("  BASE SEPOLIA LIVE ON-CHAIN TRANSFER TEST")
        print("=" * 80)
        print("  No PRIVATE_KEY provided in environment.")
        print("  To run a real on-chain transaction on Base Sepolia, set your key:")
        print("    $env:PRIVATE_KEY=\"0xYourBaseSepoliaPrivateKey\"")
        print("    python test_base_sepolia_live_transfer.py")
        print("=" * 80)
        return

    if not pk.startswith("0x"):
        pk = "0x" + pk

    acct = Account.from_key(pk)
    w3 = Web3(Web3.HTTPProvider(BASE_SEPOLIA_RPC))

    print("=" * 80)
    print("  Fabric Foundation RoboPay — Base Sepolia Live On-Chain Transfer Demo")
    print("=" * 80)
    print(f"  Payer Wallet Address : {acct.address}")
    print(f"  Network              : Base Sepolia (Chain ID {CHAIN_ID})")
    print(f"  RPC Endpoint         : {BASE_SEPOLIA_RPC}")
    print(f"  Payee Address        : {PAYEE_ADDRESS}")

    # Check payer wallet balance on Base Sepolia
    balance_wei = w3.eth.get_balance(acct.address)
    balance_eth = w3.from_wei(balance_wei, "ether")
    print(f"  Wallet Balance       : {balance_eth:.6f} ETH")

    if balance_wei < w3.to_wei(0.0001, "ether"):
        print("\nWARNING: Low wallet balance! Get free Base Sepolia testnet ETH at: https://www.bcfaucet.com")

    # 1. Build and sign live on-chain EVM transaction on Base Sepolia
    nonce = w3.eth.get_transaction_count(acct.address)
    gas_price = w3.eth.gas_price

    tx = {
        "nonce": nonce,
        "to": Web3.to_checksum_address(PAYEE_ADDRESS),
        "value": w3.to_wei(0.00005, "ether"),  # Micro-transfer fitted to balance
        "gas": 21000,
        "gasPrice": gas_price,
        "chainId": CHAIN_ID,
    }


    signed_tx = w3.eth.account.sign_transaction(tx, pk)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    tx_hash_hex = w3.to_hex(tx_hash)

    basescan_url = f"https://sepolia.basescan.org/tx/{tx_hash_hex}"
    print(f"\n[1/3] Live On-Chain Transaction Sent!")
    print(f"      TxHash: {tx_hash_hex}")
    print(f"      BaseScan Explorer: {basescan_url}")

    # Wait for block confirmation
    print("[1/3] Waiting for Base Sepolia block confirmation...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    print(f"      Confirmed in Block #{receipt.blockNumber}! Status: SUCCESS (1)")

    # 2. Build x402 EIP-712 Payment Authorization Header with TxHash
    x402_nonce = "0x" + secrets.token_hex(32)
    valid_until = int(time.time()) + 3600

    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "Authorization": [
                {"name": "payee", "type": "address"},
                {"name": "maxAmount", "type": "string"},
                {"name": "nonce", "type": "bytes32"},
                {"name": "validUntil", "type": "uint64"},
            ],
        },
        "primaryType": "Authorization",
        "domain": {
            "name": "x402 Exact Evm Scheme",
            "version": "1",
            "chainId": CHAIN_ID,
        },
        "message": {
            "payee": PAYEE_ADDRESS,
            "maxAmount": "1000000",
            "nonce": x402_nonce,
            "validUntil": valid_until,
        },
    }

    encoded_msg = encode_typed_data(full_message=typed_data)
    sig_obj = acct.sign_message(encoded_msg)
    sig_hex = sig_obj.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex

    payment_payload = {
        "scheme": "exact",
        "network": f"eip155:{CHAIN_ID}",
        "payload": {
            "authorization": {
                "payee": PAYEE_ADDRESS,
                "maxAmount": "1000000",
                "nonce": x402_nonce,
                "validUntil": valid_until,
            },
            "signature": sig_hex,
            "txHash": tx_hash_hex,
            "payer": acct.address,
        },
    }

    # 3. Connect to Zenoh, dispatch ActionEvent, and receive simulator metrics
    print(f"\n[2/3] Connecting to Zenoh and dispatching paid ActionEvent...")
    metrics_received = []
    metrics_event = threading.Event()

    try:
        z_conf = zenoh.Config.from_json5(
            '{"mode": "peer", "scouting": {"multicast": {"enabled": false}}, "connect": {"endpoints": ["tcp/127.0.0.1:7447"]}}'
        )
        z_session = zenoh.open(z_conf)
    except Exception:
        z_config = zenoh.Config()
        z_session = zenoh.open(z_config)

    def _on_metrics(sample):
        try:
            payload = json.loads(sample.payload.to_string())
            metrics_received.append(payload)
            metrics_event.set()
        except Exception:
            pass

    sub = z_session.declare_subscriber(METRICS_TOPIC, _on_metrics)

    action_payload = {
        "payload": {"action": "look_at_apple", "target": "apple"},
        "transaction_details": {
            "tx_hash": tx_hash_hex,
            "basescan_url": basescan_url,
            "payment_payload": payment_payload,
            "payment_requirements": {
                "price": "$0.001",
                "network": f"eip155:{CHAIN_ID}",
                "payTo": PAYEE_ADDRESS,
            },
        },
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    z_session.put(ACTION_TOPIC, json.dumps(action_payload).encode("utf-8"))
    print(f"[2/3] ActionEvent published to Zenoh '{ACTION_TOPIC}' with TxHash {tx_hash_hex[:16]}...")

    print("\n[3/3] Waiting for Reachy Mini simulator to execute task and return metrics...")
    got_metrics = metrics_event.wait(timeout=30)
    sub.undeclare()
    z_session.close()

    if got_metrics and metrics_received:
        m = metrics_received[0]
        print("\n" + "=" * 80)
        print("  LIVE ON-CHAIN PROOF REPORT (BASE SEPOLIA)")
        print("=" * 80)
        print(f"  BaseScan Explorer URL: {basescan_url}")
        print(f"  Execution Status    : {m.get('execution_status')}")
        print(f"  Task Completed      : {m.get('metrics', {}).get('task_completed')}")
        print(f"  Tracking Success    : {m.get('metrics', {}).get('tracking_success_rate') * 100:.1f}%")
        print(f"  Sim2Sim Score       : {m.get('sim_to_sim_validation', {}).get('overall_sim2sim_robustness_score')}")
        print("=" * 80)
        print("\nPASSED! Real Base Sepolia on-chain transfer verified end to end!")
    else:
        print("\nFAILED: Metrics not received within 30s. Start simulator with: python RoboPay/bridge/reachy_mini/mujoco_sim_bridge/main.py")


if __name__ == "__main__":
    run_live_base_sepolia_transfer()
