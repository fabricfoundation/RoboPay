"""Pay for one live Spot obstacle-course action through the public Gateway.

This is an operator demo tool, not a CI test. It assumes the Zenoh router,
Spot bridge, and Tunnel are already running in separate terminals. The payer
key is read from ``PRIVATE_KEY`` by default. For an interactive operator demo,
``--prompt-for-private-key`` reads it directly from the terminal without
echoing, printing, or writing it to disk.

The tool first discovers the robot's registered skills and price through the
read-only Gateway route. It then obtains the x402 quote (network, asset and
payee) before signing payment.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import time
from typing import Any

import requests
from eth_account import Account
from x402 import x402ClientSync
from x402.http.clients import x402_requests
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner


NETWORK = "eip155:84532"
SKILL_ID = "navigate_obstacle_course"
FABRIC_API_BASE = os.environ.get(
    "FABRIC_API_BASE_URL", "https://api.fabric.foundation/api/core"
).rstrip("/")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing {name}. Set it in this shell; do not put it in a file.")
    return value


def _private_key(prompt: bool) -> str:
    if not prompt:
        return _required_env("PRIVATE_KEY")
    value = getpass.getpass("Test private key (input hidden): ")
    if not value:
        raise SystemExit("No private key entered.")
    return value


def _print_event(name: str, payload: Any) -> None:
    print(f"\n=== {name} ===")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _decode_payment_required(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    return json.loads(base64.b64decode(value).decode("utf-8"))


def _expect_json(response: requests.Response, label: str) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError as error:
        raise RuntimeError(f"{label} did not return JSON: HTTP {response.status_code}: {response.text}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Pay for one live Spot MuJoCo obstacle-course action.")
    parser.add_argument("--robot-id", default=os.environ.get("ROBOT_ID", "spot-mujoco-sim-01"))
    parser.add_argument("--duration", type=float, default=48.0)
    parser.add_argument("--speed-scale", type=float, default=1.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--prompt-for-private-key",
        action="store_true",
        help="Read the test private key from a hidden terminal prompt instead of PRIVATE_KEY.",
    )
    args = parser.parse_args()

    if not 5.0 <= args.duration <= 60.0:
        raise SystemExit("--duration must be between 5 and 60 seconds.")
    if not 0.25 <= args.speed_scale <= 1.0:
        raise SystemExit("--speed-scale must be between 0.25 and 1.0.")
    if args.poll_seconds <= 0 or args.timeout_seconds <= 0:
        raise SystemExit("--poll-seconds and --timeout-seconds must be greater than zero.")

    private_key = _private_key(args.prompt_for_private_key)
    payee = _required_env("ROBO_PAYEE_ADDRESS")
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    payer = Account.from_key(private_key)

    request_id = f"spot-obs-demo-{int(time.time())}"
    action_url = f"{FABRIC_API_BASE}/robots/{args.robot_id}/action"
    action_body = {
        "action": SKILL_ID,
        "robot_id": args.robot_id,
        "action_id": request_id,
        "idempotency_key": request_id,
        "params": {
            "maxDurationSec": args.duration,
            "side": "left",
            "speedScale": args.speed_scale,
        },
    }

    _print_event(
        "payer",
        {"address": payer.address, "network": NETWORK, "robot_id": args.robot_id, "request_id": request_id},
    )

    discovery_response = requests.get(
        f"{FABRIC_API_BASE}/robots/{args.robot_id}/skills", timeout=45
    )
    if discovery_response.status_code != 200:
        raise RuntimeError(
            "Robot skill discovery failed: "
            f"HTTP {discovery_response.status_code}: {discovery_response.text}"
        )
    discovery = _expect_json(discovery_response, "Robot skill discovery")
    selected = next(
        (item for item in discovery.get("skills", []) if item.get("skill_id") == SKILL_ID),
        None,
    )
    if selected is None or selected.get("price_usdc") != "0.001" or not selected.get("enabled"):
        raise RuntimeError(f"Published Spot skill is unavailable or has price drift: {discovery}")
    _print_event("robot and skill discovery", discovery)

    unpaid = requests.post(action_url, json=action_body, timeout=45)
    if unpaid.status_code != 402:
        raise RuntimeError(f"Expected an initial HTTP 402, got {unpaid.status_code}: {unpaid.text}")
    requirements = _decode_payment_required(
        unpaid.headers.get("PAYMENT-REQUIRED") or unpaid.headers.get("Payment-Required")
    )
    accepted = requirements.get("accepts", [{}])[0]
    if (
        accepted.get("payTo", "").lower() != payee.lower()
        or accepted.get("network") != NETWORK
        or accepted.get("amount") != "1000"
    ):
        raise RuntimeError(f"Unexpected x402 payment challenge: {accepted}")
    _print_event(
        "x402 quote before payment",
        {
            "http_status": unpaid.status_code,
            "network": accepted.get("network"),
            "pay_to": accepted.get("payTo"),
            "amount": accepted.get("amount"),
            "asset": accepted.get("asset"),
        },
    )

    client = x402ClientSync()
    register_exact_evm_client(client, EthAccountSigner(payer), networks=NETWORK)
    paid = x402_requests(client).post(action_url, json=action_body, timeout=120)
    if paid.status_code != 202:
        raise RuntimeError(f"Paid action failed: HTTP {paid.status_code}: {paid.text}")
    accepted_action = _expect_json(paid, "Paid action")
    if accepted_action.get("action_id") != request_id:
        raise RuntimeError(f"Accepted action ID does not match request: {accepted_action}")
    _print_event("paid action accepted", accepted_action)

    status_url = f"{action_url}/{request_id}/status"
    terminal = None
    deadline = time.monotonic() + args.timeout_seconds
    while time.monotonic() < deadline:
        response = requests.get(status_url, timeout=45)
        if response.status_code == 200:
            candidate = _expect_json(response, "Action status")
            _print_event("action status", candidate)
            if candidate.get("state") in {"succeeded", "failed", "timeout", "settlement_failed"}:
                terminal = candidate
                break
        time.sleep(args.poll_seconds)

    if terminal is None:
        raise RuntimeError("Action did not reach a terminal status before the timeout.")
    if terminal.get("state") != "succeeded" or not terminal.get("settled"):
        raise RuntimeError(f"Simulator or settlement did not succeed: {terminal}")

    settlement = terminal.get("settlement") or {}
    transaction = settlement.get("transaction") or settlement.get("txHash")
    if not transaction:
        raise RuntimeError(f"Successful terminal status lacks a settlement transaction: {terminal}")
    _print_event(
        "demo complete",
        {
            "simulator_status": terminal.get("state"),
            "settled": terminal.get("settled"),
            "transaction": transaction,
            "basescan": f"https://sepolia.basescan.org/tx/{transaction}",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
