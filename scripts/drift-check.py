#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
SOURCES_FILE = ROOT_DIR / "configs" / "sources.json"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_registry import load_source_registry


def series_file_for_source(source_name: str) -> Path:
    return load_source_registry(SOURCES_FILE).get(source_name).patch_series_path


def series(source_name: str) -> list[str]:
    series_file = series_file_for_source(source_name)
    return [
        line.strip()
        for line in series_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def patch_series_hash(source_name: str) -> str:
    series_file = series_file_for_source(source_name)
    patch_dir = series_file.parent
    digest = hashlib.sha256()
    digest.update(series_file.read_bytes())
    for name in series(source_name):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((patch_dir / name).read_bytes())
    return digest.hexdigest()


def git_output(args: list[str], cwd: Path = ROOT_DIR) -> tuple[int, str, str]:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def resolve_source_ref(source_dir: Path, ref: str) -> tuple[str, str]:
    if not (source_dir / ".git").is_dir():
        return "", "local source cache missing"
    rc, resolved, error = git_output(["git", "-C", str(source_dir), "rev-parse", f"{ref}^{{commit}}"])
    if rc != 0:
        return "", error or f"git rev-parse failed for {ref}"
    return resolved, ""


def sync_source_cache(source_name: str, ref: str, *, reason: str) -> dict[str, Any]:
    env = {
        **os.environ,
        "SOURCE": source_name,
        "SOURCE_NAME": source_name,
        "UPSTREAM_REF": ref,
    }
    result = subprocess.run(
        [str(ROOT_DIR / "scripts" / "sync-upstream.sh")],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "classification": "ok" if result.returncode == 0 else "external source sync failed",
        "reason": reason,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def run_patch_apply(source_name: str, source_dir: Path, ref: str) -> dict[str, Any]:
    if not (source_dir / ".git").is_dir():
        return {
            "status": "skipped",
            "classification": "external check skipped",
            "reason": f"local source cache is missing: {source_dir}",
        }
    target = Path(tempfile.mkdtemp(prefix=f"virtualaxe-drift-{source_name}-"))
    env = {
        "SOURCE_NAME": source_name,
        "SOURCE_DIR": str(source_dir),
        "PATCH_TARGET_DIR": str(target),
        "UPSTREAM_REF": ref,
        "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", "virtualAxe patch check"),
        "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "virtualaxe@example.invalid"),
    }
    result = subprocess.run([str(ROOT_DIR / "scripts" / "apply-patches.sh")], cwd=ROOT_DIR, env={**os.environ, **env}, text=True, capture_output=True, check=False)
    applied = [line.removeprefix("Applying ").strip() for line in result.stdout.splitlines() if line.startswith("Applying ")]
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "classification": "ok" if result.returncode == 0 else "release-blocking drift",
        "targetDir": str(target),
        "returncode": result.returncode,
        "appliedPatches": applied,
        "failedPatch": applied[-1] if result.returncode != 0 and applied else "",
        "stderr": result.stderr,
    }


def unavailable_ref(ref: str, error: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "classification": "maintenance drift",
        "requestedRef": ref,
        "reason": f"upstream ref is not available in the local source cache: {error}",
    }


def manifest_status(source_name: str, series_digest: str) -> dict[str, Any]:
    path = ROOT_DIR / "out" / "manifest.json"
    if not path.exists():
        return {"status": "missing", "classification": "generated artifact missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "failed", "classification": "generated artifact stale/missing", "error": str(exc)}
    stale = []
    if payload.get("patchSeriesSha256") != series_digest:
        stale.append("patchSeriesSha256")
    if payload.get("sourceName") != source_name:
        stale.append("sourceName")
    if payload.get("virtualProfile") != "gamma":
        stale.append("virtualProfile")
    return {
        "status": "stale" if stale else "current",
        "classification": "generated artifact stale/missing" if stale else "ok",
        "path": str(path),
        "staleFields": stale,
    }


def drift_check(include_upstream_head: bool = False) -> dict[str, Any]:
    sources = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    source_name = sources["defaultSource"]
    entry = sources["sources"][source_name]
    ref = entry["ref"]
    source_dir = ROOT_DIR / ".sources" / source_name
    resolved, ref_error = resolve_source_ref(source_dir, ref)
    source_sync = {
        "status": "skipped",
        "classification": "ok",
        "reason": "configured source ref already resolved locally",
    }
    if not resolved:
        source_sync = sync_source_cache(source_name, ref, reason=ref_error)
        if source_sync["status"] == "passed":
            resolved, ref_error = resolve_source_ref(source_dir, ref)
    series_digest = patch_series_hash(source_name)
    if source_sync["status"] == "failed":
        configured_pin = {
            "status": "skipped",
            "classification": "external check skipped",
            "reason": "source sync failed before patch apply",
        }
    else:
        configured_pin = run_patch_apply(source_name, source_dir, ref)
    upstream_head = {"status": "skipped", "classification": "external check skipped", "reason": "run with --upstream-head"}
    if include_upstream_head:
        upstream_ref = "origin/master"
        if source_dir.exists():
            _upstream_rc, upstream_resolved, upstream_error = git_output(
                ["git", "-C", str(source_dir), "rev-parse", f"{upstream_ref}^{{commit}}"]
            )
        else:
            upstream_resolved = ""
            upstream_error = "local source cache missing"
        if upstream_resolved:
            upstream_head = run_patch_apply(source_name, source_dir, upstream_resolved)
        else:
            upstream_head = unavailable_ref(upstream_ref, upstream_error)
        upstream_head["requestedRef"] = upstream_ref
        if upstream_resolved:
            upstream_head["resolvedRef"] = upstream_resolved
        if upstream_head["status"] == "failed":
            upstream_head["classification"] = "maintenance drift"
    local_state = {
        "sourcesExists": source_dir.exists(),
        "worktreesExists": (ROOT_DIR / ".worktrees").exists(),
        "stateExists": (ROOT_DIR / ".state").exists(),
        "outExists": (ROOT_DIR / "out").exists(),
    }
    release_blockers = []
    if configured_pin["classification"] == "release-blocking drift":
        release_blockers.append("configured pin patch stack does not apply")
    if source_sync["status"] == "failed":
        release_blockers.append("configured source sync failed")
    if not resolved:
        release_blockers.append(f"configured source ref is not resolved locally: {ref_error}")
    return {
        "status": "passed" if not release_blockers else "failed",
        "source": source_name,
        "configuredRef": ref,
        "resolvedConfiguredRef": resolved,
        "sourceSync": source_sync,
        "patchSeriesSha256": series_digest,
        "patchCount": len(series(source_name)),
        "configuredPinPatchCheck": configured_pin,
        "upstreamHeadPatchCheck": upstream_head,
        "manifest": manifest_status(source_name, series_digest),
        "localState": local_state,
        "releaseBlockers": release_blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report virtualAxe release drift without changing the release pin.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--upstream-head", action="store_true")
    args = parser.parse_args()
    payload = drift_check(include_upstream_head=args.upstream_head)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"drift-check {payload['status']}: {payload['source']} @ {payload['configuredRef']}")
        for blocker in payload["releaseBlockers"]:
            print(f"release blocker: {blocker}")
        print(f"configured pin patch-check: {payload['configuredPinPatchCheck']['status']}")
        print(f"manifest: {payload['manifest']['status']}")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
