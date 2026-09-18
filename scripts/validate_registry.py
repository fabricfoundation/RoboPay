#!/usr/bin/env python3
"""Validate the profile registry that every bounty submission extends.

Design goals
------------
The registry schema is still being adopted, so this validator is *permissive by
design*: it fails only on conditions that are unambiguously wrong for any
submission, and reports everything else as advisory information for reviewers.

Hard failures (non-zero exit):
  1. Any YAML/JSON file under the registry fails to parse.
  2. Two profile directories share the same directory name (cross-PR collisions
     on robot/profile ids are the exact failure mode this exists to prevent).

Advisories (printed, never fail the run):
  3. A profile directory with no descriptor file (`robot.profile.yaml`,
     `profile.yaml`, `profile.yml`, `profile.json`).
  4. A profile directory missing the common optional files
     (`skills.*`, `functions.*`, `payment-policy.*`, `execution-mapping.*`).
  5. A full inventory of everything found, for reviewers.

Tune the advisory set (not the hard-failure set) once the real schema is
settled upstream.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only when PyYAML is absent
    yaml = None

DESCRIPTOR_NAMES = {"robot.profile.yaml", "profile.yaml", "profile.yml", "profile.json"}
OPTIONAL_FILES = ("skills.", "functions.", "payment-policy.", "execution-mapping.")
DATA_SUFFIXES = {".json", ".yaml", ".yml"}


def load_file(path: Path):
    """Parse a data file; returns the object or None when PyYAML is missing."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if yaml is None:
        return None
    return yaml.safe_load(text)


def iter_data_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in DATA_SUFFIXES:
            yield path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path, help="Path to the registry/ directory")
    args = parser.parse_args()

    root: Path = args.registry
    if not root.is_dir():
        print(f"ERROR: registry path {root} does not exist", file=sys.stderr)
        return 2

    failures: list[str] = []
    advisories: list[str] = []
    inventory: list[str] = []

    data_files = list(iter_data_files(root))
    if not data_files:
        print(f"INFO: no YAML/JSON files under {root}; nothing to validate")
        return 0

    for path in data_files:
        rel = path.relative_to(root)
        try:
            load_file(path)
        except Exception as exc:  # json.JSONDecodeError, yaml.YAMLError, ...
            failures.append(f"{rel}: unparseable -> {exc}")
        else:
            inventory.append(f"parsed   {rel}")

    # Every profile directory must carry a descriptor; names must be unique.
    seen_names: dict[str, list[Path]] = {}
    profile_dirs: list[Path] = []
    for directory in sorted(d for d in root.rglob("*") if d.is_dir()):
        if not any(p.name in DESCRIPTOR_NAMES for p in directory.iterdir()):
            continue
        profile_dirs.append(directory)
        seen_names.setdefault(directory.name, []).append(directory)

    for name, dirs in seen_names.items():
        if len(dirs) > 1:
            locations = ", ".join(str(d.relative_to(root)) for d in dirs)
            failures.append(f"duplicate profile directory name {name!r} at: {locations}")

    for directory in profile_dirs:
        rel = directory.relative_to(root)
        inventory.append(f"profile  {rel}")
        missing = [suffix[:-1] for suffix in OPTIONAL_FILES
                   if not any(directory.glob(f"{suffix}*"))]
        if missing:
            advisories.append(f"{rel}: no {'/'.join(missing)} files (optional)")

    # Candidate profile directories (vendors/<vendor>/<robot>/<profile>) that
    # hold data files but no descriptor: broken or incomplete submissions.
    for directory in sorted(d for d in root.rglob("*") if d.is_dir()):
        rel = directory.relative_to(root)
        if len(rel.parts) != 4:  # registry/vendors/<vendor>/<robot>/<profile>
            continue
        if any(p.name in DESCRIPTOR_NAMES for p in directory.iterdir()):
            continue
        has_data = (any(directory.glob("*.yaml")) or any(directory.glob("*.yml"))
                    or any(directory.glob("*.json")))
        if has_data:
            advisories.append(f"{rel}: profile directory contains data files but no descriptor")

    print("Registry validation report")
    print("=" * 40)
    for line in inventory:
        print(line)
    if advisories:
        print("-" * 40)
        for line in advisories:
            print(f"ADVISORY {line}")
    if failures:
        print("-" * 40)
        for line in failures:
            print(f"FAIL     {line}")
        print(f"\n{len(failures)} hard failure(s); {len(advisories)} advisory(ies).")
        return 1

    print(f"\nOK: {len(data_files)} data file(s), {len(profile_dirs)} profile(s); "
          f"{len(advisories)} advisory(ies).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
