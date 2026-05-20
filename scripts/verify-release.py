#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
VIRTUALAXE_CLI = ROOT_DIR / "scripts" / "virtualaxe.py"
RUN_QEMU_NAT = ROOT_DIR / "scripts" / "run-qemu-nat.sh"
BUILD_VIRTUAL = ROOT_DIR / "scripts" / "build-virtual.sh"
WAIT_FOR_HTTP = ROOT_DIR / "scripts" / "wait-for-http.sh"
ENSURE_TEST_PYTHON = ROOT_DIR / "scripts" / "ensure-test-python.sh"
WAIT_FOR_SHARE_DELTA = ROOT_DIR / "scripts" / "wait-for-share-delta.py"
PLAYWRIGHT_CONFIG = ROOT_DIR / "tests" / "browser" / "playwright.config.ts"
PLAYWRIGHT_AXEOS_SPEC = ROOT_DIR / "tests" / "browser" / "axeos.spec.ts"
TESTS_BROWSER_DIR = ROOT_DIR / "tests" / "browser"
DEFAULT_OUT_DIR = ROOT_DIR / "out" / "release-matrix"
DEFAULT_VERIFY_POOL_USER = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
SMOKE_PHASE_TIMEOUT_SECONDS = 120
SMOKE_ESTIMATED_EFFECTIVE_HASHRATE_HPS = 12500.0
SMOKE_TARGET_PROBABILITY = 0.95
PUBLIC_POOL_SMOKE_ASSIGNED_DIFFICULTY = 0.0001
PUBLIC_POOL_SMOKE_PHASE_TIMEOUT_SECONDS = 120
BITRONICS_SMOKE_ASSIGNED_DIFFICULTY = 0.0005
BITRONICS_SMOKE_PHASE_TIMEOUT_SECONDS = 600
NERDMINERS_SMOKE_ASSIGNED_DIFFICULTY = 0.001
NERDMINERS_SMOKE_PHASE_TIMEOUT_SECONDS = 1200
QUALIFICATION_PHASE_TIMEOUT_SECONDS = 4200
QUALIFICATION_MIN_PHASE_DURATION_SECONDS = 0
SMOKE_MIN_ACCEPTED_SHARE_DELTA = 1
QUALIFICATION_MIN_ACCEPTED_SHARE_DELTA = 5
QUALIFICATION_TARGET_COUNT_PROBABILITY = 0.95
QUALIFICATION_NERDMINERS_OBSERVED_ACCEPTED_SHARES = 4
QUALIFICATION_NERDMINERS_OBSERVED_DURATION_SECONDS = 1800
MAX_REJECTED_SHARE_DELTA = 0
POOL_STATS_CAPABILITIES: dict[str, dict[str, Any]] = {
    "ckpool_shares": {
        "worker_bound": True,
        "accepted_share_counter": True,
        "rejected_share_counter": False,
        "supports_delta": True,
        "qualification_capable": True,
        "semantics": "worker-bound accepted-share counters from CKPool-style stats",
    },
    "public_pool_bestdiff": {
        "worker_bound": True,
        "accepted_share_counter": False,
        "rejected_share_counter": False,
        "supports_delta": False,
        "qualification_capable": False,
        "semantics": (
            "worker-scoped best-difficulty and chart-derived diagnostic evidence; "
            "not an accepted-share counter"
        ),
    },
    "bitronics_status_evidence": {
        "worker_bound": False,
        "accepted_share_counter": False,
        "rejected_share_counter": False,
        "supports_delta": False,
        "qualification_capable": False,
        "semantics": (
            "pool status, last-share timestamp, hashrate, and worker-active diagnostics; "
            "not a worker-bound accepted-share counter"
        ),
    },
}
WORKER_POOL_SLUG_BY_PHASE = {
    "public": "pub",
    "bitronics": "bit",
    "nerdminers": "nerd",
}
BITRONICS_POOL_HOME_URL = "https://pool.bitronics.store/"
BITRONICS_STATS_PAGE_TEMPLATE = "https://pool.bitronics.store/stats/{pool_user}"
PUBLIC_POOL_STATS_URL_TEMPLATE = "https://public-pool.io:40557/api/client/{pool_user}"
PUBLIC_POOL_WORKER_STATS_URL_TEMPLATE = "https://public-pool.io:40557/api/client/{pool_user}/{worker}"
BITRONICS_API_TOKEN_PATTERN = re.compile(r"window\.POOL_API_TOKEN\s*=\s*['\"]([^'\"]+)['\"]")


def expected_hashes_per_share(difficulty: float) -> float:
    return difficulty * (2**32)


def share_probability(hashrate_hps: float, difficulty: float, duration_seconds: float) -> float:
    if hashrate_hps <= 0 or difficulty <= 0 or duration_seconds <= 0:
        return 0.0
    return 1.0 - math.exp(-(hashrate_hps * duration_seconds) / expected_hashes_per_share(difficulty))


def poisson_probability_at_least(required_count: int, expected_count: float) -> float:
    if required_count <= 0:
        return 1.0
    if expected_count <= 0:
        return 0.0

    term = math.exp(-expected_count)
    cumulative = term
    for count in range(1, required_count):
        term *= expected_count / count
        cumulative += term
    return max(0.0, min(1.0, 1.0 - cumulative))


def duration_for_share_probability(hashrate_hps: float, difficulty: float, probability: float) -> float:
    if hashrate_hps <= 0 or difficulty <= 0:
        raise ValueError("hashrate and difficulty must be positive")
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be between 0 and 1")
    return -math.log(1.0 - probability) * expected_hashes_per_share(difficulty) / hashrate_hps


def duration_for_count_probability(rate_per_second: float, required_count: int, probability: float) -> float:
    if rate_per_second <= 0:
        raise ValueError("rate must be positive")
    if required_count <= 0:
        return 0.0
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be between 0 and 1")

    low = 0.0
    high = max(float(required_count), 1.0)
    while poisson_probability_at_least(required_count, high) < probability:
        high *= 2.0
    for _ in range(80):
        mid = (low + high) / 2.0
        if poisson_probability_at_least(required_count, mid) >= probability:
            high = mid
        else:
            low = mid
    return high / rate_per_second


def qualification_count_policy() -> dict[str, Any]:
    observed_rate = (
        QUALIFICATION_NERDMINERS_OBSERVED_ACCEPTED_SHARES
        / QUALIFICATION_NERDMINERS_OBSERVED_DURATION_SECONDS
    )
    hash_model_rate = (
        SMOKE_ESTIMATED_EFFECTIVE_HASHRATE_HPS
        / expected_hashes_per_share(NERDMINERS_SMOKE_ASSIGNED_DIFFICULTY)
    )
    observed_target_seconds = duration_for_count_probability(
        observed_rate,
        QUALIFICATION_MIN_ACCEPTED_SHARE_DELTA,
        QUALIFICATION_TARGET_COUNT_PROBABILITY,
    )
    hash_model_target_seconds = duration_for_count_probability(
        hash_model_rate,
        QUALIFICATION_MIN_ACCEPTED_SHARE_DELTA,
        QUALIFICATION_TARGET_COUNT_PROBABILITY,
    )
    return {
        "basis": (
            "NerdNos qualification evidence produced 4 Nerdminers accepted shares in 1800s "
            "at assigned difficulty 0.001; the timeout is sized so the 5-share threshold "
            "is reachable with at least 95% probability under that observed rate."
        ),
        "requiredAcceptedShareDelta": QUALIFICATION_MIN_ACCEPTED_SHARE_DELTA,
        "targetProbability": QUALIFICATION_TARGET_COUNT_PROBABILITY,
        "observedNerdminersAcceptedShares": QUALIFICATION_NERDMINERS_OBSERVED_ACCEPTED_SHARES,
        "observedNerdminersDurationSeconds": QUALIFICATION_NERDMINERS_OBSERVED_DURATION_SECONDS,
        "observedNerdminersRatePerSecond": round(observed_rate, 8),
        "targetProbabilitySecondsAtObservedRate": round(observed_target_seconds, 2),
        "targetProbabilitySecondsAtHashModel": round(hash_model_target_seconds, 2),
        "configuredTimeoutSeconds": QUALIFICATION_PHASE_TIMEOUT_SECONDS,
        "configuredTimeoutProbabilityAtObservedRate": round(
            poisson_probability_at_least(
                QUALIFICATION_MIN_ACCEPTED_SHARE_DELTA,
                observed_rate * QUALIFICATION_PHASE_TIMEOUT_SECONDS,
            ),
            4,
        ),
        "configuredTimeoutProbabilityAtHashModel": round(
            poisson_probability_at_least(
                QUALIFICATION_MIN_ACCEPTED_SHARE_DELTA,
                hash_model_rate * QUALIFICATION_PHASE_TIMEOUT_SECONDS,
            ),
            4,
        ),
    }


