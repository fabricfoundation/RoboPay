"""Publish concise Base Sepolia settlement evidence in GitHub Actions logs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _append_summary(markdown: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(markdown)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show the terminal Base Sepolia settlement status from a CI evidence file."
    )
    parser.add_argument("--robot", required=True, help="Robot name displayed in CI.")
    parser.add_argument("--glob", required=True, help="Evidence file glob, relative to the repository.")
    args = parser.parse_args()

    evidence_files = sorted(
        Path().glob(args.glob), key=lambda path: path.stat().st_mtime, reverse=True
    )
    heading = f"{args.robot} — Base Sepolia settlement"
    if not evidence_files:
        message = "No settlement evidence was produced; the E2E did not reach settlement."
        print(f"::warning title={heading}::{message}")
        _append_summary(f"## {heading}\n\n⚠️ {message}\n")
        return 0

    evidence_path = evidence_files[0]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    terminal = evidence.get("terminal_status") or {}
    state = terminal.get("state", "unknown")
    settled = bool(terminal.get("settled"))
    transaction_hash = evidence.get("transaction_hash") or "not returned"
    basescan_url = evidence.get("basescan_url") or "not available"
    observed_at = evidence.get("timestamp", "not recorded")

    print(f"Settlement state: {state}")
    print(f"Settlement settled: {str(settled).lower()}")
    print(f"Settlement observed at: {observed_at}")
    print(f"Settlement transaction: {transaction_hash}")
    print(f"BaseScan: {basescan_url}")

    if settled and transaction_hash != "not returned":
        print(
            f"::notice title={heading}::SETTLED at {observed_at} | "
            f"transaction {transaction_hash} | {basescan_url}"
        )
        summary_status = "✅ **SETTLED**"
    else:
        print(
            f"::warning title={heading}::Terminal state={state}; "
            f"settled={str(settled).lower()}."
        )
        summary_status = "⚠️ **NOT SETTLED**"

    _append_summary(
        f"## {heading}\n\n"
        f"{summary_status}\n\n"
        f"| Field | Value |\n| --- | --- |\n"
        f"| Observed at | {observed_at} |\n"
        f"| Terminal state | <code>{state}</code> |\n"
        f"| Settled | <code>{str(settled).lower()}</code> |\n"
        f"| Transaction | [{transaction_hash}]({basescan_url}) |\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
