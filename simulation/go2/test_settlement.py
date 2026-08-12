"""Optional Base Sepolia settlement module: guards and no-settle-on-failure.

Validates ``settlement_base_sepolia`` without requiring web3.py or network
access:

- settle_if_success returns None for every non-success result (no-settle rule)
- settle_if_success returns None when the module is not configured (no env)
- SettlementConfig.from_env() returns None without a PRIVATE_KEY
- settle_if_success never raises when web3 / env are unavailable

The live on-chain path (EIP-3009 transferWithAuthorization) runs only when
BASE_SEPOLIA_RPC_URL + PRIVATE_KEY are set and web3.py is installed, which is
never the case on CI here, so CI always exercises the guarded fallback.

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
    settle_if_success,
)


def main():
    saved = {k: os.environ.pop(k, None) for k in
             ("PRIVATE_KEY", "BASE_SEPOLIA_RPC_URL", "PAYEE_ADDRESS",
              "USDC_CONTRACT", "FACILITATOR_URL")}
    try:
        checks = {}

        # 1) no-settle-on-failure: any non-success result settles nothing
        for status in ("error", "timeout", "collision", "rejected"):
            try:
                res = settle_if_success(status, {}, "0.002")
                checks[f"no_settle_{status}"] = res is None
            except Exception:
                checks[f"no_settle_{status}"] = False

        # 2) success without configuration -> guarded fallback, no raise
        try:
            res = settle_if_success("success", {}, "0.002")
            checks["success_unconfigured_returns_none"] = res is None
        except Exception:
            checks["success_unconfigured_returns_none"] = False

        # 3) config guard: no private key -> no config
        checks["no_config_without_key"] = SettlementConfig.from_env() is None
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
