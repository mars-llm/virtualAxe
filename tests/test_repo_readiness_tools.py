import importlib.util
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parent.parent


def load_script(name: str):
    path = ROOT_DIR / "scripts" / name
    module_name = name.replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_secret_scan_reports_location_without_secret_value(tmp_path):
    module = load_script("secret-scan.py")
    path = tmp_path / "sample.txt"
    path.write_text("API_" + "KEY=abcdef1234567890abcdef\n", encoding="utf-8")

    findings = module.scan_file(path)

    assert findings == [{"file": str(path), "line": 1, "kind": "secret-assignment"}]
    assert "abcdef" not in str(findings)


def test_patch_audit_reports_hunk_metadata_and_no_minimization_claim():
    module = load_script("patch-audit.py")
    payload = module.audit()

    assert payload["patchCount"] == 19
    assert payload["hunkMinimized"] is False
    assert {source["source"] for source in payload["sources"]} == {"bitaxe", "nerdnos"}
    assert {source["patchSeries"] for source in payload["sources"]} == {
        "patches/esp-miner/bitaxe/series.txt",
        "patches/esp-miner/nerdnos/series.txt",
    }
    assert all(patch["hunkCount"] > 0 for patch in payload["patches"])
    assert any(patch["patch"].startswith("0048") and "Split into" in patch["recommendation"] for patch in payload["patches"])


def test_local_state_report_is_read_only_and_classifies_known_paths():
    module = load_script("local-state-report.py")
    payload = module.report()
    by_path = {entry["path"]: entry for entry in payload["entries"]}

    assert payload["status"] == "reported"
    assert payload["note"] == "read-only report; no files were removed"
    assert ".sources" in by_path
    assert by_path["out"]["canAffectBuildRunReview"] is True


def test_drift_check_prepares_missing_source_cache(monkeypatch):
    module = load_script("drift-check.py")
    resolved_ref = "c" * 40
    resolve_calls = iter(
        [
            ("", "local source cache missing"),
            (resolved_ref, ""),
        ]
    )
    sync_calls = []

    def fake_resolve(_source_dir, ref):
        assert ref == "64680f8a4da0b9a3b532051f0aa18429fcf04e82"
        return next(resolve_calls)

    def fake_sync(source_name, ref, *, reason):
        sync_calls.append((source_name, ref, reason))
        return {
            "status": "passed",
            "classification": "ok",
            "reason": reason,
            "returncode": 0,
            "stdout": ".sources/bitaxe",
            "stderr": "",
        }

    def fake_patch_apply(source_name, _source_dir, ref):
        return {
            "status": "passed",
            "classification": "ok",
            "source": source_name,
            "requestedRef": ref,
        }

    monkeypatch.setattr(module, "resolve_source_ref", fake_resolve)
    monkeypatch.setattr(module, "sync_source_cache", fake_sync)
    monkeypatch.setattr(module, "run_patch_apply", fake_patch_apply)
    monkeypatch.setattr(module, "manifest_status", lambda _source, _digest: {"status": "missing"})

    payload = module.drift_check()

    assert payload["status"] == "passed"
    assert payload["sourceSync"]["status"] == "passed"
    assert payload["resolvedConfiguredRef"] == resolved_ref
    assert sync_calls == [
        ("bitaxe", "64680f8a4da0b9a3b532051f0aa18429fcf04e82", "local source cache missing")
    ]


def test_drift_check_reports_source_sync_failure(monkeypatch):
    module = load_script("drift-check.py")

    monkeypatch.setattr(
        module,
        "resolve_source_ref",
        lambda _source_dir, _ref: ("", "local source cache missing"),
    )
    monkeypatch.setattr(
        module,
        "sync_source_cache",
        lambda _source, _ref, *, reason: {
            "status": "failed",
            "classification": "external source sync failed",
            "reason": reason,
            "returncode": 128,
            "stdout": "",
            "stderr": "network unavailable",
        },
    )

    def fail_patch_apply(*_args, **_kwargs):
        raise AssertionError("patch apply should not run when source sync fails")

    monkeypatch.setattr(module, "run_patch_apply", fail_patch_apply)
    monkeypatch.setattr(module, "manifest_status", lambda _source, _digest: {"status": "missing"})

    payload = module.drift_check()

    assert payload["status"] == "failed"
    assert payload["sourceSync"]["status"] == "failed"
    assert payload["configuredPinPatchCheck"]["status"] == "skipped"
    assert "configured source sync failed" in payload["releaseBlockers"]
    assert any("configured source ref is not resolved locally" in blocker for blocker in payload["releaseBlockers"])


def test_validate_full_gate_prepares_local_state_and_avoids_live_pools():
    module = load_script("validate.py")

    full = module.checks(lite=False)
    nerdnos = module.checks(lite=False, source_name="nerdnos")
    lite = module.checks(lite=True)
    full_names = [name for name, _command, _gated in full]
    full_commands = [" ".join(command) for _name, command, _gated in full]
    nerdnos_commands = [" ".join(command) for _name, command, _gated in nerdnos]

    assert "bootstrap" not in full_names
    assert "bootstrap" not in [name for name, _command, _gated in lite]
    assert "patch-check" in full_names
    assert "verify-test-ci" in full_names
    assert any("verify-submit-replay" in command for command in full_commands)
    assert any("patch-check SOURCE=bitaxe" in command for command in full_commands)
    assert any("verify-test-ci --source bitaxe" in command for command in full_commands)
    assert any("patch-check SOURCE=nerdnos" in command for command in nerdnos_commands)
    assert any("verify-test-ci --source nerdnos" in command for command in nerdnos_commands)
    assert any("verify-submit-replay SOURCE=nerdnos" in command for command in nerdnos_commands)
    assert not any("verify-release" in command for command in full_commands)
    assert not any("git clean" in command for command in full_commands)


