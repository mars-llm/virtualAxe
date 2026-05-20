#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from rich import box
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Button, RichLog, Static
except ModuleNotFoundError as exc:  # pragma: no cover - exercised by runtime smoke
    missing = exc.name or "textual/rich"
    raise SystemExit(
        f"Missing Python dependency {missing!r}. Run ./scripts/ensure-test-python.sh before starting the dashboard directly."
    ) from exc


ROOT_DIR = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT_DIR / "configs" / "profiles"

EVENT_LIMIT = 400
MAX_LOG_RENDER = 220

BUILD_SOURCE = "build"
BOOT_SOURCE = "boot"
API_SOURCE = "api"
POOL_SOURCE = "pool"
SHARE_SOURCE = "share"
ERROR_SOURCE = "error"
SYSTEM_SOURCE = "system"

FILTER_ALL = "all"
FILTER_POOL = "pool"
FILTER_SYS = "system"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="virtualAxe operator dashboard")
    parser.add_argument("--source", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--network-mode", choices=("nat",), default="nat")
    parser.add_argument("--http-port", type=int, default=18080)
    parser.add_argument("--web-http-port", type=int)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--auto-start", action="store_true")
    return parser.parse_args()


def load_profile(profile_name: str) -> dict[str, Any]:
    path = PROFILES_DIR / f"{profile_name}.json"
    if not path.is_file():
        raise SystemExit(f"Unknown profile {profile_name!r}: expected {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["path"] = str(path)
    return payload


def load_csv_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    values: dict[str, str] = {}
    for row in rows:
        if row and row[0] not in ("key", "main"):
            while len(row) < 4:
                row.append("")
            values[row[0]] = row[3]
    return values


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def fetch_json(url: str, *, timeout: float = 1.5) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, http.client.HTTPException):
        return None


def runtime_is_active(container_name: str, out_dir: Path) -> str:
    qemu_pid = out_dir / "qemu.pid"
    if qemu_pid.is_file():
        return "native"
    podman = shutil.which("podman")
    if podman:
        result = subprocess.run(
            [podman, "ps", "--format", "{{.Names}}"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and container_name in result.stdout.splitlines():
            return "container"
    return ""


def format_uptime(value: Any) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return "n/a"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours:02d}h")
    parts.append(f"{minutes:02d}m")
    parts.append(f"{secs:02d}s")
    return " ".join(parts)


def coalesce(*values: Any, default: str = "n/a") -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return str(value)
    return default


def format_metric(value: Any, suffix: str = "", unavailable: str = "n/a") -> str:
    if value in (None, "", -1):
        return unavailable
    try:
        if isinstance(value, float):
            return f"{value:.1f}{suffix}"
        return f"{value}{suffix}"
    except Exception:
        return unavailable


def format_age(ts: float, *, now_ts: float | None = None) -> str:
    now_value = now_ts if now_ts is not None else time.time()
    delta = max(0, int(now_value - ts))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def short_time(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def now_session_id(profile_name: str) -> str:
    digest = hashlib.sha256(f"{profile_name}:{time.time_ns()}".encode("utf-8")).hexdigest()
    return digest[:8].upper()


def source_display_name(source_name: str) -> str:
    return {
        "bitaxe": "Bitaxe",
        "vanilla": "Bitaxe",
        "nerdnos": "NerdNos",
    }.get(source_name, source_name.replace("_", "-").title())


def source_profile_name(source_name: str, profile: dict[str, Any]) -> str:
    device_model = coalesce(profile.get("deviceModel"), profile.get("id"), default="").strip()
    source_display = source_display_name(source_name)
    return f"{source_display} {device_model}".strip()


def source_identity_label(source_name: str) -> str:
    canonical = "bitaxe" if source_name == "vanilla" else source_name
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", canonical).strip("_").upper()
    return f"IDENT_VIRTUAL_{normalized or 'SOURCE'}"


def event_matches_filter(event_source: str, selected_filter: str) -> bool:
    if selected_filter == FILTER_ALL:
        return True
    if selected_filter == FILTER_POOL:
        return event_source in {POOL_SOURCE, SHARE_SOURCE}
    return event_source in {BUILD_SOURCE, BOOT_SOURCE, API_SOURCE, ERROR_SOURCE, SYSTEM_SOURCE}


def event_source_color(source: str, severity: str) -> str:
    if severity == "error":
        return "#ef5350"
    return {
        BUILD_SOURCE: "#7986cb",
        BOOT_SOURCE: "#4db6ac",
        API_SOURCE: "#81d4fa",
        POOL_SOURCE: "#9ccc65",
        SHARE_SOURCE: "#ffca28",
        ERROR_SOURCE: "#ef5350",
        SYSTEM_SOURCE: "#c3c7ce",
    }.get(source, "#c3c7ce")


def infer_build_state(build_log: Path, manifest: dict[str, Any], target_profile: str, source_name: str, action_busy: bool) -> tuple[str, str]:
    if action_busy:
        return ("BUILDING", "Building firmware in patched worktree")
    if manifest:
        manifest_profile = manifest.get("virtualProfile")
        manifest_source = manifest.get("sourceName")
        if manifest_profile != target_profile or manifest_source != source_name:
            return ("STALE", f"Existing image is for {manifest_source or '?'} / {manifest_profile or '?'}")
        artifacts = manifest.get("artifacts", {})
        flash = artifacts.get("qemu_flash.bin", {})
        if flash.get("exists"):
            return ("READY", "Patched firmware image present")
    if not build_log.is_file():
        return ("NOT_RUN", "No build log yet")
    text = build_log.read_text(encoding="utf-8", errors="replace")
    lowered = text.lower()
    if "project build complete" in lowered or "ready to flash" in lowered:
        return ("READY", "Last build completed successfully")
    if "error" in lowered or "failed" in lowered:
        return ("FAILED", "Build log contains an error marker")
    return ("WAIT", "Build log present but completion is unclear")


def infer_test_ci_state(log_path: Path) -> tuple[str, str]:
    if not log_path.is_file():
        return ("NOT_RUN", "test-ci has not been run yet")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if "Tests" in text and "Failures 0" in text and "Ignored" in text:
        return ("PASS", "Upstream test-ci QEMU proof passed")
    if ":FAIL" in text or "FAILED" in text:
        return ("FAIL", "test-ci log contains failing Unity output")
    return ("WAIT", "test-ci log exists but pass/fail is unclear")


def infer_boot_state(runtime_mode: str, api_online: bool, qemu_log: Path, runner_error: str) -> tuple[str, str]:
    if runner_error:
        return ("ERROR", runner_error)
    if api_online:
        return ("BOOTED", "Firmware booted and API reachable")
    if runtime_mode:
        text = qemu_log.read_text(encoding="utf-8", errors="replace") if qemu_log.is_file() else ""
        lowered = text.lower()
        if "http server" in lowered or "starting asic initialization" in lowered:
            return ("BOOTING", "Firmware is running but API is not reachable yet")
        if "panic" in lowered or "abort" in lowered:
            return ("ERROR", "Boot log contains a fatal marker")
        return ("BOOTING", "QEMU runtime is active")
    return ("STOPPED", "virtualAxe runtime is not running")


def infer_patch_state(upstream_dir: str | None) -> tuple[str, str]:
    if not upstream_dir:
        return ("WAIT", "No patched worktree configured")
    upstream_path = Path(upstream_dir)
    if upstream_path.is_dir() and ".worktrees" in upstream_path.parts:
        return ("APPLIED", f"Patched worktree ready: {upstream_path.name}")
    if upstream_path.is_dir():
        return ("UNKNOWN", f"Upstream directory exists: {upstream_path}")
    return ("FAIL", "Configured upstream worktree is missing")


def infer_persistence_state(manifest: dict[str, Any], state_dir: Path, last_result: dict[str, Any] | None) -> tuple[str, str]:
    if last_result:
        if last_result.get("ok"):
            return ("VERIFIED", "Persistence verified across restart and rebuild")
        return ("FAIL", last_result.get("message", "Persistence verification failed"))
    nvs_path = state_dir / "nvs.bin"
    if not nvs_path.is_file():
        return ("WAIT", "No persisted NVS state yet")
    mode = manifest.get("nvsSeedMode")
    if mode:
        return ("READY", f"State file present ({mode})")
    return ("READY", "State file present")


def latest_release_summary(release_root: Path) -> tuple[Path | None, dict[str, Any] | None]:
    summaries = [path for path in release_root.glob("*/summary.json") if path.is_file()]
    if not summaries:
        return (None, None)
    newest = max(summaries, key=lambda path: path.stat().st_mtime)
    return (newest, load_json(newest))


def infer_release_gate_state(summary_path: Path | None, summary: dict[str, Any] | None) -> tuple[str, str]:
    if not summary_path or not summary:
        return ("NOT_RUN", "No pool smoke run yet. Public + Bitronics + Nerdminers required.")

    run_id = summary.get("runId") or summary_path.parent.name
    age = format_age(summary_path.stat().st_mtime)
    mode = summary.get("mode", "smoke")
    profiles = summary.get("profiles") or []
    profile = profiles[0] if profiles else {}
    blocking_failures = profile.get("blockingFailures") or []

    if summary.get("status") == "passed" and profile.get("releaseGateStatus") == "PASSED":
        return (
            "PASS",
            f"Run {run_id} passed {age} in {mode} mode. Public + Bitronics + Nerdminers accepted.",
        )

    if summary.get("error"):
        return ("FAIL", f"Run {run_id} failed {age}: {summary['error']}")

    if blocking_failures:
        missing = ", ".join(blocking_failures)
        return ("FAIL", f"Run {run_id} failed {age}. Missing required phase(s): {missing}.")

    return ("WAIT", f"Run {run_id} is incomplete {age}. Public + Bitronics + Nerdminers required.")


def status_to_style(state: str) -> str:
    if state in {"READY", "PASS", "BOOTED", "APPLIED", "VERIFIED", "RUNNING"}:
        return "#4caf50"
    if state in {"WAIT", "BOOTING", "BUILDING", "STALE", "UNKNOWN", "NOT_RUN"}:
        return "#ffca28"
    if state in {"FAILED", "FAIL", "ERROR", "STOPPED", "HALTED"}:
        return "#ef5350"
    return "#e0e2e5"


def runtime_badge(runtime_mode: str, api_online: bool, runner_busy: bool, runner_error: str, active_label: str) -> tuple[str, str]:
    if runner_error:
        return ("ERROR", "#ef5350")
    if runner_busy:
        if active_label == "rebuild":
            return ("BUILDING", "#ffca28")
        return ("BOOTING", "#ffca28")
    if api_online and runtime_mode:
        return ("RUNNING", "#4caf50")
    if runtime_mode:
        return ("BOOTING", "#ffca28")
    return ("STOPPED", "#ef5350")


@dataclass
class LogEvent:
    id: int
    source: str
    message: str
    severity: str
    timestamp: float


@dataclass
class WorkerCardState:
    asic_nr: int
    model: str
    lane_offset: int
    lane_stride: int
    jobs_assigned: int
    state: str
    last_event: str


@dataclass
class DashboardSnapshot:
    session_id: str
    profile_name: str
    board_version: str
    firmware_version: str
    axeos_version: str
    web_url: str
    api_online: bool
    runtime_label: str
    runtime_color: str
    runtime_mode: str
    primary_pool: str
    fallback_pool: str
    pool_state: str
    accepted_shares: str
    rejected_shares: str
    best_diff: str
    pool_difficulty: str
    uptime: str
    ipv4: str
    temp: str
    fan_rpm: str
    power: str
    voltage: str
    workers: list[WorkerCardState]
    build_state: tuple[str, str]
    patch_state: tuple[str, str]
    boot_state: tuple[str, str]
    api_state: tuple[str, str]
    release_gate_state: tuple[str, str]
    persistence_state: tuple[str, str]
    test_ci_state: tuple[str, str]
    action_summary: str
    events: list[LogEvent] = field(default_factory=list)


class EventBuffer:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.log_files = {
            BUILD_SOURCE: out_dir / "build.log",
            "qemu": out_dir / "qemu.log",
            "test-ci": out_dir / "test-ci-qemu.log",
        }
        self.positions: dict[str, int] = {}
        self.events: deque[LogEvent] = deque(maxlen=EVENT_LIMIT)
        self.lock = threading.Lock()
        self.counter = 0
        self.worker_last_event: dict[int, str] = {}
        self._seed_existing()

    def _next_id(self) -> int:
        self.counter += 1
        return self.counter

    def _seed_existing(self) -> None:
        for key, path in self.log_files.items():
            if not path.is_file():
                self.positions[key] = 0
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            self.positions[key] = len(text)
            lines = [line for line in text.splitlines()[-18:] if line.strip()]
            for line in lines:
                self.events.append(self._classify_line(key, line, initial_seed=True))

    def _update_worker_event(self, line: str) -> None:
        lower = line.lower()
        match = re.search(r"worker\s+(\d+)", lower)
        if not match:
            return
        if not any(token in lower for token in ("share", "submit", "nonce", "hint")):
            return
        worker_id = int(match.group(1))
        cleaned = line.strip()
        if len(cleaned) > 96:
            cleaned = cleaned[:93] + "..."
        self.worker_last_event[worker_id] = cleaned

    def _classify_line(self, source_key: str, line: str, *, initial_seed: bool = False) -> LogEvent:
        cleaned = line.strip()
        lower = cleaned.lower()
        source = SYSTEM_SOURCE
        severity = "info"

        if "error" in lower or cleaned.startswith("E (") or ":fail" in lower or " failed" in lower:
            severity = "error"

        if source_key == BUILD_SOURCE:
            source = BUILD_SOURCE
        elif source_key == "test-ci":
            if "running all the registered tests" in lower or "tests " in lower:
                source = SYSTEM_SOURCE
            elif severity == "error":
                source = ERROR_SOURCE
            else:
                source = BUILD_SOURCE
        else:
            if any(token in lower for token in ("share accepted", "accepted share", "mining.submit", "submit")):
                source = SHARE_SOURCE
            elif any(token in lower for token in ("stratum", "pool", "authorize", "difficulty", "subscribe")):
                source = POOL_SOURCE
            elif any(token in lower for token in ("http server", "/api/", "web ui", "websocket")):
                source = API_SOURCE
            elif any(token in lower for token in ("boot", "app_main", "initializing", "starting asic", "filesystem")):
                source = BOOT_SOURCE
            else:
                source = SYSTEM_SOURCE

        if severity == "error":
            source = ERROR_SOURCE if source != BUILD_SOURCE else BUILD_SOURCE

        if source_key == "qemu":
            self._update_worker_event(cleaned)

        timestamp = time.time() if not initial_seed else max(time.time() - 2, 0.0)
        return LogEvent(self._next_id(), source, cleaned, severity, timestamp)

    def add_manual_event(self, source: str, message: str, severity: str = "info") -> None:
        with self.lock:
            self.events.append(LogEvent(self._next_id(), source, message, severity, time.time()))

    def poll(self) -> None:
        with self.lock:
            for key, path in self.log_files.items():
                if not path.is_file():
                    continue
                current_size = path.stat().st_size
                previous_size = self.positions.get(key, 0)
                if current_size < previous_size:
                    previous_size = 0
                if current_size == previous_size:
                    continue
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(previous_size)
                    chunk = handle.read()
                self.positions[key] = current_size
                for raw_line in chunk.splitlines():
                    if raw_line.strip():
                        self.events.append(self._classify_line(key, raw_line))

    def get_events(self) -> list[LogEvent]:
        with self.lock:
            return list(self.events)

    def last_worker_event(self, worker_id: int) -> str:
        with self.lock:
            return self.worker_last_event.get(worker_id, "n/a")


class ActionRunner:
    def __init__(self, env: dict[str, str], args: argparse.Namespace, event_buffer: EventBuffer):
        self.env = env
        self.args = args
        self.root_dir = ROOT_DIR
        self.event_buffer = event_buffer
        self.qemu_script = self.root_dir / "scripts" / "run-qemu-nat.sh"
        self.build_script = self.root_dir / "scripts" / "build-virtual.sh"
        self.test_ci_script = self.root_dir / "scripts" / "verify-test-ci.sh"
        self.verify_script = self.root_dir / "scripts" / "run-e2e.sh"
        self.persistence_script = self.root_dir / "scripts" / "verify-settings-persistence.sh"
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.label = "idle"
        self.step = "Ready"
        self.last_error = ""
        self.last_success_at = 0.0
        self.last_results: dict[str, dict[str, Any]] = {}

    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _set_state(self, *, label: str | None = None, step: str | None = None, error: str | None = None) -> None:
        with self.lock:
            if label is not None:
                self.label = label
            if step is not None:
                self.step = step
            if error is not None:
                self.last_error = error

    def start(self, label: str, fn) -> bool:
        if self.busy():
            self.event_buffer.add_manual_event(ERROR_SOURCE, f"Action {label} ignored: another action is already running", "error")
            return False

        def runner() -> None:
            started_at = time.time()
            self._set_state(label=label, step="Starting", error="")
            self.event_buffer.add_manual_event(SYSTEM_SOURCE, f"{label.upper()} requested")
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                message = str(exc) or f"{label} failed"
                self._set_state(step="Failed", error=message)
                self.last_results[label] = {"ok": False, "finishedAt": time.time(), "message": message}
                self.event_buffer.add_manual_event(ERROR_SOURCE, f"{label.upper()} failed: {message}", "error")
            else:
                finished_at = time.time()
                self._set_state(step="Completed", error="")
                self.last_success_at = finished_at
                self.last_results[label] = {
                    "ok": True,
                    "finishedAt": finished_at,
                    "message": f"{label} completed in {finished_at - started_at:.1f}s",
                }
                self.event_buffer.add_manual_event(SYSTEM_SOURCE, f"{label.upper()} completed")

        self.thread = threading.Thread(target=runner, daemon=True)
        self.thread.start()
        return True

    def update_step(self, step: str) -> None:
        self._set_state(step=step)
        self.event_buffer.add_manual_event(SYSTEM_SOURCE, step)

    def _run_cmd(self, cmd: list[str], extra_env: dict[str, str] | None = None) -> None:
        env = self.env.copy()
        env["VIRTUALAXE_DISABLE_TEE"] = "1"
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(cmd, cwd=self.root_dir, env=env, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or f"Command failed: {' '.join(cmd)}"
            raise RuntimeError(message)

    def stop_runtime(self) -> None:
        self.update_step("Stopping virtualAxe runtime")
        subprocess.run([str(self.qemu_script), "--stop"], cwd=self.root_dir, env=self.env, check=False)

    def start_runtime(self) -> None:
        self.update_step("Starting virtualAxe runtime")
        self._run_cmd([str(self.qemu_script)], {"BACKGROUND": "1"})

    def rebuild_and_start(self) -> None:
        self.stop_runtime()
        self.update_step("Building virtualAxe image")
        self._run_cmd([str(self.build_script)])
        self.start_runtime()

    def restart(self) -> None:
        self.stop_runtime()
        self.start_runtime()

    def run_test_ci(self) -> None:
        self.update_step("Running upstream test-ci in QEMU")
        self._run_cmd([str(self.test_ci_script)])

    def run_local_verify(self) -> None:
        self.update_step("Running API/browser verification")
        self._run_cmd([str(self.verify_script)])
        self.update_step("Running persistence verification")
        self._run_cmd([str(self.persistence_script)])

class DashboardStateAdapter:
    def __init__(self, args: argparse.Namespace, env: dict[str, str], profile: dict[str, Any], event_buffer: EventBuffer):
        self.args = args
        self.env = env
        self.profile = profile
        self.event_buffer = event_buffer
        self.out_dir = Path(args.out_dir)
        self.state_dir = Path(args.state_dir)
        self.web_http_port = getattr(args, "web_http_port", None) or args.http_port
        self.base_url = f"http://127.0.0.1:{self.web_http_port}"
        self.config_csv = self.out_dir / "config-active.csv"
        self.build_log = self.out_dir / "build.log"
        self.qemu_log = self.out_dir / "qemu.log"
        self.test_ci_log = self.out_dir / "test-ci-qemu.log"
        self.manifest_path = self.out_dir / "manifest.json"
        self.release_root = self.out_dir / "release-matrix"

    def _worker_cards(
        self,
        api_payload: dict[str, Any] | None,
        runtime_label: str,
        runner_error: str,
        *,
        allow_history: bool,
    ) -> list[WorkerCardState]:
        cards: list[WorkerCardState] = []
        workers = api_payload.get("virtualAsicWorkers", []) if api_payload else []
        if not workers:
            for index in range(int(self.profile.get("asicCount", 1))):
                state = "ERROR" if runner_error else ("BOOTING" if runtime_label == "BOOTING" else "IDLE")
                cards.append(
                    WorkerCardState(
                        asic_nr=index,
                        model=self.profile.get("asicModel", "BM1370"),
                        lane_offset=index,
                        lane_stride=int(self.profile.get("asicCount", 1)),
                        jobs_assigned=0,
                        state=state,
                        last_event=self.event_buffer.last_worker_event(index) if allow_history else "n/a",
                    )
                )
            return cards

        for worker in workers:
            jobs_assigned = int(worker.get("jobsAssigned", 0) or 0)
            if runner_error:
                state = "ERROR"
            elif runtime_label == "BOOTING":
                state = "BOOTING"
            elif jobs_assigned > 0:
                state = "ACTIVE"
            else:
                state = "IDLE"
            asic_nr = int(worker.get("asicNr", 0) or 0)
            cards.append(
                WorkerCardState(
                    asic_nr=asic_nr,
                    model=coalesce(api_payload.get("ASICModel") if api_payload else None, self.profile.get("asicModel")),
                    lane_offset=int(worker.get("laneOffset", asic_nr) or asic_nr),
                    lane_stride=int(worker.get("laneStride", len(workers)) or max(len(workers), 1)),
                    jobs_assigned=jobs_assigned,
                    state=state,
                    last_event=self.event_buffer.last_worker_event(asic_nr) if allow_history else "n/a",
                )
            )
        return cards

    def refresh(self, runner: ActionRunner, session_id: str) -> DashboardSnapshot:
        self.event_buffer.poll()
        api_payload = fetch_json(f"{self.base_url}/api/system/info")
        manifest = load_json(self.manifest_path)
        config_values = load_csv_values(self.config_csv)
        runtime_mode = runtime_is_active(self.env.get("QEMU_CONTAINER_NAME", "virtualaxe-qemu"), self.out_dir)
        api_online = api_payload is not None
        runtime_label, runtime_color = runtime_badge(runtime_mode, api_online, runner.busy(), runner.last_error, runner.label)
        build_state = infer_build_state(
            self.build_log,
            manifest,
            self.profile["id"],
            self.args.source,
            runner.busy() and runner.label in {"rebuild"},
        )
        patch_state = infer_patch_state(self.env.get("UPSTREAM_DIR"))
        boot_state = infer_boot_state(runtime_mode, api_online, self.qemu_log, runner.last_error)
        api_state = ("ONLINE", f"WebUI reachable at {self.base_url}") if api_online else ("OFFLINE", f"WebUI not reachable at {self.base_url}")
        release_summary_path, release_summary = latest_release_summary(self.release_root)
        release_gate_state = infer_release_gate_state(release_summary_path, release_summary)
        persistence_state = infer_persistence_state(manifest, self.state_dir, runner.last_results.get("verify"))
        test_ci_state = infer_test_ci_state(self.test_ci_log)
        profile_matches_manifest = manifest.get("virtualProfile") == self.profile["id"] and manifest.get("sourceName") == self.args.source
        allow_worker_history = api_online or (bool(runtime_mode) and profile_matches_manifest)

        pool_state = coalesce(api_payload.get("poolConnectionInfo") if api_payload else None, default="Not Connected")
        action_summary = self._action_summary(runtime_label, api_payload, pool_state, runner, api_online, build_state[0])
        workers = self._worker_cards(api_payload, runtime_label, runner.last_error, allow_history=allow_worker_history)
        config_primary = config_values.get("stratumurl") or manifest.get("poolHost")
        config_fallback = config_values.get("fbstratumurl") or manifest.get("fallbackPoolHost")

        return DashboardSnapshot(
            session_id=session_id,
            profile_name=source_profile_name(self.args.source, self.profile),
            board_version=coalesce(api_payload.get("boardVersion") if api_payload else None, self.profile.get("boardVersion")),
            firmware_version=coalesce(api_payload.get("version") if api_payload else None),
            axeos_version=coalesce(api_payload.get("axeOSVersion") if api_payload else None),
            web_url=f"{self.base_url}/",
            api_online=api_online,
            runtime_label=runtime_label,
            runtime_color=runtime_color,
            runtime_mode=runtime_mode or "stopped",
            primary_pool=coalesce(api_payload.get("stratumURL") if api_payload else None, config_primary, default="not configured"),
            fallback_pool=coalesce(api_payload.get("fallbackStratumURL") if api_payload else None, config_fallback, default="not configured"),
            pool_state=pool_state,
            accepted_shares=coalesce(api_payload.get("sharesAccepted") if api_payload else None, default="0"),
            rejected_shares=coalesce(api_payload.get("sharesRejected") if api_payload else None, default="0"),
            best_diff=coalesce(api_payload.get("bestDiff") if api_payload else None),
            pool_difficulty=coalesce(api_payload.get("poolDifficulty") if api_payload else None),
            uptime=format_uptime(api_payload.get("uptimeSeconds") if api_payload else None),
            ipv4=coalesce(api_payload.get("ipv4") if api_payload else None),
            temp=format_metric(api_payload.get("temp") if api_payload else None, " °C", unavailable="virtual"),
            fan_rpm=format_metric(api_payload.get("fanrpm") if api_payload else None, " rpm", unavailable="virtual"),
            power=format_metric(api_payload.get("power") if api_payload else None, " W", unavailable="not exposed"),
            voltage=format_metric(api_payload.get("coreVoltageActual") if api_payload else None, " V", unavailable="not exposed"),
            workers=workers,
            build_state=build_state,
            patch_state=patch_state,
            boot_state=boot_state,
            api_state=api_state,
            release_gate_state=release_gate_state,
            persistence_state=persistence_state,
            test_ci_state=test_ci_state,
            action_summary=action_summary,
            events=self.event_buffer.get_events(),
        )

    def _action_summary(
        self,
        runtime_label: str,
        api_payload: dict[str, Any] | None,
        pool_state: str,
        runner: ActionRunner,
        api_online: bool,
        build_state: str,
    ) -> str:
        if runner.busy():
            return runner.step
        if runner.last_error:
            return runner.last_error
        if build_state == "STALE":
            return "Selected source/profile differs from the current image. Rebuild before starting."
        if api_online:
            shares = int(api_payload.get("sharesAccepted", 0) or 0) if api_payload else 0
            if shares > 0:
                return "Mining nominally. Accepted shares visible in the current session."
            if pool_state not in ("", "Not Connected", "No connection"):
                return "Connected to pool, waiting for accepted share."
            return f"WebUI reachable at {self.base_url}, pool not connected."
        if runtime_label == "BOOTING":
            return "Firmware booting, waiting for the API and WebUI."
        return "System halted. Start the runtime or rebuild the firmware."

class UrlOverlayScreen(ModalScreen[None]):
    CSS = """
    UrlOverlayScreen {
        align: center middle;
        background: rgba(13, 15, 18, 0.90);
    }

    #url-overlay {
        width: 80;
        border: round #4db6ac;
        background: #111418;
        padding: 2 4;
    }
    """

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def compose(self) -> ComposeResult:
        content = Text()
        content.append(":: BROWSER LAUNCH UNAVAILABLE ::\n\n", style="bold #4db6ac")
        content.append(f"{self.url}\n\n", style="bold white")
        content.append("Open this URL in your host browser.\n", style="#c3c7ce")
        content.append("[ closing automatically ]", style="italic #6b7280")
        yield Static(Panel(content, border_style="#4db6ac", box=box.ROUNDED), id="url-overlay")

    def on_mount(self) -> None:
        self.set_timer(3.0, self.dismiss)

    def key_escape(self) -> None:
        self.dismiss()


class BitaxeDashboardApp(App[None]):
    TITLE = "virtualAxe Operator Dashboard"
    CSS = """
    Screen {
        layout: vertical;
        background: #0d0f12;
        color: #e0e2e5;
    }

    #header-bar {
        height: 3;
        background: #16191d;
        border-bottom: solid #2d333b;
        padding: 0 2;
        layout: horizontal;
        align: center middle;
    }

    #header-left {
        width: 1fr;
        content-align: left middle;
    }

    #header-right {
        width: auto;
        content-align: right middle;
    }

    #main-layout {
        height: 1fr;
        layout: horizontal;
        overflow: hidden;
    }

    #left-column, #right-column {
        padding: 1 2;
        overflow: auto;
    }

    #left-column {
        width: 1fr;
        border-right: solid #2d333b;
    }

    #right-column {
        width: 1fr;
    }

    .card {
        margin-bottom: 1;
    }

    #identity-card-view {
        margin-bottom: 0;
    }

    #show-web-url {
        width: auto;
        min-width: 0;
        background: transparent;
        color: #81d4fa;
        text-style: underline;
        border: none;
        padding: 0;
        margin-left: 1;
    }

    #event-card {
        height: 1fr;
        min-height: 18;
    }

    #event-header {
        height: 3;
        background: #1c2026;
        border: round #2d333b;
        padding: 0 1;
        layout: horizontal;
        align: center middle;
    }

    #event-title {
        width: 1fr;
        color: #9ca3af;
    }

    #event-filter-bar {
        width: auto;
        layout: horizontal;
    }

    .filter-button {
        min-width: 0;
        width: auto;
        background: transparent;
        border: none;
        color: #9ca3af;
        padding: 0 1;
    }

    .filter-button.active {
        color: #4db6ac;
        text-style: bold;
    }

    #paused-banner {
        height: 1;
        background: #ffca28;
        color: black;
        text-style: bold;
        content-align: center middle;
    }

    #event-log {
        height: 1fr;
        border: round #2d333b;
        background: #0a0a0a;
        padding: 0 1;
    }

    #command-bar {
        height: 3;
        background: #1c2026;
        border-top: solid #2d333b;
        padding: 0 1;
        layout: horizontal;
        align: center middle;
    }

    #command-actions {
        width: 1fr;
        layout: horizontal;
    }

    .command-button {
        min-width: 0;
        width: auto;
        background: transparent;
        border: none;
        color: #c3c7ce;
        padding: 0 1;
        margin-right: 1;
    }

    .command-button:hover {
        color: white;
    }

    #runtime-lamp {
        width: auto;
        content-align: right middle;
    }
    """

    BINDINGS = [
        Binding("s", "start_runtime", "Start"),
        Binding("x", "stop_runtime", "Stop"),
        Binding("ctrl+r", "restart_runtime", "Reboot"),
        Binding("b", "rebuild_runtime", "Rebuild"),
        Binding("t", "run_test_ci", "Test-CI"),
        Binding("v", "run_verify", "Verify"),
        Binding("1", "filter_all", "All"),
        Binding("2", "filter_pool", "Pool"),
        Binding("3", "filter_system", "System"),
        Binding("p", "toggle_pause", "Pause"),
        Binding("o", "show_url", "Open WebUI"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, args: argparse.Namespace, env: dict[str, str]) -> None:
        super().__init__()
        self.args = args
        self.env = env
        self.profile = load_profile(args.profile)
        self.session_id = now_session_id(args.profile)
        self.event_buffer = EventBuffer(Path(args.out_dir))
        self.runner = ActionRunner(env, args, self.event_buffer)
        self.adapter = DashboardStateAdapter(args, env, self.profile, self.event_buffer)
        self.current_filter = FILTER_ALL
        self.paused = False
        self.snapshot: DashboardSnapshot | None = None

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static("", id="header-left"),
            Static("", id="header-right"),
            id="header-bar",
        )
        with Horizontal(id="main-layout"):
            with Vertical(id="left-column"):
                with Vertical(id="identity-card", classes="card"):
                    yield Static(id="identity-card-view")
                    yield Button("", id="show-web-url")
                yield Static(id="mining-card", classes="card")
                yield Static(id="asic-card-list", classes="card")
                yield Static(id="stats-card", classes="card")
            with Vertical(id="right-column"):
                yield Static(id="health-card", classes="card")
                with Vertical(id="event-card"):
                    yield Horizontal(
                        Static("EVENT_FEED_VIRT", id="event-title"),
                        Horizontal(
                            Button("ALL", id="filter-all", classes="filter-button active"),
                            Button("POOL", id="filter-pool", classes="filter-button"),
                            Button("SYS", id="filter-system", classes="filter-button"),
                            id="event-filter-bar",
                        ),
                        id="event-header",
                    )
                    yield Static("", id="paused-banner")
                    yield RichLog(id="event-log", wrap=True, highlight=False, markup=False, auto_scroll=True)
        yield Horizontal(
            Horizontal(
                Button("S START", id="cmd-start", classes="command-button"),
                Button("X STOP", id="cmd-stop", classes="command-button"),
                Button("^R REBOOT", id="cmd-restart", classes="command-button"),
                Button("B REBUILD", id="cmd-rebuild", classes="command-button"),
                Button("T TEST-CI", id="cmd-test-ci", classes="command-button"),
                Button("V VERIFY", id="cmd-verify", classes="command-button"),
                Button("1-3 LOG", id="cmd-filter", classes="command-button"),
                Button("P PAUSE", id="cmd-pause", classes="command-button"),
                Button("O WEBUI", id="cmd-url", classes="command-button"),
                Button("Q QUIT", id="cmd-quit", classes="command-button"),
                id="command-actions",
            ),
            Static("", id="runtime-lamp"),
            id="command-bar",
        )

    def on_mount(self) -> None:
        self.query_one("#paused-banner", Static).display = False
        if self.args.auto_start and not runtime_is_active(self.env.get("QEMU_CONTAINER_NAME", "virtualaxe-qemu"), Path(self.args.out_dir)):
            self.runner.start("start", self.runner.start_runtime)
        self.set_interval(0.75, self.refresh_view)
        self.refresh_view()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id in {"cmd-start"}:
            self.action_start_runtime()
        elif button_id in {"cmd-stop"}:
            self.action_stop_runtime()
        elif button_id in {"cmd-restart"}:
            self.action_restart_runtime()
        elif button_id in {"cmd-rebuild"}:
            self.action_rebuild_runtime()
        elif button_id in {"cmd-test-ci"}:
            self.action_run_test_ci()
        elif button_id in {"cmd-verify"}:
            self.action_run_verify()
        elif button_id in {"cmd-filter"}:
            self.action_cycle_filter()
        elif button_id in {"cmd-pause"}:
            self.action_toggle_pause()
        elif button_id in {"cmd-url"}:
            self.action_show_url()
        elif button_id in {"cmd-quit"}:
            self.action_quit()
        elif button_id in {"filter-all", "filter-pool", "filter-system"}:
            mapping = {
                "filter-all": FILTER_ALL,
                "filter-pool": FILTER_POOL,
                "filter-system": FILTER_SYS,
            }
            self.set_filter(mapping[button_id])
        elif button_id == "show-web-url":
            self.action_show_url()

    def set_filter(self, value: str) -> None:
        self.current_filter = value
        for button_id in ("filter-all", "filter-pool", "filter-system"):
            button = self.query_one(f"#{button_id}", Button)
            button.remove_class("active")
        active_button = {
            FILTER_ALL: "#filter-all",
            FILTER_POOL: "#filter-pool",
            FILTER_SYS: "#filter-system",
        }[value]
        self.query_one(active_button, Button).add_class("active")
        self.render_event_log()

    def action_filter_all(self) -> None:
        self.set_filter(FILTER_ALL)

    def action_filter_pool(self) -> None:
        self.set_filter(FILTER_POOL)

    def action_filter_system(self) -> None:
        self.set_filter(FILTER_SYS)

    def action_cycle_filter(self) -> None:
        if self.current_filter == FILTER_ALL:
            self.set_filter(FILTER_POOL)
        elif self.current_filter == FILTER_POOL:
            self.set_filter(FILTER_SYS)
        else:
            self.set_filter(FILTER_ALL)

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        banner = self.query_one("#paused-banner", Static)
        banner.display = self.paused
        banner.update("!! STREAM PAUSED !!" if self.paused else "")
        self.event_buffer.add_manual_event(SYSTEM_SOURCE, "Event stream paused." if self.paused else "Event stream resumed.")
        self.render_event_log()

    def action_show_url(self) -> None:
        url = self.snapshot.web_url if self.snapshot else f"http://127.0.0.1:{self.args.http_port}/"
        opened = webbrowser.open(url)
        self.event_buffer.add_manual_event(
            SYSTEM_SOURCE,
            f"Opened WebUI: {url}" if opened else f"WebUI URL: {url}",
        )
        if not opened:
            self.push_screen(UrlOverlayScreen(url))

    def action_start_runtime(self) -> None:
        self.runner.start("start", self.runner.start_runtime)

    def action_stop_runtime(self) -> None:
        self.runner.start("stop", self.runner.stop_runtime)

    def action_restart_runtime(self) -> None:
        self.runner.start("restart", self.runner.restart)

    def action_rebuild_runtime(self) -> None:
        self.runner.start("rebuild", self.runner.rebuild_and_start)

    def action_run_test_ci(self) -> None:
        self.runner.start("test-ci", self.runner.run_test_ci)

    def action_run_verify(self) -> None:
        self.runner.start("verify", self.runner.run_local_verify)

    def refresh_view(self) -> None:
        self.snapshot = self.adapter.refresh(self.runner, self.session_id)
        self.render_header()
        self.render_identity_card()
        self.render_mining_card()
        self.render_asic_cards()
        self.render_stats_card()
        self.render_health_card()
        if not self.paused:
            self.render_event_log()
        self.render_footer()

    def render_header(self) -> None:
        assert self.snapshot is not None
        left = Text()
        left.append("virtualaxe-operator.sys", style="bold underline #4db6ac")
        left.append(f"   SESSION: {self.snapshot.session_id}", style="italic #6b7280")
        left.append(f"   PROFILE: {self.snapshot.profile_name.upper()}", style="bold white")
        right = Text()
        api_style = "#4caf50" if self.snapshot.api_online else "#ef5350"
        right.append(f"API: {'ONLINE' if self.snapshot.api_online else 'OFFLINE'}", style=f"bold {api_style}")
        right.append("   ")
        right.append(self.snapshot.runtime_label, style=f"bold {self.snapshot.runtime_color}")
        right.append("   ")
        right.append(time.strftime("%H:%M:%S"), style="#9ca3af")
        self.query_one("#header-left", Static).update(left)
        self.query_one("#header-right", Static).update(right)

    def render_identity_card(self) -> None:
        assert self.snapshot is not None
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(style="#6b7280", ratio=1)
        grid.add_column(style="white", ratio=2)
        grid.add_row("Board:", self.snapshot.board_version)
        grid.add_row("Firmware:", self.snapshot.firmware_version)
        grid.add_row("AxeOS:", self.snapshot.axeos_version)
        grid.add_row("Web Access:", "")
        card = Group(
            grid,
            Text(""),
            Text.from_markup(f"[#6b7280]WebUI:[/] [bold #81d4fa]{self.snapshot.web_url}[/]"),
        )
        panel = Panel(
            card,
            title=(
                f"[bold #4db6ac]{source_identity_label(self.args.source)}[/]  "
                f"[bold white]{self.snapshot.profile_name}[/]"
            ),
            border_style="#2d333b",
            box=box.ROUNDED,
        )
        self.query_one("#identity-card-view", Static).update(panel)
        self.query_one("#show-web-url", Button).label = "OPEN WEBUI"

    def render_mining_card(self) -> None:
        assert self.snapshot is not None
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(style="#6b7280", ratio=1)
        grid.add_column(style="white", ratio=3)
        grid.add_row("PRIMARY", self.snapshot.primary_pool)
        grid.add_row("FALLBACK", self.snapshot.fallback_pool)
        panel = Panel(grid, title="[bold #9ccc65]MINING_CONFIG[/]", border_style="#2d333b", box=box.ROUNDED)
        self.query_one("#mining-card", Static).update(panel)

    def render_asic_cards(self) -> None:
        assert self.snapshot is not None
        cards: list[Any] = []
        for worker in self.snapshot.workers:
            grid = Table.grid(expand=True, padding=(0, 1))
            grid.add_column(style="#6b7280", ratio=1)
            grid.add_column(style="white", ratio=1)
            grid.add_column(style="#6b7280", ratio=1)
            grid.add_column(style="white", ratio=1)
            grid.add_row("State", worker.state, "Lane", f"{worker.lane_offset}/{worker.lane_stride}")
            grid.add_row("Jobs", str(worker.jobs_assigned), "Model", worker.model)
            grid.add_row("Worker", f"ASIC_{worker.asic_nr:02d}", "Last", worker.last_event)
            cards.append(
                Panel(
                    grid,
                    title=f"[bold #ff7043]ASIC_{worker.asic_nr:02d}[/]  [bold white]{worker.state}[/]",
                    border_style="#ff7043",
                    box=box.ROUNDED,
                )
            )

        body = Group(*cards) if cards else Text("No ASIC workers in scope.", style="#6b7280")
        panel = Panel(body, title="[bold #ff7043]ASIC_LANE_REPORTS[/]", border_style="#2d333b", box=box.ROUNDED)
        self.query_one("#asic-card-list", Static).update(panel)

    def render_stats_card(self) -> None:
        assert self.snapshot is not None
        stats = Table.grid(expand=True, padding=(0, 1))
        stats.add_column(style="#6b7280", ratio=1)
        stats.add_column(style="bold #4caf50", ratio=1)
        stats.add_column(style="#6b7280", ratio=1)
        stats.add_column(style="white", ratio=1)
        stats.add_row("Shares (Acc/Rej)", f"{self.snapshot.accepted_shares} / {self.snapshot.rejected_shares}", "Best Diff", self.snapshot.best_diff)
        stats.add_row("Uptime", self.snapshot.uptime, "Pool Diff", self.snapshot.pool_difficulty)
        stats.add_row("Pool State", self.snapshot.pool_state, "IPv4", self.snapshot.ipv4)
        stats.add_row("Temp", self.snapshot.temp, "Fan", self.snapshot.fan_rpm)
        stats.add_row("Power", self.snapshot.power, "Voltage", self.snapshot.voltage)
        panel = Panel(stats, title="[bold #4db6ac]RUNTIME STATS / SENSORS[/]", border_style="#2d333b", box=box.ROUNDED)
        self.query_one("#stats-card", Static).update(panel)

    def render_health_card(self) -> None:
        assert self.snapshot is not None
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(style="#6b7280", ratio=1)
        table.add_column(ratio=1)
        table.add_column(style="white", ratio=3)

        rows = [
            ("Build State", self.snapshot.build_state),
            ("Virt-Patches", self.snapshot.patch_state),
            ("Firmware Boot", self.snapshot.boot_state),
            ("API WebUI", self.snapshot.api_state),
            ("Persistence", self.snapshot.persistence_state),
            ("test-ci", self.snapshot.test_ci_state),
            ("Pool Smoke", self.snapshot.release_gate_state),
        ]
        for label, (state, detail) in rows:
            table.add_row(label, Text(state, style=f"bold {status_to_style(state)}"), detail)

        group = Group(
            table,
            Text(""),
            Text("CURRENT ACTION", style="bold #6b7280"),
            Text(self.snapshot.action_summary, style="bold #81d4fa"),
        )
        panel = Panel(group, title="[bold #ba68c8]SYSTEM CHECKS[/]", border_style="#2d333b", box=box.ROUNDED)
        self.query_one("#health-card", Static).update(panel)

    def render_event_log(self) -> None:
        if self.snapshot is None:
            return
        log_widget = self.query_one("#event-log", RichLog)
        log_widget.clear()
        events = [event for event in self.snapshot.events if event_matches_filter(event.source, self.current_filter)]
        if not events:
            log_widget.write(Text("-- NO EVENTS IN SCOPE --", style="italic #6b7280"))
            return
        for event in events[-MAX_LOG_RENDER:]:
            prefix = Text(f"[{event.source.upper():>5}] ", style=f"bold {event_source_color(event.source, event.severity)}")
            body = Text(event.message, style="#c3c7ce" if event.severity != "error" else "#ef5350")
            timestamp = Text(short_time(event.timestamp) + " ", style="#6b7280")
            log_widget.write(Text.assemble(timestamp, prefix, body), scroll_end=not self.paused)

    def render_footer(self) -> None:
        assert self.snapshot is not None
        lamp = Text()
        if self.snapshot.runtime_label == "RUNNING":
            lamp.append("● ", style="bold #4caf50")
            lamp.append("SYSTEM ACTIVE", style="bold #4caf50")
        elif self.snapshot.runtime_label in {"BUILDING", "BOOTING"}:
            lamp.append("● ", style="bold #ffca28")
            lamp.append(self.snapshot.runtime_label, style="bold #ffca28")
        else:
            lamp.append("● ", style="bold #ef5350")
            lamp.append("SYSTEM HALTED", style="bold #ef5350")
        self.query_one("#runtime-lamp", Static).update(lamp)


def main() -> int:
    args = parse_args()
    web_http_port = args.web_http_port or args.http_port
    env = os.environ.copy()
    env["HTTP_PORT"] = str(args.http_port)
    env["BASE_URL"] = f"http://127.0.0.1:{web_http_port}"
    env["OUT_DIR"] = args.out_dir
    env["STATE_DIR"] = args.state_dir
    env["VIRTUALAXE_DISABLE_TEE"] = "1"
    app = BitaxeDashboardApp(args, env)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
