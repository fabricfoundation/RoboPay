#!/usr/bin/env python3
"""Validate registry profiles against bridge constants."""
from pathlib import Path
import yaml
import json
import sys

REGISTRY_DIR = Path(__file__).resolve().parents[2] / "registry"
ERRORS = []

for profile_dir in sorted(REGISTRY_DIR.rglob("*.yaml")):
    if "robot.profile.yaml" not in profile_dir.name:
        continue
    try:
        profile = yaml.safe_load(profile_dir.read_text(encoding="utf-8"))
        profile_id = profile.get("profileId", "")
        vendor = profile.get("vendor", "")
        
        parent = profile_dir.parent
        skills_path = parent / "skills.yaml"
        catalog_path = parent / "skill-catalog.json"
        
        if not skills_path.exists():
            ERRORS.append(f"{profile_id}: missing skills.yaml")
            continue
        
        skills = yaml.safe_load(skills_path.read_text(encoding="utf-8"))
        skill_ids = {s["skillId"] for s in skills.get("skills", [])}
        
        if catalog_path.exists():
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog_skills = catalog.get("skills", [])
            if isinstance(catalog_skills, list):
                catalog_ids = {s["skillId"] for s in catalog_skills}
            else:
                catalog_ids = set(catalog_skills.keys())
            if skill_ids != catalog_ids:
                ERRORS.append(f"{profile_id}: skills mismatch {skill_ids} != {catalog_ids}")
        
        for req in ["payment-policy.yaml", "execution-mapping.yaml", "functions.yaml"]:
            if not (parent / req).exists():
                ERRORS.append(f"{profile_id}: missing {req}")
        
        print(f"  OK: {profile_id} ({len(skill_ids)} skills)")
    except Exception as e:
        ERRORS.append(f"{profile_dir}: {e}")

if ERRORS:
    print(f"\n{len(ERRORS)} errors:")
    for err in ERRORS:
        print(f"  FAIL: {err}")
    sys.exit(1)
else:
    print(f"\nAll profiles valid.")