def test_validate_resolves_source_aliases_and_rejects_unknown_sources():
    module = load_script("validate.py")

    assert module.validation_source({}) == "bitaxe"
    assert module.validation_source({"SOURCE": "vanilla"}) == "bitaxe"
    assert module.validation_source({"SOURCE": "nerdnos"}) == "nerdnos"
    with pytest.raises(module.SourceRegistryError, match="Unknown source"):
        module.validation_source({"SOURCE": "missing"})


def test_validate_classifies_setup_failures_as_dependency_issues(monkeypatch):
    module = load_script("validate.py")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "AxeOS frontend dependencies are missing in node_modules, and npm is not available."

    def fake_run(*_args, **_kwargs):
        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.run_check("verify-submit-replay", ["make", "verify-submit-replay"], environment_gated=True)

    assert result["status"] == "failed"
    assert result["classification"] == "failed due to environment/dependency issue"


def run_apply_patches_guard(
    target: str,
    source_dir: Path,
    *,
    home: Path | None = None,
    upstream_ref: str = "HEAD",
    patch_series_file: Path | None = None,
):
    env = os.environ.copy()
    env.update(
        {
            "SOURCE_DIR": str(source_dir),
            "PATCH_TARGET_DIR": target,
            "UPSTREAM_REF": upstream_ref,
            "GIT_COMMITTER_NAME": "virtualAxe test",
            "GIT_COMMITTER_EMAIL": "virtualaxe-test@example.invalid",
        }
    )
    if home is not None:
        env["HOME"] = str(home)
    if patch_series_file is not None:
        env["PATCH_SERIES_FILE"] = str(patch_series_file)
    return subprocess.run(
        ["bash", "scripts/apply-patches.sh"],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_apply_patches_refuses_unsafe_patch_targets(tmp_path):
    source_dir = tmp_path / "source"
    fake_home = tmp_path / "home"
    source_dir.mkdir()
    fake_home.mkdir()

    unsafe_targets = [
        "",
        "/",
        str(ROOT_DIR),
        str(fake_home),
        str(tmp_path / "not-disposable"),
    ]
    for target in unsafe_targets:
        result = run_apply_patches_guard(target, source_dir, home=fake_home)
        assert result.returncode != 0
        assert "Refusing unsafe PATCH_TARGET_DIR" in result.stderr


def test_apply_patches_allows_virtualaxe_temp_targets(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    subprocess.run(["git", "init"], cwd=source_dir, text=True, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "virtualAxe test"], cwd=source_dir, check=True)
    subprocess.run(["git", "config", "user.email", "virtualaxe-test@example.invalid"], cwd=source_dir, check=True)
    (source_dir / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source_dir, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=source_dir, text=True, capture_output=True, check=True)

    target = Path(tempfile.gettempdir()) / f"virtualaxe-guard-test-{os.getpid()}"
    shutil.rmtree(target, ignore_errors=True)
    try:
        result = run_apply_patches_guard(str(target), source_dir)
        assert "Replacing patch target:" in result.stderr
        assert "Refusing unsafe PATCH_TARGET_DIR" not in result.stderr
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_apply_patches_fetches_resolved_commit_not_reachable_from_source_head(tmp_path):
    origin_dir = tmp_path / "origin"
    source_dir = tmp_path / "source"
    subprocess.run(["git", "init", "--initial-branch=master", str(origin_dir)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "virtualAxe test"], cwd=origin_dir, check=True)
    subprocess.run(["git", "config", "user.email", "virtualaxe-test@example.invalid"], cwd=origin_dir, check=True)
    fixture = origin_dir / "README.md"
    fixture.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=origin_dir, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=origin_dir, check=True, capture_output=True)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin_dir, text=True, check=True, capture_output=True
    ).stdout.strip()
    fixture.write_text("head\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "head"], cwd=origin_dir, check=True, capture_output=True)
    head_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin_dir, text=True, check=True, capture_output=True
    ).stdout.strip()

    subprocess.run(["git", "clone", str(origin_dir), str(source_dir)], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "--detach", base_commit], cwd=source_dir, check=True, capture_output=True)
    patch_series_file = tmp_path / "series.txt"
    patch_series_file.write_text("", encoding="utf-8")
    target = Path(tempfile.gettempdir()) / f"virtualaxe-resolved-ref-test-{os.getpid()}"
    shutil.rmtree(target, ignore_errors=True)
    try:
        result = run_apply_patches_guard(
            str(target),
            source_dir,
            upstream_ref=head_commit,
            patch_series_file=patch_series_file,
        )
        assert result.returncode == 0, result.stderr
        checked_out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=target, text=True, check=True, capture_output=True
        ).stdout.strip()
        assert checked_out == head_commit
    finally:
        shutil.rmtree(target, ignore_errors=True)
