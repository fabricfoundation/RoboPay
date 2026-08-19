import base64
import json
import unittest

import requests
from eth_account import Account
from x402 import x402ClientSync
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner

from test_base_sepolia_tunnel_e2e import create_verified_payment_headers


NETWORK = "eip155:84532"


class LivePaymentSigningTests(unittest.TestCase):
    def test_first_402_challenge_produces_locally_recoverable_payment(self):
        account = Account.from_key("0x" + "11" * 32)
        challenge = {
            "x402Version": 2,
            "resource": {
                "url": "http:///action",
                "description": "Run a paid robot action",
                "mimeType": "application/json",
            },
            "accepts": [
                {
                    "scheme": "exact",
                    "network": NETWORK,
                    "amount": "1000",
                    "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                    "payTo": "0x39a315667d557B1425bb1e5D371DD66d300c98c1",
                    "maxTimeoutSeconds": 300,
                    "extra": {"name": "USDC", "version": "2"},
                }
            ],
        }
        unpaid = requests.Response()
        unpaid.status_code = 402
        unpaid.headers["PAYMENT-REQUIRED"] = base64.b64encode(
            json.dumps(challenge).encode()
        ).decode()
        unpaid._content = b"null"

        client = x402ClientSync()
        register_exact_evm_client(
            client,
            EthAccountSigner(account),
            networks=NETWORK,
        )
        headers = create_verified_payment_headers(unpaid, client, account)

        self.assertIn("PAYMENT-SIGNATURE", headers)
        encoded = headers["PAYMENT-SIGNATURE"]
        payload = json.loads(base64.b64decode(encoded).decode())
        self.assertEqual(payload["accepted"], challenge["accepts"][0])
        self.assertEqual(payload["payload"]["authorization"]["from"], account.address)


if __name__ == "__main__":
    unittest.main()
