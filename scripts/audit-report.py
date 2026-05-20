#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent


def run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, cwd=ROOT_DIR, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    payload.setdefault("returncode", result.returncode)
    if result.stderr:
        payload.setdefault("stderr", result.stderr)
    return payload


def git_status() -> str:
    result = subprocess.run(["git", "status", "--short"], cwd=ROOT_DIR, text=True, capture_output=True, check=False)
    return result.stdout


def build_report() -> dict[str, Any]:
    return {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gitStatusShort": git_status(),
        "config": run_json(["python3", "scripts/validate-config.py", "--json"]),
        "secretScan": run_json(["python3", "scripts/secret-scan.py", "--json"]),
        "localState": run_json(["python3", "scripts/local-state-report.py", "--json"]),
        "drift": run_json(["python3", "scripts/drift-check.py", "--json"]),
        "patchAudit": run_json(["python3", "scripts/patch-audit.py", "--json"]),
    }


def markdown(report: dict[str, Any]) -> str:
    blockers = report.get("drift", {}).get("releaseBlockers", [])
    lines = [
        "# virtualAxe Repository Readiness Audit",
        "",
        f"- Generated: `{report['generatedAt']}`",
        f"- Git status clean: `{not bool(report['gitStatusShort'].strip())}`",
        f"- Config: `{report.get('config', {}).get('status', 'unknown')}`",
        f"- Secret scan: `{report.get('secretScan', {}).get('status', 'unknown')}`",
        f"- Drift: `{report.get('drift', {}).get('status', 'unknown')}`",
        f"- Patch count: `{report.get('patchAudit', {}).get('patchCount', 'unknown')}`",
        f"- Hunk minimized: `{report.get('patchAudit', {}).get('hunkMinimized', False)}`",
        "",
        "## Release Blockers",
        "",
    ]
    if blockers:
        lines.extend(f"- {blocker}" for blocker in blockers)
    else:
        lines.append("- None reported by drift-check.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a compact virtualAxe readiness audit report.")
    parser.add_argument("--out-dir", default=str(ROOT_DIR / "out" / "audit"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_report()
    json_path = out_dir / "repository-readiness-audit.json"
    markdown_path = out_dir / "repository-readiness-audit.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps({"status": "reported", "json": str(json_path), "markdown": str(markdown_path)}, indent=2, sort_keys=True))
    else:
        print(f"audit report: {markdown_path}")
        print(f"audit json: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