def smoke_feasibility(label: str, assigned_difficulty: float, configured_timeout_seconds: int) -> dict[str, Any]:
    required_seconds = duration_for_share_probability(
        SMOKE_ESTIMATED_EFFECTIVE_HASHRATE_HPS,
        assigned_difficulty,
        SMOKE_TARGET_PROBABILITY,
    )
    return {
        "basis": (
            "NerdNos live evidence measured roughly 12.5 KH/s effective guest hash attempts "
            f"after version rolling while {label} assigned difficulty {assigned_difficulty}."
        ),
        "estimatedHashrateHps": SMOKE_ESTIMATED_EFFECTIVE_HASHRATE_HPS,
        "assignedDifficulty": assigned_difficulty,
        "targetProbability": SMOKE_TARGET_PROBABILITY,
        "targetProbabilitySeconds": round(required_seconds, 2),
        "configuredTimeoutSeconds": configured_timeout_seconds,
        "configuredTimeoutProbability": round(
            share_probability(
                SMOKE_ESTIMATED_EFFECTIVE_HASHRATE_HPS,
                assigned_difficulty,
                configured_timeout_seconds,
            ),
            4,
        ),
    }


def public_pool_smoke_feasibility() -> dict[str, Any]:
    return smoke_feasibility("PublicPool", PUBLIC_POOL_SMOKE_ASSIGNED_DIFFICULTY, PUBLIC_POOL_SMOKE_PHASE_TIMEOUT_SECONDS)


def bitronics_smoke_feasibility() -> dict[str, Any]:
    return smoke_feasibility("Bitronics", BITRONICS_SMOKE_ASSIGNED_DIFFICULTY, BITRONICS_SMOKE_PHASE_TIMEOUT_SECONDS)


def nerdminers_smoke_feasibility() -> dict[str, Any]:
    return smoke_feasibility("Nerdminers", NERDMINERS_SMOKE_ASSIGNED_DIFFICULTY, NERDMINERS_SMOKE_PHASE_TIMEOUT_SECONDS)

PHASES = (
    {
        "name": "01-primary",
        "slug": "public",
        "label": "PublicPool",
        "host": "public-pool.io",
        "port": 3333,
        "difficulty": 0.0001,
        "subscribe_agent": "",
        "required_for_pass": True,
        "require_accepted_share": True,
        "require_accepted_log": False,
        "require_local_diff_at_pool_difficulty": True,
        "pool_stats_kind": "public_pool_bestdiff",
        "pool_stats_url_template": PUBLIC_POOL_STATS_URL_TEMPLATE,
        "pool_stats_page_url_template": PUBLIC_POOL_WORKER_STATS_URL_TEMPLATE,
        "smoke_phase_timeout_seconds": PUBLIC_POOL_SMOKE_PHASE_TIMEOUT_SECONDS,
        "smoke_feasibility": public_pool_smoke_feasibility,
    },
    {
        "name": "02-secondary",
        "slug": "bitronics",
        "label": "Bitronics",
        "host": "pool.bitronics.store",
        "port": 3334,
        "difficulty": 0.0001,
        "subscribe_agent": "NerdMinerV2/virtualAxe-gamma",
        "required_for_pass": True,
        "require_accepted_share": True,
        "require_accepted_log": False,
        "require_local_diff_at_pool_difficulty": True,
        "pool_stats_kind": "bitronics_status_evidence",
        "pool_stats_url_template": "https://pool.bitronics.store/api/stats/{pool_user}",
        "pool_stats_page_url_template": BITRONICS_STATS_PAGE_TEMPLATE,
        "pool_stats_auth": "bitronics",
        "smoke_phase_timeout_seconds": BITRONICS_SMOKE_PHASE_TIMEOUT_SECONDS,
        "smoke_feasibility": bitronics_smoke_feasibility,
    },
    {
        "name": "03-tertiary",
        "slug": "nerdminers",
        "label": "Nerdminers",
        "host": "pool.nerdminers.org",
        "port": 3333,
        "difficulty": 0.0005,
        "subscribe_agent": "NerdMinerV2/virtualAxe-gamma",
        "required_for_pass": True,
        "require_accepted_share": True,
        "require_accepted_log": False,
        "require_local_diff_at_pool_difficulty": True,
        "pool_stats_url_template": "https://pool.nerdminers.org/users/{pool_user}",
        "pool_stats_kind": "ckpool_shares",
        "smoke_phase_timeout_seconds": NERDMINERS_SMOKE_PHASE_TIMEOUT_SECONDS,
        "smoke_feasibility": nerdminers_smoke_feasibility,
    },
)


def phase_requirements(mode: str) -> dict[str, Any]:
    if mode == "qualification":
        return {
            "phase_timeout_seconds": QUALIFICATION_PHASE_TIMEOUT_SECONDS,
            "min_accepted_share_delta": QUALIFICATION_MIN_ACCEPTED_SHARE_DELTA,
            "require_pool_side_accepted_share": True,
            "require_pool_stats_accepted_share": False,
            "min_duration_seconds": QUALIFICATION_MIN_PHASE_DURATION_SECONDS,
            "max_rejected_share_delta": MAX_REJECTED_SHARE_DELTA,
        }
    return {
        "phase_timeout_seconds": SMOKE_PHASE_TIMEOUT_SECONDS,
        "min_accepted_share_delta": SMOKE_MIN_ACCEPTED_SHARE_DELTA,
        "require_pool_side_accepted_share": False,
        "require_pool_stats_accepted_share": False,
        "min_duration_seconds": 0,
        "max_rejected_share_delta": MAX_REJECTED_SHARE_DELTA,
    }


def phases_for_mode(mode: str) -> tuple[dict[str, Any], ...]:
    requirements = phase_requirements(mode)
    phases: list[dict[str, Any]] = []
    for phase in PHASES:
        configured = {**phase, **requirements}
        if callable(configured.get("smoke_feasibility")):
            configured["smoke_feasibility"] = configured["smoke_feasibility"]()
        if mode == "smoke":
            configured["phase_timeout_seconds"] = phase.get(
                "smoke_phase_timeout_seconds",
                requirements["phase_timeout_seconds"],
            )
        configured.update(pool_stats_capability_fields(str(configured.get("pool_stats_kind", ""))))
        phases.append(configured)
    return tuple(phases)


def pool_stats_capability(kind: str) -> dict[str, Any]:
    capability = POOL_STATS_CAPABILITIES.get(kind)
    if capability is None:
        return {
            "worker_bound": False,
            "accepted_share_counter": False,
            "rejected_share_counter": False,
            "supports_delta": False,
            "qualification_capable": False,
            "semantics": f"{kind or 'missing'} does not expose a worker-bound accepted-share counter",
        }
    return dict(capability)


