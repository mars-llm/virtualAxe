#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
PATTERNS = (
    ("private-key", re.compile(r"BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY")),
    ("secret-assignment", re.compile(r"(?i)\b(?:secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}")),
    ("mnemonic-seed", re.compile(r"(?i)\b(?:mnemonic|seed phrase)\b\s*[:=]")),
)
ALLOWLIST = (
    "<pool-user>",
    "BITRONICS_API_TOKEN_PATTERN",
    "window\\.POOL_API_TOKEN",
)


def tracked_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT_DIR, text=True, capture_output=True, check=True)
    return [ROOT_DIR / line for line in result.stdout.splitlines() if line]


def is_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return True
    return b"\0" in chunk


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def scan_file(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if is_binary(path):
        return findings
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return findings
    for line_number, line in enumerate(lines, start=1):
        if any(token in line for token in ALLOWLIST):
            continue
        for kind, pattern in PATTERNS:
            if pattern.search(line):
                findings.append(
                    {
                        "file": display_path(path),
                        "line": line_number,
                        "kind": kind,
                    }
                )
    return findings


def scan_repo() -> dict[str, Any]:
    findings = [finding for path in tracked_files() for finding in scan_file(path)]
    return {
        "status": "passed" if not findings else "failed",
        "findings": findings,
        "scannedFiles": len(tracked_files()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan tracked virtualAxe files for private secret material.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = scan_repo()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["findings"]:
        for finding in payload["findings"]:
            print(f"{finding['file']}:{finding['line']}: {finding['kind']}", file=sys.stderr)
    else:
        print("secret scan passed")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
