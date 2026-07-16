#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_registry import SourceRegistryError, load_source_registry


PYTHON = sys.executable
ENVIRONMENT_PATTERNS = (
    "No usable container or native ESP-IDF runtime is available",
    "Podman is installed but is not reachable",
    "Docker is installed but is not reachable",
    "is not available locally",
    "AxeOS frontend dependencies are missing",
    "Could not resolve host",
    "Failed to connect",
    "Temporary failure in name resolution",
    "unable to access",
)


def run_check(name: str, command: list[str], *, environment_gated: bool = False) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "virtualaxe-uv-cache"))
    result = subprocess.run(command, cwd=ROOT_DIR, env=env, text=True, capture_output=True, check=False)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode == 0:
        status = "passed"
        classification = "passed"
    elif environment_gated and any(pattern in output for pattern in ENVIRONMENT_PATTERNS):
        status = "failed"
        classification = "failed due to environment/dependency issue"
    else:
        status = "failed"
        classification = "failed due to repository issue"
    return {
        "name": name,
        "command": command,
        "status": status,
        "classification": classification,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def validation_source(environment: Mapping[str, str] | None = None) -> str:
    env = environment if environment is not None else os.environ
    registry = load_source_registry()
    requested = env.get("SOURCE") or env.get("SOURCE_NAME") or registry.default_source
    return registry.canonical_name(requested)


def checks(lite: bool, source_name: str = "bitaxe") -> list[tuple[str, list[str], bool]]:
    base = [
        ("compile", [PYTHON, "-m", "compileall", "-q", "scripts", "tests"], False),
        ("shell-syntax", ["bash", "-n", *[str(path.relative_to(ROOT_DIR)) for path in sorted((ROOT_DIR / "scripts").glob("*.sh"))]], False),
        ("config", [PYTHON, "scripts/validate-config.py"], False),
        ("secret-scan", [PYTHON, "scripts/secret-scan.py"], False),
        ("release-hygiene", [PYTHON, "-m", "pytest", "-q", "tests/test_release_hygiene.py"], False),
        ("python-tests", [PYTHON, "-m", "pytest", "-q"], False),
    ]
    if lite:
        return base
    return [
        *base,
        ("patch-check", ["make", "patch-check", f"SOURCE={source_name}"], True),
        (
            "verify-test-ci",
            [PYTHON, "scripts/virtualaxe.py", "verify-test-ci", "--source", source_name, "--json"],
            True,
        ),
        ("verify-submit-replay", ["make", "verify-submit-replay", f"SOURCE={source_name}"], True),
    ]


def run_validation(lite: bool, source_name: str | None = None) -> dict[str, Any]:
    selected_source = source_name or validation_source()
    results = [
        run_check(name, command, environment_gated=gated)
        for name, command, gated in checks(lite, selected_source)
    ]
    failures = [result for result in results if result["status"] == "failed"]
    return {
        "status": "passed" if not failures else "failed",
        "mode": "lite" if lite else "full",
        "source": selected_source,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic virtualAxe validation checks.")
    parser.add_argument("--lite", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = run_validation(args.lite)
    except SourceRegistryError as exc:
        print(f"validate: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for result in payload["results"]:
            print(f"{result['name']}: {result['status']} ({result['classification']})")
            if result["status"] == "failed":
                sys.stderr.write(result["stderr"] or result["stdout"])
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
