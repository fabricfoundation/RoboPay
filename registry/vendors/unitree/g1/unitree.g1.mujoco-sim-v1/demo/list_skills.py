#!/usr/bin/env python3
"""List available skills for a robot."""
import argparse
import json
import yaml
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot", default="g1-demo-001")
    args = parser.parse_args()

    skills_path = Path(__file__).parent.parent / "skills.yaml"
    with open(skills_path) as f:
        data = yaml.safe_load(f)

    print(f"Skills for {args.robot}:")
    for skill in data.get("skills", []):
        price = skill.get("priceUSDC", "free") if skill.get("paymentRequired") else "free"
        print(f"  - {skill['skillId']}: {skill['description']} ({price} USDC)")


if __name__ == "__main__":
    main()
