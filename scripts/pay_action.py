#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["x402[requests,evm]"]
# ///
"""Pay for a robot action via x402, then send a JSON payload to it.

Flow
----
    this script ──HTTP POST──▶ fabric-foundation-api proxy ──WS tunnel──▶ robot-tunnel-client
                                  /api/core/robots/{id}/action                   POST /action  (x402-gated)

  1. The proxy forwards our request over the robot's WebSocket tunnel to the
     robot's local `POST /action` handler, which is x402 payment-protected.
  2. The first, unpaid request comes back as HTTP 402 with a PAYMENT-REQUIRED
     header describing price / network / payee.
  3. The x402 requests adapter (installed by `x402_requests`) signs a payment
     payload with our EVM private key and transparently retries the request
     with a PAYMENT-SIGNATURE header.
  4. The robot's facilitator verifies the payment, the handler runs, publishes
     the payload to Zenoh, and returns 200 {"status": "accepted"}.

The payment scheme/network is chosen by the server (advertised in the 402), so
we register the "eip155:*" wildcard and let the SDK match.

Usage
-----
    export EVM_PRIVATE_KEY=0x<key funded with the payment token>
    uv run scripts/pay_action.py \
        --api http://localhost:8080 \
        --robot test-robot \
        --payload '{"command":"move","args":{"x":1,"y":2}}'

If you are not using uv, install the deps first:
    pip install "x402[requests,evm]"
    python scripts/pay_action.py ...
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys

try:
    import requests
    from eth_account import Account
    from x402 import x402ClientSync
    from x402.http.clients import x402_requests
    from x402.mechanisms.evm.exact import ExactEvmScheme
    from x402.mechanisms.evm.signers import EthAccountSigner
except ImportError as exc:  # pragma: no cover - guidance only
    sys.exit(
        f'missing dependency ({exc}). Install with: pip install "x402[requests,evm]" '
        '(or run this file with `uv run`).'
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--api', default=os.getenv('API_BASE_URL', 'http://localhost:8080'),
                   help='Base URL of the fabric-foundation-api proxy')
    p.add_argument('--robot', default=os.getenv('ROBOT_ID', 'test-robot'),
                   help='Target robot ID connected to the proxy tunnel')
    p.add_argument('--path', default='/action',
                   help='Robot-local path to invoke (the x402-gated action route)')
    p.add_argument('--payload', default='{"command":"ping"}',
                   help='Inline JSON payload to send')
    p.add_argument('--payload-file', default=None,
                   help='Read the JSON payload from this file instead of --payload')
    p.add_argument('--key', default=os.getenv('EVM_PRIVATE_KEY'),
                   help='EVM private key (hex, 0x-optional). Prefer the EVM_PRIVATE_KEY env var')
    p.add_argument('--timeout', type=float, default=45.0,
                   help='Request timeout in seconds (must exceed the proxy 30s tunnel timeout)')
    return p.parse_args()


def resolve_payload(inline: str, file: str | None) -> bytes:
    if file:
        with open(file, 'rb') as fh:
            return fh.read()
    return inline.encode()


def decode_header_json(value: str) -> str:
    """Best-effort base64-decode an x402 header for readable printing."""
    try:
        return base64.b64decode(value).decode()
    except Exception:
        return value


def main() -> int:
    args = parse_args()

    if not args.key:
        sys.exit('missing EVM private key: set EVM_PRIVATE_KEY or pass --key')

    # Resolve payload and ensure it is valid JSON (the robot handler rejects non-JSON bodies).
    body = resolve_payload(args.payload, args.payload_file)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        sys.exit(f'payload is not valid JSON: {exc}')

    # 1. Signer from the private key (EIP-712 / EIP-3009 signing is offline; no RPC needed).
    account = Account.from_key(args.key)
    signer = EthAccountSigner(account)
    print(f'paying from address {account.address}')

    # 2. Sync x402 client with the EVM exact scheme registered for all EVM networks.
    #    The wildcard lets the server pick the network (e.g. eip155:84532).
    client = x402ClientSync()
    client.register('eip155:*', ExactEvmScheme(signer=signer))

    # 3. A requests.Session that handles 402 + payment retry automatically.
    session = x402_requests(client)

    # 4. Build the proxied URL: the proxy exposes robots under /api/core/robots/{id}/*path.
    url = f"{args.api.rstrip('/')}/api/core/robots/{args.robot}/{args.path.lstrip('/')}"

    print(f'POST {url} ({len(body)} byte payload)')
    try:
        resp = session.post(url, json=payload, timeout=args.timeout)
    except requests.RequestException as exc:
        sys.exit(f'request failed: {exc}')

    print('\n=== result ===')
    print(f'status: {resp.status_code} {resp.reason}')
    settle = resp.headers.get('PAYMENT-RESPONSE')
    if settle:
        print(f'settlement: {decode_header_json(settle)}')
    print(f'body: {resp.text.strip()}')

    if resp.status_code == 200:
        print('action paid for and payload delivered ✔')
        return 0
    if resp.status_code == 402:
        sys.exit('still 402 after retry — payment rejected (check token balance, network, and facilitator)')
    if resp.status_code == 503:
        sys.exit(f'robot {args.robot!r} is not connected to the proxy')
    sys.exit(f'unexpected status {resp.status_code}')


if __name__ == '__main__':
    raise SystemExit(main())
