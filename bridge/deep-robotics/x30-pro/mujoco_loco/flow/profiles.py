"""Profile manifests -- loaded at runtime, not decorative.

The five YAML files under `profiles/` are the contract a RoboPay reviewer
reads. To make sure they describe the *running* bridge and not an aspiration,
this module loads them and the rest of the code asks it questions:

    flow/relay.py    -> price + x402 `accepts` block for the 402 challenge
    flow/relay.py    -> parameter validation before any robot is contacted
    flow/demo.py     -> skill discovery (functions.yaml::list_skills)
    tests/test_profiles.py -> every number is cross-checked against arm_spec.py

Nothing here can settle a payment or move a robot; it only answers questions.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

MANIFESTS = {
    "robot": "robot.profile.yaml",
    "skills": "skills.yaml",
    "functions": "functions.yaml",
    "payment": "payment-policy.yaml",
    "mapping": "execution-mapping.yaml",
}

UNSET_ADDRESS = "0x0000000000000000000000000000000000000000"


class ProfileError(Exception):
    """Manifest missing, unreadable or internally inconsistent."""


class ParamError(ProfileError):
    """Skill parameters rejected before execution."""


# ------------------------------------------------------------------ loading
@functools.lru_cache(maxsize=None)
def load(name: str) -> dict:
    if name not in MANIFESTS:
        raise ProfileError(f"unknown manifest {name!r} (expected {sorted(MANIFESTS)})")
    try:
        import yaml
    except ImportError as exc:                                # pragma: no cover
        raise ProfileError(
            "pyyaml is required to read the profile manifests "
            "(pip install -r requirements.txt)"
        ) from exc
    path = PROFILES_DIR / MANIFESTS[name]
    if not path.exists():
        raise ProfileError(f"missing manifest: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ProfileError(f"manifest {path.name} did not parse to a mapping")
    return data


def robot_profile() -> dict:
    return load("robot")


def skills_catalog() -> dict:
    return load("skills")


def functions_manifest() -> dict:
    return load("functions")


def payment_policy() -> dict:
    return load("payment")


def execution_mapping() -> dict:
    return load("mapping")


def robot_id() -> str:
    return robot_profile()["robotId"]


def profile_id() -> str:
    return robot_profile()["profileId"]


def topics() -> dict:
    return robot_profile()["transport"]["topics"]


# -------------------------------------------------------------------- skills
def skill(skill_id: str) -> dict:
    for entry in skills_catalog().get("skills", []):
        if entry.get("skillId") == skill_id:
            return entry
    raise ProfileError(f"unsupported_skill:{skill_id}")


def skill_ids() -> list:
    return [s["skillId"] for s in skills_catalog().get("skills", [])]


def list_skills(robot: str | None = None) -> dict:
    """functions.yaml::list_skills -- free discovery, no payment, no robot."""
    if robot and robot != robot_id():
        raise ProfileError(f"unknown robotId:{robot}")
    out = []
    for entry in skills_catalog().get("skills", []):
        pricing = entry.get("pricing", {})
        out.append({
            "skillId": entry["skillId"],
            "displayName": entry.get("displayName"),
            "description": (entry.get("description") or "").strip(),
            "price": pricing.get("amount"),
            "currency": pricing.get("currency"),
            "network": pricing.get("network"),
            "settlement": pricing.get("settlement"),
            "paramsSchema": entry.get("paramsSchema", {}),
            "failureModes": [f["reason"] for f in entry.get("failureModes", [])],
        })
    return {"robotId": robot_id(), "profileId": profile_id(), "skills": out}


# ------------------------------------------------------------------- payment
def _env_address(var: str) -> str:
    """Wallet material comes from the environment, never from the repo."""
    return os.environ.get(var) or UNSET_ADDRESS


def payment_requirements(skill_id: str, resource: str | None = None) -> list:
    """The x402 `accepts` block, assembled from payment-policy.yaml + skills.yaml."""
    policy = payment_policy()
    provider = policy["provider"]
    challenge = policy["challenge"]
    pricing = skill(skill_id).get("pricing", {})
    asset = provider.get("asset", {})
    return [{
        "scheme": provider.get("scheme", "exact"),
        "network": provider.get("network"),
        "chainId": provider.get("chainId"),
        "asset": asset.get("address"),
        "assetSymbol": asset.get("symbol"),
        "maxAmountRequired": pricing.get("amountAtomic"),
        "amount": pricing.get("amount"),
        "currency": pricing.get("currency"),
        "payTo": _env_address(provider.get("payToAddressEnv", "")),
        "resource": resource or challenge.get("resource"),
        "description": challenge.get("description"),
        "maxTimeoutSeconds": challenge.get("maxTimeoutSeconds"),
        "settlement": pricing.get("settlement"),
    }]


def payment_required(skill_id: str, error: str | None = None) -> dict:
    """Complete HTTP 402 body. Callers must not execute anything after this."""
    body = {
        "status": 402,
        "paymentRequired": True,
        "x402Version": str(payment_policy()["provider"].get("version", "1")),
        "header": payment_policy()["challenge"].get("headerIn"),
        "accepts": payment_requirements(skill_id),
    }
    if error:
        body["error"] = error
    return body


def settle_on_failure_allowed() -> bool:
    """Read back the safety switch so a test can assert the policy is honoured."""
    return bool(payment_policy().get("safety", {}).get("settleOnFailure", False))


# ---------------------------------------------------------- param validation
def validate_params(skill_id: str, params: dict | None) -> dict:
    """Minimal JSON-Schema subset enforcement (the only one skills.yaml uses).

    Raises ParamError -- the relay turns that into a rejection *before* the
    robot is contacted and *before* anything is settled.
    """
    schema = skill(skill_id).get("paramsSchema") or {}
    props = schema.get("properties", {})
    params = dict(params or {})

    if schema.get("additionalProperties") is False:
        extra = sorted(set(params) - set(props))
        if extra:
            raise ParamError(f"unknown parameter(s): {', '.join(extra)}")

    for key in schema.get("required", []):
        if key not in params:
            raise ParamError(f"missing required parameter: {key}")

    resolved = {}
    for key, spec in props.items():
        if key not in params:
            if "default" in spec:
                resolved[key] = spec["default"]
            continue
        value = params[key]
        expected = spec.get("type")
        if expected == "string" and not isinstance(value, str):
            raise ParamError(f"{key} must be a string")
        if expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ParamError(f"{key} must be an integer")
        if expected == "number" and isinstance(value, bool):
            raise ParamError(f"{key} must be a number")
        if "enum" in spec and value not in spec["enum"]:
            raise ParamError(
                f"{key}={value!r} is not one of {spec['enum']}"
            )
        if "minimum" in spec and value < spec["minimum"]:
            raise ParamError(f"{key} must be >= {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise ParamError(f"{key} must be <= {spec['maximum']}")
        resolved[key] = value
    return resolved
