"""Optional Base Sepolia settlement module: guards + EIP-3009 offline proof.

Validates ``settlement_base_sepolia`` without any network access:

- no-settle-on-failure: every non-success result returns None
- success without configuration -> guarded fallback (None, never raises)
- SettlementConfig.from_env() -> None without a PRIVATE_KEY
- EIP-3009 correctness (offline, runs on CI):
    * TransferWithAuthorization typehash == 0x7c7c6cdb... (EIP-3009 canonical)
    * EIP712Domain typehash == 0x8b73c3c6... (EIP-712 canonical)
    * digest is deterministic for a fixed payload + domain
    * digest is sensitive to every domain field (name "USDC" vs "USD Coin",
      chainId, verifyingContract) and to the nonce (bytes32)
    * when eth_account is available: sign the typed data with a payer key,
      recover the signer and prove it equals "from" (full offline proof)

The live on-chain path (transferWithAuthorization broadcast) runs only when
BASE_SEPOLIA_RPC_URL + PRIVATE_KEY are set and web3.py is installed, which is
never the case on CI here — so CI exercises the offline proof and the guarded
fallbacks, and the on-chain ABI is pinned by the offline tests.

Prints PASS/FAIL, exits nonzero on failure.
"""

import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

from settlement_base_sepolia import (  # noqa: E402
    SettlementConfig,
    build_auth_digest,
    domain_separator,
    settle_if_success,
    split_signature,
    to_bytes32,
    verify_authorization,
    TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
    EIP712_DOMAIN_TYPEHASH,
    DEFAULT_USDC_CONTRACT,
    DEFAULT_CHAIN_ID,
)

# EIP-3009 canonical typehash (eips.ethereum.org/EIPS/eip-3009 / Circle)
KNOWN_TWA_TYPEHASH = "0x7c7c6cdb67a18743f49ec6fa9b35f50d52ed05cbed4cc592e13b44501c1a2267"
# EIP-712 canonical domain typehash
KNOWN_DOMAIN_TYPEHASH = "0x8b73c3c69bb8fe3d512ecc4cf759cc79239f7b179b0ffacaa9a75d522b39400f"


def make_auth(payer: str, payee: str, nonce_hex: str, signature: str = "") -> dict:
    return {
        "from": payer,
        "to": payee,
        "value": 5000,               # 0.005 USDC (6 decimals)
        "validAfter": 0,
        "validBefore": 2 ** 64 - 1,
        "nonce": nonce_hex,
        "signature": signature,
    }