def pool_stats_capability_fields(kind: str) -> dict[str, Any]:
    capability = pool_stats_capability(kind)
    return {
        "pool_stats_worker_bound": bool(capability["worker_bound"]),
        "pool_stats_accepted_share_counter": bool(capability["accepted_share_counter"]),
        "pool_stats_rejected_share_counter": bool(capability["rejected_share_counter"]),
        "pool_stats_supports_delta": bool(capability["supports_delta"]),
        "pool_stats_qualification_capable": bool(capability["qualification_capable"]),
        "pool_stats_qualification_capability": str(capability["semantics"]),
    }


def release_policy_text(mode: str) -> str:
    if mode == "qualification":
        return (
            "PublicPool + Bitronics + Nerdminers required; each phase proves at least "
            "5 pool-side accepted shares through direct live Stratum "
            "acceptance or worker-bound pool stats; timeout is sized from the observed "
            "Nerdminers 0.001-difficulty accepted-share rate"
        )
    return "PublicPool + Bitronics + Nerdminers required; smoke waits for one accepted share per pool with explicit probability-based per-pool timeouts"


def load_virtualaxe_module():
    spec = importlib.util.spec_from_file_location("virtualaxe_release_module", VIRTUALAXE_CLI)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Unable to load {VIRTUALAXE_CLI}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the gamma remote-pool virtualAxe verification suite.")
    parser.add_argument("--source", default="bitaxe")
    parser.add_argument("--pool-user", default=os.environ.get("VERIFY_POOL_USER", DEFAULT_VERIFY_POOL_USER))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--run-id")
    parser.add_argument("--http-port", type=int, default=18080)
    parser.add_argument("--mode", choices=("smoke", "qualification"), default="smoke")
    parser.add_argument("--qualification", action="store_true", help="Run the strict release-prep qualification mode.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.qualification:
        args.mode = "qualification"
    return args


def run_command(
    cmd: list[str],
    *,
    cwd: Path = ROOT_DIR,
    capture: bool = True,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    stream_stderr: bool = False,
) -> subprocess.CompletedProcess[str]:
    if capture and stream_stderr:
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def collect_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                stdout_chunks.append(line)

        def collect_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_chunks.append(line)
                sys.stderr.write(line)
                sys.stderr.flush()

        stdout_thread = threading.Thread(target=collect_stdout)
        stderr_thread = threading.Thread(target=collect_stderr)
        stdout_thread.start()
        stderr_thread.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            stdout_thread.join()
            stderr_thread.join()
            raise
        stdout_thread.join()
        stderr_thread.join()
        return subprocess.CompletedProcess(
            cmd,
            returncode,
            "".join(stdout_chunks),
            "".join(stderr_chunks),
        )
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=capture,
        check=False,
        timeout=timeout,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_log_excerpt(source_path: Path, start_offset: int, output_path: Path) -> None:
    if not source_path.is_file():
        return
    if start_offset <= 0:
        shutil.copy2(source_path, output_path)
        return

    with source_path.open("rb") as handle:
        handle.seek(start_offset)
        content = handle.read()
    output_path.write_bytes(content)


def worker_run_token(run_id: str) -> str:
    digest = hashlib.sha1(run_id.encode("utf-8")).hexdigest()
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    suffix = "".join(alphabet[int(digest[index : index + 2], 16) % len(alphabet)] for index in range(0, 8, 2))
    return f"r{suffix}"


def worker_name(pool_user: str, profile: str, phase_slug: str, run_id: str) -> str:
    pool_slug = WORKER_POOL_SLUG_BY_PHASE.get(phase_slug, phase_slug)
    return f"{pool_user}.va{profile[0]}{pool_slug}{worker_run_token(run_id)}"


def runtime_active(out_dir: Path) -> bool:
    return (out_dir / "qemu.pid").is_file() or (out_dir / "qemu.cid").is_file()


def stop_runtime(env: dict[str, str]) -> None:
    run_command([str(RUN_QEMU_NAT), "--stop"], env=env, capture=True)


def fetch_system_info(base_url: str, *, timeout: float = 10.0, retries: int = 15) -> dict[str, Any]:
    info_url = f"{base_url.rstrip('/')}/api/system/info"
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(info_url, timeout=timeout) as response:
                return json.load(response)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Unable to read {info_url}: {last_error}")


def phase_pool_stats_url(phase: dict[str, Any], pool_user: str) -> str:
    template = str(phase.get("pool_stats_url_template", "") or "")
    return template.format(pool_user=pool_user) if template else ""


def pool_stats_display_worker(worker_name: str) -> str:
    return worker_name.rsplit(".", 1)[1] if "." in worker_name else worker_name


def phase_pool_stats_page_url(phase: dict[str, Any], pool_user: str, worker_name: str) -> str:
    template = str(phase.get("pool_stats_page_url_template", "") or "")
    return template.format(pool_user=pool_user, worker=pool_stats_display_worker(worker_name)) if template else ""


def fetch_text(url: str, *, headers: dict[str, str] | None = None, timeout: float = 15.0) -> str:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def bitronics_pool_headers() -> dict[str, str]:
    home = fetch_text(BITRONICS_POOL_HOME_URL)
    match = BITRONICS_API_TOKEN_PATTERN.search(home)
    if not match:
        raise RuntimeError("Unable to discover Bitronics public stats token")
    return {
        "X-Pool-Request": "internal",
        "X-Pool-Token": match.group(1),
    }


def fetch_pool_stats_json(pool_stats_url: str, auth: str = "") -> dict[str, Any]:
    headers = bitronics_pool_headers() if auth == "bitronics" else {}
    request = urllib.request.Request(pool_stats_url, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def pool_stats_worker(payload: dict[str, Any], worker_name: str) -> dict[str, Any] | None:
    for worker in payload.get("worker", []) or []:
        if worker.get("workername") == worker_name:
            return worker
    return None


def bitronics_pool_snapshot(payload: dict[str, Any], worker_name: str) -> dict[str, Any]:
    data = payload.get("data", {}) or {}
    pools = data.get("pools", []) or []
    pool = next((entry for entry in pools if entry.get("pool") == "nerd"), {})
    return {
        "worker": worker_name,
        "shares": 0.0,
        "lastshare": pool.get("lastShare"),
        "hashrate1m": pool.get("hashrate1m", 0),
        "workers": pool.get("workers", 0),
        "bestshare": pool.get("bestShare"),
        "workerStats": pool,
    }


def public_pool_best_difficulty(worker: dict[str, Any] | None) -> float:
    if not worker:
        return 0.0
    try:
        return float(worker.get("bestDifficulty", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def public_pool_worker(payload: dict[str, Any], worker_name: str) -> dict[str, Any] | None:
    display_worker = pool_stats_display_worker(worker_name)
    workers = [
        worker
        for worker in payload.get("workers", []) or []
        if str(worker.get("name", "")) in {worker_name, display_worker}
    ]
    if not workers:
        return None
    return max(workers, key=public_pool_best_difficulty)


def public_pool_snapshot(payload: dict[str, Any], worker_name: str) -> dict[str, Any]:
    worker = public_pool_worker(payload, worker_name) or {}
    return {
        "worker": worker_name,
        "shares": 0.0,
        "lastshare": worker.get("lastSeen"),
        "workers": payload.get("workersCount", 0),
        "bestDifficulty": public_pool_best_difficulty(worker),
        "workerStats": worker,
    }


def pool_stats_worker_snapshot(
    pool_stats_url: str,
    worker_name: str,
    *,
    kind: str = "ckpool_shares",
    auth: str = "",
) -> dict[str, Any]:
    payload = fetch_pool_stats_json(pool_stats_url, auth)
    if kind == "bitronics_status_evidence":
        snapshot = bitronics_pool_snapshot(payload, worker_name)
        snapshot["url"] = pool_stats_url
        return snapshot
    if kind == "public_pool_bestdiff":
        snapshot = public_pool_snapshot(payload, worker_name)
        snapshot["url"] = pool_stats_url
        return snapshot

    worker = pool_stats_worker(payload, worker_name) or {}
    shares = 0.0
    try:
        shares = float(worker.get("shares", 0) or 0)
    except (TypeError, ValueError):
        shares = 0.0
    return {
        "url": pool_stats_url,
        "worker": worker_name,
        "shares": shares,
        "lastshare": worker.get("lastshare"),
        "workerStats": worker,
    }


def capture_pool_stats_before(
    phase_dir: Path,
    pool_stats_url: str,
    worker_name: str,
    phase: dict[str, Any],
) -> tuple[float | None, float | None, str | None]:
    try:
        snapshot = pool_stats_worker_snapshot(
            pool_stats_url,
            worker_name,
            kind=str(phase.get("pool_stats_kind", "ckpool_shares")),
            auth=str(phase.get("pool_stats_auth", "")),
        )
    except Exception as exc:  # noqa: BLE001
        write_json(phase_dir / "pool-stats-before.json", {"error": str(exc), "url": pool_stats_url, "worker": worker_name})
        return None, None, None

    write_json(phase_dir / "pool-stats-before.json", snapshot)
    return (
        float(snapshot["shares"]),
        float(snapshot.get("bestDifficulty", 0) or 0),
        snapshot.get("lastshare"),
    )


def phase_primary_settings(phase: dict[str, Any], worker: str, source: str = "bitaxe") -> dict[str, Any]:
    settings = {
        "stratumURL": phase["host"],
        "stratumPort": int(phase["port"]),
        "stratumUser": worker,
    }
    if source == "nerdnos":
        settings["stratumDifficulty"] = float(phase["difficulty"])
    else:
        settings["stratumSubscribeAgent"] = phase.get("subscribe_agent", "")
        settings["stratumSuggestedDifficulty"] = float(phase["difficulty"])
    return settings


def setting_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, float):
        try:
            actual_float = float(actual)
        except (TypeError, ValueError):
            return False
        return abs(actual_float - expected) <= max(1e-6, abs(expected) * 1e-5)
    return actual == expected


def settings_match(payload: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(setting_matches(payload.get(key), value) for key, value in expected.items())


def compact_settings(payload: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: payload.get(key) for key in keys}


def ensure_browser_dependencies() -> None:
    if (TESTS_BROWSER_DIR / "node_modules" / "@playwright" / "test").is_dir():
        return
    if (TESTS_BROWSER_DIR / "package-lock.json").is_file():
        result = run_command(["npm", "ci", "--prefix", str(TESTS_BROWSER_DIR)], capture=True)
    else:
        result = run_command(["npm", "install", "--prefix", str(TESTS_BROWSER_DIR)], capture=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Unable to install browser test dependencies")


def phase_runtime_paths(profile_dir: Path, phase: dict[str, Any]) -> tuple[Path, Path]:
    phase_root = profile_dir / "runtime" / phase["name"]
    return phase_root / "out", phase_root / "state"


def build_release_env(
    args: argparse.Namespace,
    phase: dict[str, Any],
    run_id: str,
    *,
    out_dir: Path | None = None,
    state_dir: Path | None = None,
) -> tuple[Any, dict[str, str], dict[str, Any]]:
    virtualaxe = load_virtualaxe_module()
    sources = virtualaxe.load_sources()
    if args.source not in sources["sources"]:
        raise SystemExit(f"Unknown source {args.source!r}")
    profile = virtualaxe.load_profile("gamma")
    source_dir, resolved_entry = virtualaxe.ensure_git_source(args.source, sources["sources"][args.source])
    probe = virtualaxe.source_probe(source_dir)
    virtualaxe.require_shared_virtual_patch_support(args.source, probe)
    worktree = virtualaxe.prepare_worktree(args.source, source_dir, resolved_entry)

    build_args = argparse.Namespace(
        out_dir=out_dir,
        state_dir=state_dir,
        json=False,
        pool_host=phase["host"],
        pool_port=phase["port"],
        pool_user=worker_name(args.pool_user, profile["id"], phase["slug"], run_id),
        pool_pass="x",
        pool_diff=phase["difficulty"],
        pool_tls=virtualaxe.DEFAULT_POOL_TLS,
        pool_cert=virtualaxe.DEFAULT_POOL_CERT,
        pool_subscribe_agent=phase.get("subscribe_agent", ""),
        hostname=virtualaxe.DEFAULT_HOSTNAME,
        virtual_asic_mode=virtualaxe.DEFAULT_VIRTUAL_ASIC_MODE,
        http_port=args.http_port,
        reset_persisted_state=True,
    )
    env = virtualaxe.build_env(build_args, args.source, worktree, profile)
    env["BASE_URL"] = f"http://127.0.0.1:{args.http_port}"
    env["BACKGROUND"] = "1"
    env["VIRTUALAXE_DISABLE_TEE"] = "1"
    return virtualaxe, env, profile


def start_runtime(env: dict[str, str]) -> None:
    result = run_command([str(RUN_QEMU_NAT)], env=env, capture=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Unable to start QEMU runtime")
    wait_for_runtime(env)


def wait_for_runtime(env: dict[str, str]) -> None:
    wait = run_command([str(WAIT_FOR_HTTP)], env=env, capture=True)
    if wait.returncode != 0:
        raise RuntimeError(wait.stderr.strip() or wait.stdout.strip() or "Timed out waiting for the Bitaxe API")


def ensure_runtime_dependencies(env: dict[str, str]) -> None:
    result = run_command([str(ENSURE_TEST_PYTHON)], env=env, capture=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Unable to provision the release test Python environment")
    ensure_browser_dependencies()


def run_api_smoke(base_url: str, phase_dir: Path, source_name: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["BASE_URL"] = base_url
    env["SOURCE_NAME"] = source_name
    result = run_command(
        [
            "uv",
            "run",
            "pytest",
            "-q",
            str(ROOT_DIR / "tests" / "api" / "test_system_info.py"),
            str(ROOT_DIR / "tests" / "api" / "test_dashboard.py"),
        ],
        env=env,
        capture=True,
    )
    payload = {
        "status": "passed" if result.returncode == 0 else "failed",
        "command": result.args,
        "returncode": result.returncode,
        "stdoutPath": str(phase_dir / "api-smoke.stdout.log"),
        "stderrPath": str(phase_dir / "api-smoke.stderr.log"),
    }
    (phase_dir / "api-smoke.stdout.log").write_text(result.stdout or "", encoding="utf-8")
    (phase_dir / "api-smoke.stderr.log").write_text(result.stderr or "", encoding="utf-8")
    return payload


def run_browser_smoke(base_url: str, phase_dir: Path, source_name: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["BASE_URL"] = base_url
    env["SOURCE_NAME"] = source_name
    result = run_command(
        [
            "npm",
            "--prefix",
            str(TESTS_BROWSER_DIR),
            "exec",
            "--",
            "playwright",
            "test",
            "--config",
            str(PLAYWRIGHT_CONFIG),
            str(PLAYWRIGHT_AXEOS_SPEC),
            "--grep",
            "axeos dashboard loads",
        ],
        env=env,
        capture=True,
    )
    payload = {
        "status": "passed" if result.returncode == 0 else "failed",
        "command": result.args,
        "returncode": result.returncode,
        "stdoutPath": str(phase_dir / "browser-smoke.stdout.log"),
        "stderrPath": str(phase_dir / "browser-smoke.stderr.log"),
    }
    (phase_dir / "browser-smoke.stdout.log").write_text(result.stdout or "", encoding="utf-8")
    (phase_dir / "browser-smoke.stderr.log").write_text(result.stderr or "", encoding="utf-8")
    return payload


def run_pool_connectivity_smoke(base_url: str, phase: dict[str, Any], phase_dir: Path, source_name: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["BASE_URL"] = base_url
    env["SOURCE_NAME"] = source_name
    env["REAL_POOL_HOST"] = phase["host"]
    env["REAL_POOL_PORT"] = str(phase["port"])
    result = run_command(
        [
            "uv",
            "run",
            "pytest",
            "-q",
            str(ROOT_DIR / "tests" / "api" / "test_real_pool_connectivity.py"),
        ],
        env=env,
        capture=True,
    )
    payload = {
        "status": "passed" if result.returncode == 0 else "failed",
        "command": result.args,
        "returncode": result.returncode,
        "stdoutPath": str(phase_dir / "pool-smoke.stdout.log"),
        "stderrPath": str(phase_dir / "pool-smoke.stderr.log"),
    }
    (phase_dir / "pool-smoke.stdout.log").write_text(result.stdout or "", encoding="utf-8")
    (phase_dir / "pool-smoke.stderr.log").write_text(result.stderr or "", encoding="utf-8")
    return payload


def run_wait_for_phase(
    base_url: str,
    phase: dict[str, Any],
    baseline_shares: int,
    baseline_rejected: int,
    qemu_log: Path,
    qemu_log_start: int,
    source_name: str,
    pool_stats_url: str = "",
    pool_stats_page_url: str = "",
    pool_stats_worker_name: str = "",
    pool_stats_baseline_shares: float | None = None,
    pool_stats_baseline_best_difficulty: float | None = None,
    pool_stats_baseline_last_share: str | None = None,
) -> subprocess.CompletedProcess[str]:
    min_delta = int(phase.get("min_accepted_share_delta", 1))
    require_pool_side = bool(phase.get("require_pool_side_accepted_share", False))
    command = [
        sys.executable,
        str(WAIT_FOR_SHARE_DELTA),
        "--base-url",
        base_url,
        "--timeout",
        str(phase.get("phase_timeout_seconds", SMOKE_PHASE_TIMEOUT_SECONDS)),
        "--min-duration-seconds",
        str(phase.get("min_duration_seconds", 0)),
        "--baseline-rejected",
        str(baseline_rejected),
        "--max-rejected-delta",
        str(phase.get("max_rejected_share_delta", MAX_REJECTED_SHARE_DELTA)),
        "--expected-pool-host",
        phase["host"],
        "--expected-pool-port",
        str(phase["port"]),
        "--expected-pool-worker",
        pool_stats_worker_name,
        "--qemu-log",
        str(qemu_log),
        "--qemu-log-start",
        str(qemu_log_start),
    ]
    if source_name != "nerdnos":
        command.extend(
            [
                "--min-worker-count",
                "1",
                "--require-worker-jobs",
            ]
        )
    if phase["require_accepted_share"]:
        command.extend(
            [
                "--require-accepted-share",
                "--min-shares",
                str(baseline_shares + min_delta),
            ]
        )
    else:
        command.append("--no-require-accepted-share")
    if phase["require_accepted_log"]:
        command.append("--require-accepted-log")
    if phase["require_local_diff_at_pool_difficulty"]:
        command.append("--require-local-diff-at-least-pool-difficulty")
    if pool_stats_url:
        command.extend(
            [
                "--pool-stats-url",
                pool_stats_url,
                "--pool-stats-kind",
                str(phase.get("pool_stats_kind", "ckpool_shares")),
                "--pool-stats-auth",
                str(phase.get("pool_stats_auth", "")),
                "--pool-stats-worker",
                pool_stats_worker_name,
                "--pool-stats-baseline-shares",
                str(pool_stats_baseline_shares or 0.0),
                "--pool-stats-baseline-best-difficulty",
                str(pool_stats_baseline_best_difficulty or 0.0),
            ]
        )
        if require_pool_side:
            command.extend(["--pool-stats-min-delta", str(min_delta)])
        if pool_stats_page_url:
            command.extend(["--pool-stats-page-url", pool_stats_page_url])
        if pool_stats_baseline_last_share:
            command.extend(["--pool-stats-baseline-last-share", pool_stats_baseline_last_share])
    return run_command(command, capture=True, stream_stderr=True)


def phase_status(
    phase: dict[str, Any],
    session_established: bool,
    accepted_share_delta: int,
    phase_duration_seconds: float,
    rejected_share_delta: int,
    qualification_accepted_share_delta: int | None = None,
) -> str:
    min_delta = int(phase.get("min_accepted_share_delta", 1))
    min_duration = float(phase.get("min_duration_seconds", 0) or 0)
    max_rejected_delta = int(phase.get("max_rejected_share_delta", MAX_REJECTED_SHARE_DELTA))
    if phase.get("require_pool_side_accepted_share", False):
        if qualification_accepted_share_delta is None:
            qualification_accepted_share_delta = 0
        accepted_ok = qualification_accepted_share_delta >= min_delta
    else:
        accepted_ok = not phase["require_accepted_share"] or accepted_share_delta >= min_delta
    duration_ok = phase_duration_seconds >= min_duration
    rejected_ok = rejected_share_delta <= max_rejected_delta
    if phase["require_accepted_share"]:
        return "PASSED" if session_established and accepted_ok and duration_ok and rejected_ok else "FAILED"
    return "EVIDENCED" if session_established and duration_ok and rejected_ok else "FAILED"


def summarize_phase(
    phase: dict[str, Any],
    phase_dir: Path,
    runtime_out_dir: Path,
    runtime_state_dir: Path,
    wait_payload: dict[str, Any] | None,
    before_payload: dict[str, Any] | None,
    after_payload: dict[str, Any] | None,
    *,
    phase_error: str,
) -> dict[str, Any]:
    before_payload = before_payload or {}
    after_payload = after_payload or {}
    workers = after_payload.get("virtualAsicWorkers", [])
    if not workers and wait_payload:
        workers = wait_payload.get("virtualAsicWorkers", [])
    shares_before = int(before_payload.get("sharesAccepted", 0) or 0)
    shares_after = int(after_payload.get("sharesAccepted", 0) or 0)
    local_accepted_share_delta = max(0, shares_after - shares_before)
    pool_stats_accepted_share_delta = 0
    if wait_payload and wait_payload.get("poolStatsAccepted") and wait_payload.get("poolStatsProofKind") != "bitronics_status_evidence":
        pool_stats_accepted_share_delta = int(float(wait_payload.get("poolStatsAcceptedShareDelta", 0) or 0))
    qemu_accepted_share_delta = int(float(wait_payload.get("qemuAcceptedShareDelta", 0) or 0)) if wait_payload else 0
    pool_stratum_accepted_share_delta = int(float(wait_payload.get("poolStratumAcceptedShareDelta", 0) or 0)) if wait_payload else 0
    diagnostic_accepted_share_delta = max(local_accepted_share_delta, pool_stats_accepted_share_delta, qemu_accepted_share_delta)
    require_pool_side = bool(phase.get("require_pool_side_accepted_share", False))
    required_accepted_share_delta = int(phase.get("min_accepted_share_delta", 1))
    qualification_accepted_share_delta = (
        max(pool_stratum_accepted_share_delta, pool_stats_accepted_share_delta)
        if require_pool_side
        else diagnostic_accepted_share_delta
    )
    qualification_proof_sources = []
    if pool_stratum_accepted_share_delta >= required_accepted_share_delta:
        qualification_proof_sources.append("pool_stratum_response")
    if pool_stats_accepted_share_delta >= required_accepted_share_delta:
        qualification_proof_sources.append("pool_stats")
    qualification_proof_source = qualification_proof_sources[0] if require_pool_side and qualification_proof_sources else ""
    rejected_before = int(before_payload.get("sharesRejected", 0) or 0)
    rejected_after = int(after_payload.get("sharesRejected", 0) or 0)
    rejected_share_delta = max(0, rejected_after - rejected_before)
    phase_duration_seconds = float(wait_payload.get("durationSeconds", 0) or 0) if wait_payload else 0.0
    session_established = bool(wait_payload)
    status = phase_status(
        phase,
        session_established,
        diagnostic_accepted_share_delta,
        phase_duration_seconds,
        rejected_share_delta,
        qualification_accepted_share_delta,
    )
    assigned_pool_difficulty = after_payload.get("poolDifficulty")
    if assigned_pool_difficulty is None and wait_payload:
        assigned_pool_difficulty = wait_payload.get("poolDifficulty")

    return {
        "phase": phase["name"],
        "label": phase["label"],
        "slug": phase["slug"],
        "requiredForPass": phase["required_for_pass"],
        "acceptedShareRequired": phase["require_accepted_share"],
        "phaseStatus": status,
        "sessionEstablished": session_established,
        "poolHost": after_payload.get("stratumURL") or before_payload.get("stratumURL") or phase["host"],
        "poolPort": after_payload.get("stratumPort") or before_payload.get("stratumPort") or phase["port"],
        "subscribeAgent": after_payload.get("stratumSubscribeAgent") or before_payload.get("stratumSubscribeAgent") or phase.get("subscribe_agent", ""),
        "assignedPoolDifficulty": assigned_pool_difficulty,
        "workerCount": len(workers),
        "jobsAssigned": [int(worker.get("jobsAssigned", 0) or 0) for worker in workers],
        "sharesAcceptedBefore": shares_before,
        "sharesAcceptedAfter": shares_after,
        "localAcceptedShareDelta": local_accepted_share_delta,
        "qemuAcceptedShareDelta": qemu_accepted_share_delta,
        "poolStratumAcceptedShareDelta": pool_stratum_accepted_share_delta,
        "diagnosticAcceptedShareDelta": diagnostic_accepted_share_delta,
        "acceptedShareDelta": diagnostic_accepted_share_delta,
        "acceptedShareProofSource": wait_payload.get("acceptedShareProofSource") if wait_payload else "",
        "acceptedShareProofSources": wait_payload.get("acceptedShareProofSources", []) if wait_payload else [],
        "evidenceTransport": wait_payload.get("evidenceTransport") or wait_payload.get("poolStratumEvidenceTransport", "") if wait_payload else "",
        "qualificationAcceptedShareDelta": qualification_accepted_share_delta,
        "qualificationProofSource": qualification_proof_source,
        "qualificationProofSources": qualification_proof_sources if require_pool_side else [],
        "qualificationPoolSideRequired": require_pool_side,
        "qualificationPoolStatsRequired": bool(phase.get("require_pool_stats_accepted_share", False)),
        "requiredAcceptedShareDelta": required_accepted_share_delta,
        "sharesRejectedBefore": rejected_before,
        "sharesRejectedAfter": rejected_after,
        "rejectedShareDelta": rejected_share_delta,
        "maxRejectedShareDelta": int(phase.get("max_rejected_share_delta", MAX_REJECTED_SHARE_DELTA)),
        "phaseDurationSeconds": phase_duration_seconds,
        "requiredDurationSeconds": float(phase.get("min_duration_seconds", 0) or 0),
        "phaseTimeoutSeconds": float(phase.get("phase_timeout_seconds", SMOKE_PHASE_TIMEOUT_SECONDS)),
        "smokeFeasibility": phase.get("smoke_feasibility", {}),
        "observedLocalDiff": wait_payload.get("observedLocalDiff") if wait_payload else None,
        "provenLocalDiffLowerBound": wait_payload.get("provenLocalDiffLowerBound") if wait_payload else None,
        "localDiffSatisfiedByAcceptance": wait_payload.get("localDiffSatisfiedByAcceptance") if wait_payload else False,
        "acceptedLogSeen": wait_payload.get("acceptedLogSeen") if wait_payload else False,
        "qemuPoolIdentity": wait_payload.get("qemuPoolIdentity") if wait_payload else False,
        "qemuWorkerIdentity": wait_payload.get("qemuWorkerIdentity") if wait_payload else False,
        "qemuSubmitSeen": wait_payload.get("qemuSubmitSeen") if wait_payload else False,
        "qemuAcceptedShare": wait_payload.get("qemuAcceptedShare") if wait_payload else False,
        "poolStatsURL": wait_payload.get("poolStatsURL") if wait_payload else phase.get("pool_stats_url_template", ""),
        "poolStatsWorker": wait_payload.get("poolStatsWorker") if wait_payload else "",
        "poolStatsSharesBefore": wait_payload.get("poolStatsSharesBefore") if wait_payload else None,
        "poolStatsSharesAfter": wait_payload.get("poolStatsSharesAfter") if wait_payload else None,
        "poolStatsBestDifficultyBefore": wait_payload.get("poolStatsBestDifficultyBefore") if wait_payload else None,
        "poolStatsBestDifficultyAfter": wait_payload.get("poolStatsBestDifficultyAfter") if wait_payload else None,
        "poolStatsLastShareBefore": wait_payload.get("poolStatsLastShareBefore") if wait_payload else None,
        "poolStatsLastShareAfter": wait_payload.get("poolStatsLastShareAfter") if wait_payload else None,
        "poolStatsWorkerActive": wait_payload.get("poolStatsWorkerActive") if wait_payload else False,
        "poolStatsProofKind": wait_payload.get("poolStatsProofKind") if wait_payload else phase.get("pool_stats_kind", ""),
        "poolStatsWorkerBound": bool(phase.get("pool_stats_worker_bound", False)),
        "poolStatsAcceptedShareCounter": bool(phase.get("pool_stats_accepted_share_counter", False)),
        "poolStatsRejectedShareCounter": bool(phase.get("pool_stats_rejected_share_counter", False)),
        "poolStatsSupportsDelta": bool(phase.get("pool_stats_supports_delta", False)),
        "poolStatsQualificationCapable": bool(phase.get("pool_stats_qualification_capable", False)),
        "poolStatsQualificationCapability": phase.get("pool_stats_qualification_capability", ""),
        "poolStatsAcceptedShareDelta": pool_stats_accepted_share_delta,
        "poolStatsAccepted": bool(pool_stats_accepted_share_delta),
        "apiBeforePath": str(phase_dir / "api-before.json"),
        "apiAfterPath": str(phase_dir / "api-after.json"),
        "waitResultPath": str(phase_dir / "wait-result.json"),
        "qemuLogPath": str(phase_dir / "qemu.log"),
        "poolStatsBeforePath": str(phase_dir / "pool-stats-before.json"),
        "poolStatsAfterPath": str(phase_dir / "pool-stats-after.json"),
        "runtimeOutDir": str(runtime_out_dir),
        "runtimeStateDir": str(runtime_state_dir),
        "error": phase_error,
    }


def write_markdown_summary(run_dir: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# virtualAxe Remote-Pool Verification",
        "",
        f"- Run ID: `{summary['runId']}`",
        f"- Source: `{summary['source']}`",
        f"- Verifier pool user: `{summary['poolUser']}`",
        f"- Mode: `{summary.get('mode', 'smoke')}`",
        f"- Status: `{summary['status']}`",
        f"- Release gate: `{summary.get('releaseGate', {}).get('policy', release_policy_text(summary.get('mode', 'smoke')))}`",
        "",
    ]

    smoke = summary.get("smoke", {})
    if smoke:
        lines.append("## Smoke")
        lines.append(f"- API smoke: `{smoke.get('api', {}).get('status', 'unknown')}`")
        lines.append(f"- Browser smoke: `{smoke.get('browser', {}).get('status', 'unknown')}`")
        lines.append(f"- Pool smoke: `{smoke.get('pool', {}).get('status', 'unknown')}`")
        lines.append("")

    for profile in summary["profiles"]:
        lines.append(f"## {profile['profile']}")
        lines.append(f"- Status: `{profile['status']}`")
        lines.append(f"- Release gate: `{profile['releaseGateStatus']}`")
        lines.append(f"- Duration: `{profile['durationSeconds']}` seconds")
        lines.append("")
        for phase in profile["phases"]:
            lines.append(f"### {phase['phase']} · {phase['label']}")
            lines.append(f"- Required for pass: `{phase['requiredForPass']}`")
            lines.append(f"- Status: `{phase['phaseStatus']}`")
            lines.append(f"- Session established: `{phase['sessionEstablished']}`")
            lines.append(f"- Pool: `{phase['poolHost']}:{phase['poolPort']}`")
            lines.append(f"- Subscribe agent: `{phase['subscribeAgent']}`")
            lines.append(f"- Assigned pool difficulty: `{phase['assignedPoolDifficulty']}`")
            lines.append(f"- Worker count: `{phase['workerCount']}`")
            lines.append(f"- Jobs assigned: `{phase['jobsAssigned']}`")
            lines.append(f"- Accepted share proof source: `{phase['acceptedShareProofSource']}`")
            lines.append(f"- Diagnostic accepted share delta: `{phase['diagnosticAcceptedShareDelta']}`")
            lines.append(f"- Local accepted share delta: `{phase['localAcceptedShareDelta']}`")
            lines.append(f"- QEMU accepted share delta: `{phase['qemuAcceptedShareDelta']}`")
            lines.append(f"- Pool Stratum accepted share delta: `{phase['poolStratumAcceptedShareDelta']}`")
            lines.append(f"- Qualification accepted share delta: `{phase['qualificationAcceptedShareDelta']}`")
            lines.append(f"- Qualification proof source: `{phase['qualificationProofSource']}`")
            lines.append(f"- Qualification proof sources: `{phase['qualificationProofSources']}`")
            lines.append(f"- Qualification pool-side proof required: `{phase['qualificationPoolSideRequired']}`")
            lines.append(f"- Qualification pool stats required: `{phase['qualificationPoolStatsRequired']}`")
            lines.append(f"- Required accepted share delta: `{phase['requiredAcceptedShareDelta']}`")
            lines.append(f"- Rejected share delta: `{phase['rejectedShareDelta']}`")
            lines.append(f"- Max rejected share delta: `{phase['maxRejectedShareDelta']}`")
            lines.append(f"- Phase duration: `{phase['phaseDurationSeconds']}` seconds")
            lines.append(f"- Required duration: `{phase['requiredDurationSeconds']}` seconds")
            lines.append(f"- Observed local diff: `{phase['observedLocalDiff']}`")
            lines.append(f"- Proven local diff lower bound: `{phase['provenLocalDiffLowerBound']}`")
            lines.append(f"- Local diff satisfied by acceptance: `{phase['localDiffSatisfiedByAcceptance']}`")
            lines.append(f"- Accepted log seen: `{phase['acceptedLogSeen']}`")
            lines.append(f"- QEMU pool identity: `{phase['qemuPoolIdentity']}`")
            lines.append(f"- QEMU worker identity: `{phase['qemuWorkerIdentity']}`")
            lines.append(f"- QEMU submit seen: `{phase['qemuSubmitSeen']}`")
            lines.append(f"- QEMU accepted share: `{phase['qemuAcceptedShare']}`")
            if phase.get("poolStatsURL"):
                lines.append(f"- Pool stats accepted: `{phase['poolStatsAccepted']}`")
                lines.append(f"- Pool stats worker: `{phase['poolStatsWorker']}`")
                lines.append(f"- Pool stats share delta: `{phase['poolStatsAcceptedShareDelta']}`")
                lines.append(f"- Pool stats worker-bound: `{phase['poolStatsWorkerBound']}`")
                lines.append(f"- Pool stats accepted-share counter: `{phase['poolStatsAcceptedShareCounter']}`")
                lines.append(f"- Pool stats rejected-share counter: `{phase['poolStatsRejectedShareCounter']}`")
                lines.append(f"- Pool stats supports delta: `{phase['poolStatsSupportsDelta']}`")
                lines.append(f"- Pool stats qualification capable: `{phase['poolStatsQualificationCapable']}`")
                lines.append(f"- Pool stats qualification capability: `{phase['poolStatsQualificationCapability']}`")
                lines.append(f"- Pool stats before: `{phase['poolStatsBeforePath']}`")
                lines.append(f"- Pool stats after: `{phase['poolStatsAfterPath']}`")
            lines.append(f"- API before: `{phase['apiBeforePath']}`")
            lines.append(f"- API after: `{phase['apiAfterPath']}`")
            lines.append(f"- Wait result: `{phase['waitResultPath']}`")
            lines.append(f"- QEMU log: `{phase['qemuLogPath']}`")
            lines.append(f"- Runtime out: `{phase['runtimeOutDir']}`")
            lines.append(f"- Runtime state: `{phase['runtimeStateDir']}`")
            if phase["error"]:
                lines.append(f"- Error: `{phase['error']}`")
            lines.append("")

    (run_dir / "summary.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def run_profile_matrix(
    *,
    args: argparse.Namespace,
    output_root: Path,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    phases = phases_for_mode(args.mode)
    profile_dir = output_root / "gamma"
    evidence_dir = profile_dir / "evidence"
    profile_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    initial_phase = phases[0]
    _, env, profile = build_release_env(args, initial_phase, run_id)
    base_url = f"http://127.0.0.1:{args.http_port}"
    stale_runtime_was_active = runtime_active(Path(env["OUT_DIR"]))

    stop_runtime(env)
    ensure_runtime_dependencies(env)

    started_at = time.time()
    smoke: dict[str, Any] = {}

    phases_summary: list[dict[str, Any]] = []
    phase_workers = {
        phase["slug"]: worker_name(args.pool_user, profile["id"], phase["slug"], run_id)
        for phase in phases
    }

    for index, phase in enumerate(phases):
        phase_dir = evidence_dir / phase["name"]
        phase_dir.mkdir(parents=True, exist_ok=True)
        qemu_log_start = 0
        before_payload: dict[str, Any] | None = None
        after_payload: dict[str, Any] | None = None
        wait_payload: dict[str, Any] | None = None
        phase_error = ""
        runtime_out_dir, runtime_state_dir = phase_runtime_paths(profile_dir, phase)
        pool_stats_worker_name = phase_workers[phase["slug"]]
        pool_stats_url = phase_pool_stats_url(phase, args.pool_user)
        pool_stats_page_url = phase_pool_stats_page_url(phase, args.pool_user, pool_stats_worker_name)
        pool_stats_baseline_shares: float | None = None
        pool_stats_baseline_best_difficulty: float | None = None
        pool_stats_baseline_last_share: str | None = None
        _, env, profile = build_release_env(
            args,
            phase,
            run_id,
            out_dir=runtime_out_dir,
            state_dir=runtime_state_dir,
        )
        qemu_log = Path(env["OUT_DIR"]) / "qemu.log"

        try:
            stop_runtime(env)
            build_result = run_command([str(BUILD_VIRTUAL)], env=env, capture=True)
            (phase_dir / "build.stdout.log").write_text(build_result.stdout or "", encoding="utf-8")
            (phase_dir / "build.stderr.log").write_text(build_result.stderr or "", encoding="utf-8")
            (profile_dir / "build.stdout.log").write_text(build_result.stdout or "", encoding="utf-8")
            (profile_dir / "build.stderr.log").write_text(build_result.stderr or "", encoding="utf-8")
            if build_result.returncode != 0:
                raise RuntimeError(build_result.stderr.strip() or build_result.stdout.strip() or f"Unable to build {phase['label']} release image")

            if pool_stats_url:
                (
                    pool_stats_baseline_shares,
                    pool_stats_baseline_best_difficulty,
                    pool_stats_baseline_last_share,
                ) = capture_pool_stats_before(
                    phase_dir,
                    pool_stats_url,
                    pool_stats_worker_name,
                    phase,
                )

            start_runtime(env)

            if index == 0:
                smoke_dir = profile_dir / "smoke"
                smoke_dir.mkdir(exist_ok=True)
                smoke = {
                    "api": run_api_smoke(base_url, smoke_dir, args.source),
                    "browser": run_browser_smoke(base_url, smoke_dir, args.source),
                    "pool": run_pool_connectivity_smoke(base_url, initial_phase, smoke_dir, args.source),
                }
                write_json(smoke_dir / "summary.json", smoke)

            qemu_log_start = 0
            before_payload = fetch_system_info(base_url)
            write_json(phase_dir / "api-before.json", before_payload)
            expected_runtime_settings = phase_primary_settings(phase, phase_workers[phase["slug"]], args.source)
            if not settings_match(before_payload, expected_runtime_settings):
                observed = compact_settings(before_payload, list(expected_runtime_settings.keys()))
                raise RuntimeError(f"{phase['label']} runtime settings mismatch: expected {expected_runtime_settings}; observed {observed}")
            baseline_shares = int(before_payload.get("sharesAccepted", 0) or 0)
            baseline_rejected = int(before_payload.get("sharesRejected", 0) or 0)
            wait_result = run_wait_for_phase(
                base_url,
                phase,
                baseline_shares,
                baseline_rejected,
                qemu_log,
                qemu_log_start,
                args.source,
                pool_stats_url,
                pool_stats_page_url,
                pool_stats_worker_name,
                pool_stats_baseline_shares,
                pool_stats_baseline_best_difficulty,
                pool_stats_baseline_last_share,
            )
            (phase_dir / "wait.stdout.log").write_text(wait_result.stdout or "", encoding="utf-8")
            (phase_dir / "wait.stderr.log").write_text(wait_result.stderr or "", encoding="utf-8")
            if wait_result.returncode != 0:
                raise RuntimeError(wait_result.stderr.strip() or wait_result.stdout.strip() or f"{phase['label']} phase timed out")
            wait_payload = json.loads(wait_result.stdout or "{}")
            write_json(phase_dir / "wait-result.json", wait_payload)
        except Exception as exc:  # noqa: BLE001
            phase_error = str(exc) or f"{phase['label']} failed"
            if wait_payload is None:
                write_json(phase_dir / "wait-result.json", {"error": phase_error})
        finally:
            try:
                after_payload = fetch_system_info(base_url)
                write_json(phase_dir / "api-after.json", after_payload)
            except Exception as exc:  # noqa: BLE001
                phase_error = phase_error or f"Unable to capture API after {phase['label']}: {exc}"
                if after_payload is None:
                    write_json(phase_dir / "api-after.json", {"error": phase_error})
            if pool_stats_url:
                try:
                    write_json(
                        phase_dir / "pool-stats-after.json",
                        pool_stats_worker_snapshot(
                            pool_stats_url,
                            pool_stats_worker_name,
                            kind=str(phase.get("pool_stats_kind", "ckpool_shares")),
                            auth=str(phase.get("pool_stats_auth", "")),
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    write_json(phase_dir / "pool-stats-after.json", {"error": str(exc), "url": pool_stats_url, "worker": pool_stats_worker_name})
            write_log_excerpt(qemu_log, qemu_log_start, phase_dir / "qemu.log")
            stop_runtime(env)

        phases_summary.append(
            summarize_phase(
                phase,
                phase_dir,
                runtime_out_dir,
                runtime_state_dir,
                wait_payload,
                before_payload,
                after_payload,
                phase_error=phase_error,
            )
        )

    finished_at = time.time()
    blocking_failures = [phase["phase"] for phase in phases_summary if phase["requiredForPass"] and phase["phaseStatus"] != "PASSED"]
    payload = {
        "profile": profile["id"],
        "status": "passed" if not blocking_failures else "failed",
        "releaseGateStatus": "PASSED" if not blocking_failures else "FAILED",
        "mode": args.mode,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "durationSeconds": round(finished_at - started_at, 2),
        "staleRuntimeWasActive": stale_runtime_was_active,
        "buildLogPaths": {
            "stdout": str(profile_dir / "build.stdout.log"),
            "stderr": str(profile_dir / "build.stderr.log"),
        },
        "smoke": smoke,
        "phases": phases_summary,
        "blockingFailures": blocking_failures,
    }
    write_json(profile_dir / "summary.json", payload)
    return payload, env


def main() -> int:
    args = parse_args()
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.out_dir).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    env: dict[str, str] | None = None
    overall_status = "failed"
    profile_payload: dict[str, Any] | None = None
    top_level_error = ""
    try:
        profile_payload, env = run_profile_matrix(args=args, output_root=run_dir, run_id=run_id)
        overall_status = profile_payload["status"]
    except Exception as exc:  # noqa: BLE001
        top_level_error = str(exc) or "verify-release failed"
    finally:
        if env is not None:
            stop_runtime(env)

    summary = {
        "runId": run_id,
        "source": args.source,
        "poolUser": args.pool_user,
        "mode": args.mode,
        "status": overall_status,
        "releaseGate": {
            "requiredPools": ["PublicPool", "Bitronics", "Nerdminers"],
            "policy": release_policy_text(args.mode),
            "smoke": {
                "phaseTimeoutSeconds": SMOKE_PHASE_TIMEOUT_SECONDS,
                "phaseTimeoutSecondsByPool": {
                    phase["label"]: float(phase["phase_timeout_seconds"])
                    for phase in phases_for_mode("smoke")
                },
                "feasibilityByPool": {
                    phase["label"]: phase.get("smoke_feasibility", {})
                    for phase in phases_for_mode("smoke")
                    if phase.get("smoke_feasibility")
                },
                "minAcceptedShareDelta": SMOKE_MIN_ACCEPTED_SHARE_DELTA,
            },
            "qualification": {
                "phaseTimeoutSeconds": QUALIFICATION_PHASE_TIMEOUT_SECONDS,
                "minDurationSeconds": QUALIFICATION_MIN_PHASE_DURATION_SECONDS,
                "minAcceptedShareDelta": QUALIFICATION_MIN_ACCEPTED_SHARE_DELTA,
                "proofSources": ["pool_stratum_response", "pool_stats"],
                "poolSideAcceptedShareRequired": True,
                "countProbabilityPolicy": qualification_count_policy(),
            },
        },
        "error": top_level_error,
        "profiles": [profile_payload] if profile_payload is not None else [],
        "outputDir": str(run_dir),
    }
    write_json(run_dir / "summary.json", summary)
    write_markdown_summary(run_dir, summary)

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"verify-release {overall_status}: {run_dir}")
    return 0 if overall_status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
