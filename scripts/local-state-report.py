#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_PATHS = (
    ".sources",
    ".worktrees",
    ".state",
    "out",
    ".venv",
    ".pytest_cache",
    ".playwright-mcp",
    "test-results",
    "tests/browser/node_modules",
    "tests/browser/test-results",
    "upstream",
)


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def git_lines(*args: str) -> set[str]:
    result = subprocess.run(["git", *args], cwd=ROOT_DIR, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return set()
    return set(result.stdout.splitlines())


def classify(path: str, tracked: set[str], ignored: set[str], untracked: set[str]) -> str:
    has_tracked = path in tracked or any(item.startswith(f"{path}/") for item in tracked)
    has_ignored = path in ignored or any(item.startswith(f"{path}/") for item in ignored)
    if has_tracked and has_ignored:
        return "tracked-root-with-ignored-contents"
    if has_tracked:
        return "tracked"
    if has_ignored:
        return "ignored"
    if path in untracked or any(item.startswith(f"{path}/") for item in untracked):
        return "untracked"
    return "absent"


def report() -> dict[str, Any]:
    tracked = git_lines("ls-files")
    ignored = git_lines("ls-files", "--others", "--ignored", "--exclude-standard")
    untracked = git_lines("ls-files", "--others", "--exclude-standard")
    entries = []
    for relative in STATE_PATHS:
        path = ROOT_DIR / relative
        entries.append(
            {
                "path": relative,
                "exists": path.exists(),
                "kind": "directory" if path.is_dir() else ("file" if path.is_file() else "missing"),
                "bytes": path_size(path),
                "classification": classify(relative, tracked, ignored, untracked),
                "canAffectBuildRunReview": relative in {".sources", ".worktrees", ".state", "out", "upstream"},
                "cleanup": f"review then remove {relative}" if path.exists() else "",
            }
        )
    unexpected = sorted(
        item
        for item in untracked
        if not any(item == known or item.startswith(f"{known}/") for known in STATE_PATHS)
    )
    return {
        "status": "reported",
        "entries": entries,
        "unexpectedUntracked": unexpected,
        "note": "read-only report; no files were removed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report ignored and generated local virtualAxe state.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = report()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for entry in payload["entries"]:
            print(
                f"{entry['path']}: {entry['classification']} "
                f"{entry['kind']} {entry['bytes']} bytes"
            )
        if payload["unexpectedUntracked"]:
            print("unexpected untracked paths:")
            for path in payload["unexpectedUntracked"]:
                print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