def main():
    saved = {k: os.environ.pop(k, None) for k in
             ("PRIVATE_KEY", "BASE_SEPOLIA_RPC_URL", "PAYEE_ADDRESS",
              "USDC_CONTRACT", "FACILITATOR_URL")}
    try:
        checks = {}

        # 1) no-settle-on-failure: any non-success result settles nothing
        for status in ("error", "timeout", "collision", "rejected"):
            try:
                checks[f"no_settle_{status}"] = (
                    settle_if_success(status, {}, "0.002") is None)
            except Exception:
                checks[f"no_settle_{status}"] = False

        # 2) success without configuration -> guarded fallback, no raise
        try:
            checks["success_unconfigured_returns_none"] = (
                settle_if_success("success", {}, "0.002") is None)
        except Exception:
            checks["success_unconfigured_returns_none"] = False

        # 3) config guard: no private key -> no config
        checks["no_config_without_key"] = SettlementConfig.from_env() is None

        # --- EIP-3009 offline proof -------------------------------------
        checks["typehash_transfer_with_auth"] = (
            TRANSFER_WITH_AUTHORIZATION_TYPEHASH == KNOWN_TWA_TYPEHASH[2:])
        checks["typehash_eip712_domain"] = (
            EIP712_DOMAIN_TYPEHASH == KNOWN_DOMAIN_TYPEHASH[2:])

        payer = "0x1111111111111111111111111111111111111111"
        payee = "0x2222222222222222222222222222222222222222"
        nonce = to_bytes32(123456789)
        auth = make_auth(payer, payee, nonce)

        # domain sensitivity: every field must change the digest
        base_digest = build_auth_digest(
            auth, DEFAULT_CHAIN_ID, DEFAULT_USDC_CONTRACT)
        checks["digest_deterministic"] = (
            base_digest == build_auth_digest(
                auth, DEFAULT_CHAIN_ID, DEFAULT_USDC_CONTRACT))

        wrong_name = build_auth_digest(
            auth, DEFAULT_CHAIN_ID, DEFAULT_USDC_CONTRACT, name="USD Coin")
        checks["digest_sensitive_to_domain_name"] = (
            wrong_name != base_digest)
        checks["domain_name_is_usdc_not_usd_coin"] = (
            domain_separator(DEFAULT_CHAIN_ID, DEFAULT_USDC_CONTRACT,
                             name="USDC")
            != domain_separator(DEFAULT_CHAIN_ID, DEFAULT_USDC_CONTRACT,
                                name="USD Coin"))

        wrong_chain = build_auth_digest(
            auth, DEFAULT_CHAIN_ID + 1, DEFAULT_USDC_CONTRACT)
        checks["digest_sensitive_to_chain_id"] = wrong_chain != base_digest

        wrong_contract = build_auth_digest(
            auth, DEFAULT_CHAIN_ID, "0x3333333333333333333333333333333333333333")
        checks["digest_sensitive_to_contract"] = (
            wrong_contract != base_digest)

        wrong_nonce = build_auth_digest(
            make_auth(payer, payee, to_bytes32(42)),
            DEFAULT_CHAIN_ID, DEFAULT_USDC_CONTRACT)
        checks["digest_sensitive_to_nonce"] = wrong_nonce != base_digest

        # nonce normalization: int and hex produce the same bytes32
        checks["nonce_int_and_hex_equal"] = (
            to_bytes32(255) == to_bytes32("0x" + "ff".rjust(64, "0")))

        # signature splitting: 65 bytes -> v/r/s (65-byte round trip)
        sig = "0x" + "11" * 32 + "22" * 32 + "1b"
        v, r, s = split_signature(sig)
        checks["signature_split_roundtrip"] = (
            v == 27 and r == "0x" + "11" * 32 and s == "0x" + "22" * 32)

        # full offline sign -> recover proof (only with eth_account/web3)
        try:
            from eth_account import Account
            from eth_account.messages import encode_typed_data
            from settlement_base_sepolia import build_eip712_domain
            import secrets

            sk = "0x" + secrets.token_hex(32)
            acct = Account.from_key(sk)
            msg = {
                "from": acct.address,
                "to": payee,
                "value": 5000,
                "validAfter": 0,
                "validBefore": 2 ** 64 - 1,
                "nonce": nonce,
            }
            typed = build_eip712_domain(DEFAULT_CHAIN_ID, DEFAULT_USDC_CONTRACT)
            typed["message"] = msg
            enc = encode_typed_data(full_message=typed)
            signature = acct.sign_message(enc).signature.hex()
            auth_signed = make_auth(acct.address, payee, nonce,
                                    "0x" + signature)
            checks["signature_recovers_to_from"] = verify_authorization(
                auth_signed, DEFAULT_CHAIN_ID, DEFAULT_USDC_CONTRACT)
            checks["signature_rejects_wrong_domain"] = not verify_authorization(
                auth_signed, DEFAULT_CHAIN_ID, DEFAULT_USDC_CONTRACT,
                name="USD Coin")
        except Exception:
            checks["signature_recovers_to_from"] = False
            checks["signature_rejects_wrong_domain"] = False
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    print(json.dumps({"checks": checks}, indent=1))
    ok_all = all(checks.values())
    print("PASS" if ok_all else "FAIL")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
