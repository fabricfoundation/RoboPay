"""
Live Base Sepolia payment test for the Deep Robotics M20 Pro Tier 1 skill.

Signs a real x402 payment with the configured wallet, sends it to a
running tunnel/cmd/localserver instance, and polls the status endpoint
until the action settles (or fails). Reads secrets from environment
variables only -- never hardcode a private key here.

Usage:
    export $(cat .env.local | grep -v '^#' | xargs)
    python pay_m20_pro.py
"""
import json
import os
import sys
import time

import requests
from eth_account import Account

from x402 import x402ClientSync
from x402.mechanisms.evm.exact import ExactEvmScheme
from x402.mechanisms.evm import EthAccountSigner
from x402.http.clients.requests import x402_requests

PRIVATE_KEY = os.environ["PAYER_PRIVATE_KEY"]
LOCALSERVER_PORT = os.environ.get("LOCALSERVER_PORT", "8402")
BASE_URL = f"http://127.0.0.1:{LOCALSERVER_PORT}"

def main():
    account = Account.from_key(PRIVATE_KEY)
    print(f"Paying from wallet: {account.address}")

    signer = EthAccountSigner(account)
    client = x402ClientSync()
    client.register("eip155:*", ExactEvmScheme(signer=signer))

    session = x402_requests(client)

    action_payload = {
        "action": "m20_pro_obstacle_navigation",
        "params": {"target_xy": [8.0, 0.0], "max_episode_steps": 50000},
    }

    print("Sending POST /action (will sign payment automatically on 402)...")
    resp = session.post(f"{BASE_URL}/action", json=action_payload, timeout=30)
    print(f"Response: {resp.status_code}")
    print(resp.text)

    if resp.status_code != 202:
        print("FAILED: expected 202 Accepted", file=sys.stderr)
        sys.exit(1)

    body = resp.json()
    action_id = body["actionId"]
    status_url = f"{BASE_URL}{body['status_url']}"
    print(f"\nactionId={action_id}")
    print(f"Polling {status_url} ...")

    for i in range(30):
        time.sleep(1)
        status_resp = requests.get(status_url, timeout=10)
        status = status_resp.json()
        print(f"  [{i}s] state={status.get('state')} settled={status.get('settled')}")
        if status.get("state") in ("succeeded", "failed", "settlement_failed"):
            print("\nFinal status:")
            print(json.dumps(status, indent=2))
            if status.get("state") == "succeeded" and status.get("settled"):
                print("\nSUCCESS: payment settled.")
                sys.exit(0)
            else:
                print("\nDID NOT SETTLE (this may be correct if the sim failed).")
                sys.exit(0 if status.get("state") == "failed" else 1)

    print("TIMEOUT waiting for terminal status", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
