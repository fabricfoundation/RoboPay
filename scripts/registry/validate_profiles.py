"""Fail when a RoboPay registry profile drifts across its contract documents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


REQUIRED_DOCUMENTS = (
    "robot.profile.yaml",
    "skills.yaml",
    "functions.yaml",
    "payment-policy.yaml",
    "execution-mapping.yaml",
)
SKILL_CATALOG = "skill-catalog.json"
CATALOG_SCHEMA_FIELDS = {
    "type", "required", "values", "minimum", "maximum", "items",
    "min_items", "max_items", "unique_items",
}
CATALOG_TYPES = {"string", "number", "integer", "boolean", "array"}


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        document = yaml.safe_load(source)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return document


def _ids(entries: list[dict], field: str, path: Path, errors: list[str]) -> set[str]:
    values: list[str] = []
    for entry in entries:
        value = entry.get(field) if isinstance(entry, dict) else None
        if not isinstance(value, str) or not value:
            errors.append(f"{path}: every entry needs a non-empty {field}")
            continue
        values.append(value)
    if len(values) != len(set(values)):
        errors.append(f"{path}: duplicate {field} values")
    return set(values)


def _validate_catalog_param(schema: object, label: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        errors.append(f"{label}: parameter schema must be an object")
        return
    unknown = sorted(set(schema) - CATALOG_SCHEMA_FIELDS)
    if unknown:
        errors.append(f"{label}: unsupported Tunnel schema field(s): {', '.join(unknown)}")
    schema_type = schema.get("type")
    if schema_type not in CATALOG_TYPES:
        errors.append(f"{label}: unsupported Tunnel parameter type {schema_type!r}")
        return
    if schema_type == "array":
        if "items" not in schema:
            errors.append(f"{label}: array schema requires items")
        else:
            _validate_catalog_param(schema["items"], f"{label}.items", errors)
    for field in ("min_items", "max_items"):
        value = schema.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            errors.append(f"{label}: {field} must be a non-negative integer")
    if isinstance(schema.get("min_items"), int) and isinstance(schema.get("max_items"), int):
        if schema["min_items"] > schema["max_items"]:
            errors.append(f"{label}: min_items exceeds max_items")


def _validate_profile(profile_path: Path) -> list[str]:
    root = profile_path.parent
    errors: list[str] = []
    documents: dict[str, dict] = {}
    for filename in REQUIRED_DOCUMENTS:
        path = root / filename
        if not path.is_file():
            errors.append(f"{root}: missing required document {filename}")
            continue
        try:
            documents[filename] = _load_yaml(path)
        except (OSError, ValueError, yaml.YAMLError) as error:
            errors.append(str(error))

    if len(documents) != len(REQUIRED_DOCUMENTS):
        return errors

    profile = documents["robot.profile.yaml"]
    profile_id = profile.get("profileId")
    if not isinstance(profile_id, str) or not profile_id:
        return [f"{profile_path}: missing profileId"]
    if root.name != profile_id:
        errors.append(f"{profile_path}: directory name must equal profileId ({profile_id})")

    for filename, document in documents.items():
        if filename == "payment-policy.yaml":
            continue
        if document.get("profileId") != profile_id:
            errors.append(f"{root / filename}: profileId must equal {profile_id}")

    skills_path = root / "skills.yaml"
    skills = documents["skills.yaml"].get("skills")
    if not isinstance(skills, list) or not skills:
        errors.append(f"{skills_path}: skills must be a non-empty list")
        return errors
    skill_ids = _ids(skills, "skillId", skills_path, errors)

    catalog_path = root / SKILL_CATALOG
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"{catalog_path}: valid robot-scoped JSON skill catalog is required ({error})")
        catalog = []
    if not isinstance(catalog, list) or not catalog:
        errors.append(f"{catalog_path}: catalog must be a non-empty list")
    else:
        catalog_ids = _ids(catalog, "skill_id", catalog_path, errors)
        if catalog_ids != skill_ids:
            errors.append(
                f"{catalog_path}: catalog skills {sorted(catalog_ids)} must equal "
                f"declared skills {sorted(skill_ids)}"
            )
        for skill in catalog:
            if not isinstance(skill, dict):
                continue
            params = skill.get("params")
            if not isinstance(params, dict):
                errors.append(f"{catalog_path}: {skill.get('skill_id')}.params must be an object")
                continue
            for param_name, schema in params.items():
                _validate_catalog_param(
                    schema,
                    f"{catalog_path}: {skill.get('skill_id')}.{param_name}",
                    errors,
                )

    payment_path = root / "payment-policy.yaml"
    policies = documents["payment-policy.yaml"].get("policies")
    if not isinstance(policies, list):
        errors.append(f"{payment_path}: policies must be a list")
    else:
        policy_ids = _ids(policies, "skillId", payment_path, errors)
        if policy_ids != skill_ids:
            errors.append(
                f"{payment_path}: policy skills {sorted(policy_ids)} must equal "
                f"declared skills {sorted(skill_ids)}"
            )
        required_by_skill = {
            item["skillId"]: item.get("paymentRequired")
            for item in skills
            if isinstance(item, dict) and isinstance(item.get("skillId"), str)
        }
        required_by_policy = {
            item["skillId"]: item.get("required")
            for item in policies
            if isinstance(item, dict) and isinstance(item.get("skillId"), str)
        }
        for skill_id in skill_ids:
            if required_by_skill.get(skill_id) != required_by_policy.get(skill_id):
                errors.append(
                    f"{payment_path}: required for {skill_id} must match "
                    f"skills.yaml paymentRequired"
                )

    mapping_path = root / "execution-mapping.yaml"
    mappings = documents["execution-mapping.yaml"].get("mappings")
    if not isinstance(mappings, dict):
        errors.append(f"{mapping_path}: mappings must be a mapping")
    else:
        mapping_ids = set(mappings)
        if mapping_ids != skill_ids:
            errors.append(
                f"{mapping_path}: mapping skills {sorted(mapping_ids)} must equal "
                f"declared skills {sorted(skill_ids)}"
            )

    runtime = profile.get("runtime")
    transport = documents["execution-mapping.yaml"].get("transport")
    if not isinstance(runtime, dict) or not isinstance(transport, dict):
        errors.append(f"{root}: runtime and execution mapping transport must be mappings")
    else:
        if runtime.get("transport") != transport.get("type"):
            errors.append(f"{mapping_path}: transport.type must match robot profile runtime.transport")
        for field in ("actionTopic", "resultTopic", "metricsTopic"):
            if runtime.get(field) != transport.get(field):
                errors.append(f"{mapping_path}: transport.{field} must match robot profile runtime.{field}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "registry",
        help="Registry root containing vendor profiles.",
    )
    args = parser.parse_args()
    profile_paths = sorted(args.registry_root.rglob("robot.profile.yaml"))
    if not profile_paths:
        print(f"No profiles found under {args.registry_root}", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    for profile_path in profile_paths:
        errors = _validate_profile(profile_path)
        if errors:
            all_errors.extend(errors)
        else:
            print(f"OK: {profile_path.parent.relative_to(args.registry_root)}")

    if all_errors:
        print("Registry profile drift detected:", file=sys.stderr)
        print("\\n".join(f"- {error}" for error in all_errors), file=sys.stderr)
        return 1
    print(f"Registry profile contract: OK ({len(profile_paths)} profiles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
