#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TextIO


ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_registry import load_source_registry

SOURCES_FILE = ROOT_DIR / "configs" / "sources.json"
PROFILES_DIR = ROOT_DIR / "configs" / "profiles"
WORKTREES_DIR = ROOT_DIR / ".worktrees"
SOURCES_CACHE_DIR = ROOT_DIR / ".sources"
STATE_ROOT = ROOT_DIR / ".state"
DASHBOARD_SCRIPT = ROOT_DIR / "scripts" / "virtualaxe_dashboard.py"
ENSURE_TEST_PYTHON = ROOT_DIR / "scripts" / "ensure-test-python.sh"
VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
VERIFY_RELEASE_SCRIPT = ROOT_DIR / "scripts" / "verify-release.py"
RUN_QEMU_NAT_SCRIPT = ROOT_DIR / "scripts" / "run-qemu-nat.sh"
DEFAULT_POOL_HOST = "public-pool.io"
DEFAULT_POOL_PORT = "3333"
DEFAULT_HTTP_PORT = 18080
DEFAULT_POOL_USER = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
DEFAULT_POOL_PASS = "x"
DEFAULT_POOL_DIFF = 0.0001
DEFAULT_POOL_TLS = 0
DEFAULT_POOL_CERT = "x"
DEFAULT_VERIFY_RELEASE_POOL_USER = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
DEFAULT_HOSTNAME = "virtualaxe"
DEFAULT_VIRTUAL_ASIC_MODE = "cpu"
DEFAULT_SUBMIT_REPLAY_GUEST_POOL_HOST = "10.0.2.2"
DEFAULT_SUBMIT_REPLAY_PORT = 3334
DEFAULT_SUBMIT_REPLAY_HTTP_PORT = 18081
DEFAULT_SUBMIT_REPLAY_DIFFICULTY = 0.000001
DEFAULT_SUBMIT_REPLAY_TIMEOUT = 300.0
DEFAULT_SUBMIT_REPLAY_USER = "bc1qvirtualaxereplay.worker"
DEFAULT_SUBMIT_REPLAY_CONTAINER_NAME = "virtualaxe-submit-replay-qemu"
DEFAULT_SUBMIT_REPLAY_EXTRANONCE1 = "01000000"
DEFAULT_SUBMIT_REPLAY_EXTRANONCE2_SIZE = 4
BUILD_INPUT_DIRS = (
    ROOT_DIR / "patches" / "esp-miner",
    ROOT_DIR / "configs",
)
BUILD_INPUT_FILES = (
    ROOT_DIR / "scripts" / "apply-patches.sh",
    ROOT_DIR / "scripts" / "build-virtual.sh",
    ROOT_DIR / "scripts" / "render-virtual-config.py",
    ROOT_DIR / "scripts" / "sync-upstream.sh",
)
BUILD_PROGRESS_RE = re.compile(
    r"^\[virtualAxe\] \[(?P<elapsed>[^\]]+)\] (?:(?P<step>\d+)/(?P<total>\d+): )?(?P<message>.*)$"
)
BUILD_REFERENCE_SECONDS = {
    "bitaxe": 7 * 60 + 52,
    "nerdnos": 4 * 60 + 4,
}
BUILD_STAGE_LABELS = (
    "Prepare output",
    "Select build environment",
    "Prepare patched worktree",
    "Check AxeOS dependencies",
    "Render virtual config",
    "Prepare NVS state",
    "Build firmware image",
    "Write manifest",
)
BUILD_WAIT_NOTES = (
    "Full compiler output is in build.log.",
    "The QEMU flash image is reusable.",
    "Upstream firmware stays in ignored .sources/ state.",
    "AxeOS assets build inside the patched worktree.",
    "Mining logic stays inside guest firmware.",
    "Matching manifests let later runs reuse the image.",
)
BUILD_COCKPIT_WIDTH = 78
BUILD_ACTIVITY_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
REPO_CONFIG_ENV_KEYS = {
    "BACKGROUND",
    "BASE_URL",
    "BOARD",
    "CONTAINER_IMAGE",
    "FALLBACK_POOL_CERT",
    "FALLBACK_POOL_DIFF",
    "FALLBACK_POOL_HOST",
    "FALLBACK_POOL_PASS",
    "FALLBACK_POOL_PORT",
    "FALLBACK_POOL_SUBSCRIBE_AGENT",
    "FALLBACK_POOL_TLS",
    "FALLBACK_POOL_USER",
    "HOSTNAME_VALUE",
    "HTTP_PORT",
    "LOCAL_STRATUM_PORT",
    "OUT_DIR",
    "PATCH_ALREADY_APPLIED",
    "POOL_CERT",
    "POOL_DIFF",
    "POOL_HOST",
    "POOL_PASS",
    "POOL_PORT",
    "POOL_SUBSCRIBE_AGENT",
    "POOL_TLS",
    "POOL_USER",
    "QEMU_CONTAINER_NAME",
    "RESET_PERSISTED_STATE",
    "SOURCE_NAME",
    "SOURCE_RELEASE_TAG",
    "SOURCE_REPO_URL",
    "SOURCE_RESOLVED_COMMIT",
    "STATE_DIR",
    "SIM_ACTIONS_ENABLED",
    "SIM_BACKEND_HTTP_PORT",
    "SIM_HTTP_PORT",
    "STRATUM_REPLAY_DIFFICULTY",
    "STRATUM_REPLAY_EXTRANONCE1",
    "STRATUM_REPLAY_EXTRANONCE2_SIZE",
    "STRATUM_REPLAY_HOST",
    "STRATUM_REPLAY_PORT",
    "STRATUM_REPLAY_TIMEOUT",
    "STRATUM_REPLAY_USERNAME",
    "UPSTREAM_DIR",
    "VIRTUAL_ASIC_MODE",
    "VIRTUAL_PROFILE",
    "VIRTUAL_PROFILE_FILE",
    "VIRTUAL_TRANSPORT_HOST",
    "VIRTUAL_TRANSPORT_PORT",
    "VIRTUALAXE_DISABLE_TEE",
    "VIRTUAL_BITAXE_SIM_BACKEND_PORT",
    "VIRTUAL_BITAXE_SIM_ACTIONS",
    "WEB_HTTP_PORT",
}


def clean_process_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in REPO_CONFIG_ENV_KEYS}
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def load_sources() -> dict[str, Any]:
    return load_source_registry(SOURCES_FILE).as_legacy_payload(include_aliases=True)


def source_registry():
    return load_source_registry(SOURCES_FILE)


def source_metadata(source_name: str | None = None):
    return source_registry().get(source_name)


def source_support_error_message(source_name: str, minimum_state: str, command: str) -> str:
    registry = source_registry()
    source = registry.get(source_name)
    canonical = registry.canonical_name(source_name)
    return (
        f"Source '{canonical}' is {source.support_state}; {command} requires {minimum_state}. "
        f"Run `make build SOURCE={canonical}` to reproduce the verified build state, "
        f"or `make patch-check SOURCE={canonical}` to verify patch application. "
        "Use `vaxe --source bitaxe` for the submit-replay verified virtual Gamma source."
    )


def completed_output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def require_source_support(source_name: str, minimum_state: str, command: str) -> None:
    source = source_metadata(source_name)
    if not source.supports(minimum_state):
        raise SystemExit(source_support_error_message(source_name, minimum_state, command))


def default_output_dir(source_name: str, profile_id: str) -> Path:
    canonical = source_registry().canonical_name(source_name)
    if canonical == "bitaxe":
        return ROOT_DIR / "out"
    return ROOT_DIR / "out" / canonical / profile_id


def default_submit_replay_output_dir(source_name: str, profile_id: str) -> Path:
    canonical = source_registry().canonical_name(source_name)
    if canonical == "bitaxe":
        return ROOT_DIR / "out" / "submit-replay"
    return ROOT_DIR / "out" / "submit-replay" / canonical / profile_id


def runtime_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return token or "default"


def qemu_container_name(source_name: str, profile_id: str) -> str:
    canonical = source_registry().canonical_name(source_name)
    return f"virtualaxe-qemu-{runtime_token(canonical)}-{runtime_token(profile_id)}"


def managed_qemu_runtime_specs(profile_id: str) -> list[tuple[str, Path]]:
    registry = source_registry()
    specs = [("virtualaxe-qemu", ROOT_DIR / "out")]
    for source_name in sorted(registry.sources):
        specs.append((qemu_container_name(source_name, profile_id), default_output_dir(source_name, profile_id)))
    return specs


def load_profile(profile_name: str) -> dict[str, Any]:
    path = PROFILES_DIR / f"{profile_name}.json"
    if not path.is_file():
        raise SystemExit(f"Unknown profile {profile_name!r}. Expected {path}.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["path"] = str(path)
    return payload


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = clean_process_env()
    if env is not None:
        merged_env.update(env)

    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            env=merged_env,
            text=True,
            capture_output=capture,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            exc.cmd,
            124,
            completed_output_text(exc.stdout),
            completed_output_text(exc.stderr) or f"Command timed out after {timeout} seconds.",
        )


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def format_expected_build_time(source_name: str) -> str:
    expected = BUILD_REFERENCE_SECONDS.get(source_registry().canonical_name(source_name))
    if expected is None:
        return "several minutes"
    return f"{format_elapsed(expected)} reference on this machine"


def format_expected_build_time_short(source_name: str) -> str:
    expected = BUILD_REFERENCE_SECONDS.get(source_registry().canonical_name(source_name))
    if expected is None:
        return "several minutes"
    return format_elapsed(expected)


def progress_bar(step: int, total: int, *, width: int = 24) -> str:
    if total <= 0:
        return "[" + "." * width + "]"
    filled = min(width, max(0, round(width * step / total)))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def cockpit_stage_sequence(step: int, total: int, status: str) -> str:
    if total <= 0:
        return "○"
    current = min(max(step, 0), total)
    markers: list[str] = []
    for index in range(1, total + 1):
        if index < current or (index == current and status == "COMPLETE"):
            markers.append("✓")
        elif index == current and status == "FAILED":
            markers.append("✕")
        elif index == current and status == "BUILDING":
            markers.append("●")
        else:
            markers.append("○")
    return " ".join(markers)


def format_byte_count(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


@dataclass
class BuildCockpitState:
    source_name: str
    profile_id: str
    out_dir: Path
    log_path: Path
    started_at: float
    current_step: int = 0
    total_steps: int = len(BUILD_STAGE_LABELS)
    current_message: str = "Initializing build"
    last_marker: str = "Initializing build"
    last_activity_at: float | None = None
    current_stage_started_at: float | None = None
    log_bytes: int = 0
    log_lines: int = 0
    note_index: int = 0
    status: str = "BUILDING"
    returncode: int | None = None


def fit_text(text: object, width: int, *, keep: str = "start") -> str:
    value = str(text)
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    if keep == "end":
        return "..." + value[-(width - 3):]
    return value[: width - 3] + "..."


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def fit_path(path: Path, width: int = 56) -> str:
    return fit_text(display_path(path), width, keep="end")


def cockpit_rule(label: str = "", *, width: int = BUILD_COCKPIT_WIDTH) -> str:
    if not label:
        return "├" + "─" * (width - 2) + "┤"
    text = f" {label} "
    remaining = max(0, width - len(text) - 2)
    return "├" + text + "─" * remaining + "┤"


def cockpit_unicode_line(left: str = "", right: str = "", *, width: int = BUILD_COCKPIT_WIDTH) -> str:
    inner_width = width - 4
    if right:
        right_text = fit_text(right, max(0, inner_width - 1))
        left_width = max(0, inner_width - len(right_text) - 1)
        body = f"{fit_text(left, left_width):<{left_width}} {right_text}"
    else:
        body = fit_text(left, inner_width)
    return f"│ {body:<{inner_width}} │"


def decorate_cockpit_line(line: str, *, color: bool) -> str:
    if not color:
        return line
    replacements = {
        "virtualAxe build cockpit": "36;1",
        "BUILDING": "33;1",
        "COMPLETE": "32;1",
        "FAILED": "31;1",
        "●": "33;1",
        "✓": "32;1",
        "✕": "31;1",
    }
    decorated = line
    for token, code in replacements.items():
        decorated = decorated.replace(token, colorize(token, code, enabled=True))
    return decorated


def colorize(text: str, code: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def should_render_build_cockpit(stream: TextIO, env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    if values.get("CI"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def should_colorize_build_cockpit(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    term = values.get("TERM", "")
    return "NO_COLOR" not in values and term not in ("", "dumb", "unknown")


def set_build_cockpit_cursor_visible(stream: TextIO, visible: bool) -> None:
    stream.write("\033[?25h" if visible else "\033[?25l")
    stream.flush()


def build_cockpit_lines(state: BuildCockpitState, *, now: float | None = None, color: bool = False) -> list[str]:
    current_time = time.monotonic() if now is None else now
    elapsed = format_elapsed(current_time - state.started_at)
    status_text = state.status
    if state.returncode not in (None, 0):
        status_text = f"FAILED rc={state.returncode}"
    qemu_image = state.out_dir / "qemu_flash.bin"
    manifest = state.out_dir / "manifest.json"
    stage_total = max(1, state.total_steps)
    stage = min(max(state.current_step, 0), stage_total)
    activity_at = state.last_activity_at if state.last_activity_at is not None else state.started_at
    activity_age = format_elapsed(current_time - activity_at)
    stage_started_at = state.current_stage_started_at if state.current_stage_started_at is not None else state.started_at
    stage_age = format_elapsed(current_time - stage_started_at)
    updated = time.strftime("%H:%M:%S")
    note = BUILD_WAIT_NOTES[state.note_index % len(BUILD_WAIT_NOTES)]
    top = "╭" + "─" * (BUILD_COCKPIT_WIDTH - 2) + "╮"
    bottom = "╰" + "─" * (BUILD_COCKPIT_WIDTH - 2) + "╯"
    current_stage_label = BUILD_STAGE_LABELS[stage - 1] if 1 <= stage <= len(BUILD_STAGE_LABELS) else "Initializing"
    activity_marker = "✓" if state.status == "COMPLETE" else ("✕" if state.status == "FAILED" else BUILD_ACTIVITY_SPINNER[int(current_time) % len(BUILD_ACTIVITY_SPINNER)])
    log_summary = f"{state.log_lines:,} lines / {format_byte_count(state.log_bytes)}"
    lines = [
        top,
        cockpit_unicode_line("virtualAxe build cockpit", status_text),
        cockpit_unicode_line(f"{state.source_name}/{state.profile_id}", f"elapsed {elapsed}"),
        cockpit_unicode_line(f"stage {stage}/{stage_total}: {current_stage_label}", f"updated {updated}"),
        cockpit_unicode_line(f"reference {format_expected_build_time_short(state.source_name)}", "local clean build"),
        cockpit_rule("outputs"),
        cockpit_unicode_line(f"image     {fit_path(qemu_image, 58)}"),
        cockpit_unicode_line(f"manifest  {fit_path(manifest, 58)}"),
        cockpit_unicode_line(f"log       {fit_path(state.log_path, 58)}"),
        cockpit_rule("progress"),
        cockpit_unicode_line(f"overall   {cockpit_stage_sequence(stage, stage_total, state.status)}", f"stage {stage}/{stage_total}"),
        cockpit_unicode_line(f"active    {activity_marker} {current_stage_label}, running {stage_age}"),
        cockpit_unicode_line(f"log       {log_summary}", f"updated {activity_age} ago"),
        cockpit_unicode_line(f"current   {state.current_message}"),
        cockpit_rule("stage rail"),
    ]
    for row_start in range(1, len(BUILD_STAGE_LABELS) + 1, 2):
        entries: list[str] = []
        for index in range(row_start, min(row_start + 2, len(BUILD_STAGE_LABELS) + 1)):
            label = BUILD_STAGE_LABELS[index - 1]
            if index < stage or (index == stage and state.status == "COMPLETE"):
                marker = "✓"
            elif index == stage and state.status == "FAILED":
                marker = "✕"
            elif index == stage:
                marker = "●"
            else:
                marker = "○"
            entries.append(f"{marker} {index}. {label}")
        lines.append(cockpit_unicode_line(f"{entries[0]:<34}{entries[1] if len(entries) > 1 else ''}"))
    lines.extend(
        [
            cockpit_rule("flight recorder"),
            cockpit_unicode_line(note),
            bottom,
        ]
    )
    return [decorate_cockpit_line(line, color=color) for line in lines]


def write_ansi_dashboard(lines: list[str], previous_line_count: int, stream: TextIO) -> int:
    stream.write("\033[H\033[2J")
    for line in lines:
        stream.write(line + "\n")
    stream.flush()
    return len(lines)


def parse_build_progress_line(line: str) -> dict[str, Any] | None:
    match = BUILD_PROGRESS_RE.match(line)
    if not match:
        return None
    step_text = match.group("step")
    total_text = match.group("total")
    return {
        "elapsed": match.group("elapsed"),
        "step": int(step_text) if step_text else None,
        "total": int(total_text) if total_text else None,
        "message": match.group("message").strip(),
    }


def render_build_progress_line(line: str) -> tuple[str, int | None, int | None, str]:
    parsed = parse_build_progress_line(line)
    if parsed is None:
        message = line.removeprefix("[virtualAxe]").strip()
        return f"[virtualAxe] note: {message}", None, None, message

    message = parsed["message"]
    step = parsed["step"]
    total = parsed["total"]
    if isinstance(step, int) and isinstance(total, int):
        return (
            f"[virtualAxe] {progress_bar(step, total)} {step}/{total} {message} "
            f"elapsed {parsed['elapsed']}",
            step,
            total,
            message,
        )
    return f"[virtualAxe] note: {message} ({parsed['elapsed']})", None, None, message


def print_build_dashboard_header(
    *,
    source_name: str,
    profile_id: str,
    out_dir: Path,
    log_path: Path,
) -> None:
    qemu_image = out_dir / "qemu_flash.bin"
    print("[virtualAxe] Build dashboard", file=sys.stderr, flush=True)
    print(f"[virtualAxe]   source/profile: {source_name}/{profile_id}", file=sys.stderr, flush=True)
    print(
        f"[virtualAxe]   expected cold build: {format_expected_build_time(source_name)}",
        file=sys.stderr,
        flush=True,
    )
    print(f"[virtualAxe]   reusable image: {qemu_image}", file=sys.stderr, flush=True)
    print(f"[virtualAxe]   full log: {log_path}", file=sys.stderr, flush=True)
    print(
        "[virtualAxe]   terminal view: milestones, elapsed time, and short build notes",
        file=sys.stderr,
        flush=True,
    )


def print_build_dashboard_footer(
    *,
    source_name: str,
    profile_id: str,
    out_dir: Path,
    log_path: Path,
    elapsed: str,
    succeeded: bool,
) -> None:
    qemu_image = out_dir / "qemu_flash.bin"
    manifest = out_dir / "manifest.json"
    source_label = source_registry().canonical_name(source_name)
    if succeeded:
        print(f"[virtualAxe] QEMU firmware image ready in {elapsed}.", file=sys.stderr, flush=True)
        print(f"[virtualAxe]   image:    {display_path(qemu_image)}", file=sys.stderr, flush=True)
        print(f"[virtualAxe]   manifest: {display_path(manifest)}", file=sys.stderr, flush=True)
        print(f"[virtualAxe]   boot it:  ./vaxe --source {source_label}", file=sys.stderr, flush=True)
        print(f"[virtualAxe]   test it:  make verify-submit-replay SOURCE={source_label}", file=sys.stderr, flush=True)
        print(f"[virtualAxe]   rebuild:  make build SOURCE={source_label}", file=sys.stderr, flush=True)
    else:
        print(f"[virtualAxe] Build failed after {elapsed}.", file=sys.stderr, flush=True)
        print(f"[virtualAxe]   full log: {display_path(log_path)}", file=sys.stderr, flush=True)
        print(f"[virtualAxe]   source/profile: {source_name}/{profile_id}", file=sys.stderr, flush=True)
        print(f"[virtualAxe]   retry: make build SOURCE={source_label}", file=sys.stderr, flush=True)


def build_log_tail(log_path: Path, *, max_lines: int = 12) -> list[str]:
    if not log_path.is_file():
        return []
    lines = [line.rstrip() for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()]
    return [line for line in lines if line][-max_lines:]


def print_build_failure_tail(log_path: Path) -> None:
    tail = build_log_tail(log_path)
    if not tail:
        return
    print("[virtualAxe] Last build log lines:", file=sys.stderr, flush=True)
    for line in tail:
        print(f"[virtualAxe]   {line}", file=sys.stderr, flush=True)


def run_build_with_progress(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    command = [str(ROOT_DIR / "scripts" / "build-virtual.sh")]
    merged_env = clean_process_env()
    merged_env.update(env)
    merged_env["VIRTUALAXE_DISABLE_TEE"] = "1"

    out_dir = Path(merged_env["OUT_DIR"])
    log_path = out_dir / "build.log"
    source_name = merged_env.get("SOURCE_NAME", "bitaxe")
    profile_id = merged_env.get("VIRTUAL_PROFILE", "gamma")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    start = time.monotonic()
    offset = 0
    state = BuildCockpitState(
        source_name=source_name,
        profile_id=profile_id,
        out_dir=out_dir,
        log_path=log_path,
        started_at=start,
        last_activity_at=start,
    )
    last_render = start
    last_note_rotation = start
    cockpit_enabled = should_render_build_cockpit(sys.stderr)
    cockpit_lines = 0
    cockpit_color = should_colorize_build_cockpit()
    if cockpit_enabled:
        set_build_cockpit_cursor_visible(sys.stderr, False)
        cockpit_lines = write_ansi_dashboard(
            build_cockpit_lines(state, now=start, color=cockpit_color),
            cockpit_lines,
            sys.stderr,
        )
    else:
        print_build_dashboard_header(
            source_name=source_name,
            profile_id=profile_id,
            out_dir=out_dir,
            log_path=log_path,
        )
    process = subprocess.Popen(
        command,
        cwd=str(ROOT_DIR),
        env=merged_env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def drain_progress() -> None:
        nonlocal offset, cockpit_lines
        if not log_path.exists():
            return
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            chunk = handle.read()
            offset = handle.tell()
        if chunk:
            now = time.monotonic()
            state.last_activity_at = now
            state.log_lines += chunk.count("\n")
            try:
                state.log_bytes = log_path.stat().st_size
            except OSError:
                state.log_bytes = max(state.log_bytes, len(chunk.encode("utf-8", errors="replace")))
        for line in chunk.splitlines():
            if line.startswith("[virtualAxe]"):
                rendered, step, total, marker = render_build_progress_line(line)
                if step is not None and total is not None:
                    if step != state.current_step:
                        state.current_stage_started_at = time.monotonic()
                    state.current_step = step
                    state.total_steps = total
                    state.current_message = marker
                state.last_marker = marker
                if cockpit_enabled:
                    cockpit_lines = write_ansi_dashboard(
                        build_cockpit_lines(state, color=cockpit_color),
                        cockpit_lines,
                        sys.stderr,
                    )
                else:
                    print(rendered, file=sys.stderr, flush=True)

    while process.poll() is None:
        drain_progress()
        now = time.monotonic()
        if cockpit_enabled and now - last_note_rotation >= 20:
            state.note_index += 1
            last_note_rotation = now
        if cockpit_enabled and now - last_render >= 1:
            cockpit_lines = write_ansi_dashboard(
                build_cockpit_lines(state, now=now, color=cockpit_color),
                cockpit_lines,
                sys.stderr,
            )
            last_render = now
        elif not cockpit_enabled and now - last_render >= 30:
            if state.current_step:
                stage = f"{progress_bar(state.current_step, state.total_steps)} {state.current_step}/{state.total_steps}"
            else:
                stage = progress_bar(0, 1)
            note = BUILD_WAIT_NOTES[state.note_index % len(BUILD_WAIT_NOTES)]
            print(
                f"[virtualAxe] {stage} {format_elapsed(now - start)} building {source_name}/{profile_id}; "
                f"current: {state.last_marker}",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"[virtualAxe] flight recorder: {note}",
                file=sys.stderr,
                flush=True,
            )
            state.note_index += 1
            last_render = now
        time.sleep(1)

    drain_progress()
    elapsed = format_elapsed(time.monotonic() - start)
    state.status = "COMPLETE" if process.returncode == 0 else "FAILED"
    state.returncode = process.returncode
    if process.returncode == 0:
        state.current_step = state.total_steps
        state.current_message = "Reusable QEMU image ready"
    else:
        state.current_message = f"Stopped during: {state.last_marker}"
    if cockpit_enabled:
        write_ansi_dashboard(build_cockpit_lines(state, color=cockpit_color), cockpit_lines, sys.stderr)
        set_build_cockpit_cursor_visible(sys.stderr, True)
        print("", file=sys.stderr, flush=True)
    print_build_dashboard_footer(
        source_name=source_name,
        profile_id=profile_id,
        out_dir=out_dir,
        log_path=log_path,
        elapsed=elapsed,
        succeeded=process.returncode == 0,
    )
    if process.returncode != 0:
        print_build_failure_tail(log_path)
    return subprocess.CompletedProcess(command, process.returncode, "", "")


def run_build_script(env: dict[str, str], *, capture: bool) -> subprocess.CompletedProcess[str]:
    if capture:
        return run([str(ROOT_DIR / "scripts" / "build-virtual.sh")], cwd=ROOT_DIR, env=env, capture=True)
    return run_build_with_progress(env)


def ensure_git_source(
    name: str,
    entry: dict[str, Any],
    *,
    allow_clone: bool = True,
    allow_fetch: bool = True,
) -> tuple[Path, dict[str, Any]]:
    checkout = SOURCES_CACHE_DIR / name
    checkout.parent.mkdir(parents=True, exist_ok=True)
    if not checkout.exists():
        if not allow_clone:
            raise SystemExit(
                f"Source {name} is not available locally at {checkout}. "
                "Populate .sources/ first or run a build on a networked machine."
            )
        clone = run(["git", "clone", entry["repoUrl"], str(checkout)], cwd=ROOT_DIR, capture=True)
        if clone.returncode != 0:
            raise SystemExit(clone.stderr.strip() or clone.stdout.strip() or f"Unable to clone {name}")

    ref = entry.get("ref")
    if ref and allow_fetch:
        fetch = run(["git", "-C", str(checkout), "fetch", "origin", ref], cwd=ROOT_DIR, capture=True, timeout=60)
        checkout_cmd = run(["git", "-C", str(checkout), "checkout", "--detach", ref], cwd=ROOT_DIR, capture=True)
        if checkout_cmd.returncode != 0:
            if fetch.returncode != 0:
                message = fetch.stderr.strip() or fetch.stdout.strip() or f"Unable to fetch {name}"
                raise SystemExit(message)
            raise SystemExit(checkout_cmd.stderr.strip() or checkout_cmd.stdout.strip())
    return checkout, entry


def source_probe(source_dir: Path) -> dict[str, Any]:
    capabilities = {
        "hasIdfProject": (source_dir / "CMakeLists.txt").is_file(),
        "hasHttpServer": (
            (source_dir / "main" / "http_server" / "http_server.c").is_file()
            or (source_dir / "main" / "http_server" / "http_server.cpp").is_file()
        ),
        "hasAxeOS": (source_dir / "main" / "http_server" / "axe-os").is_dir(),
        "hasModernDeviceConfig": (source_dir / "main" / "device_config.c").is_file(),
        "hasNerdNosBoardModel": (source_dir / "main" / "boards" / "board.h").is_file()
        and (source_dir / "components" / "bm1397" / "include" / "asic.h").is_file(),
        "hasVirtualAsicSeam": (source_dir / "components" / "asic" / "virtual_asic.c").is_file(),
    }
    supports_bitaxe_layout = all(
        capabilities[name]
        for name in ("hasIdfProject", "hasHttpServer", "hasAxeOS", "hasModernDeviceConfig")
    )
    supports_nerdnos_layout = all(
        capabilities[name]
        for name in ("hasIdfProject", "hasHttpServer", "hasAxeOS", "hasNerdNosBoardModel")
    )
    capabilities["supportsGammaProfiles"] = supports_bitaxe_layout or supports_nerdnos_layout

    return {
        "flavor": "nerdnos-esp-miner" if supports_nerdnos_layout and not supports_bitaxe_layout else "bitaxeorg-esp-miner",
        "capabilities": capabilities,
        "missingRequiredCapabilities": [
            name for name in ("hasIdfProject", "hasHttpServer", "hasAxeOS")
            if not capabilities[name]
        ],
    }


def require_shared_virtual_patch_support(source_name: str, probe: dict[str, Any]) -> None:
    if probe["capabilities"]["supportsGammaProfiles"]:
        return
    missing = ", ".join(probe["missingRequiredCapabilities"]) or "unknown"
    raise SystemExit(
        f"Source {source_name} is missing the required virtualAxe seams: {missing}."
    )


def prepare_worktree(source_name: str, source_dir: Path, entry: dict[str, Any]) -> Path:
    target = WORKTREES_DIR / source_name / (entry.get("ref") or "floating")
    target.parent.mkdir(parents=True, exist_ok=True)
    env = clean_process_env()
    env["SOURCE_NAME"] = source_name
    env["SOURCE_DIR"] = str(source_dir)
    env["PATCH_TARGET_DIR"] = str(target)
    env["UPSTREAM_REF"] = entry.get("ref") or "HEAD"
    result = run([str(ROOT_DIR / "scripts" / "apply-patches.sh")], cwd=ROOT_DIR, env=env, capture=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip())
    return target


def read_patch_series(source_name: str | None = None) -> list[str]:
    series_file = source_metadata(source_name).patch_series_path
    return [
        line.strip()
        for line in series_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def patch_touched_files(patch_path: Path) -> list[str]:
    touched: list[str] = []
    for line in patch_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4 and parts[3].startswith("b/"):
            touched.append(parts[3][2:])
    return touched


def patch_series_metadata(source_name: str | None = None) -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    series_file = source_metadata(source_name).patch_series_path
    patch_dir = series_file.parent
    for patch_name in read_patch_series(source_name):
        patch_path = patch_dir / patch_name
        files = patch_touched_files(patch_path)
        metadata.append(
            {
                "patch": patch_name,
                "sha256": file_sha256(patch_path),
                "touchedFiles": files,
            }
        )
    return metadata


def touched_surface_summary(series_metadata: list[dict[str, Any]]) -> dict[str, list[str]]:
    surfaces: dict[str, list[str]] = {}
    for patch in series_metadata:
        for touched_file in patch["touchedFiles"]:
            surfaces.setdefault(touched_file, []).append(patch["patch"])
    return dict(sorted(surfaces.items()))


def resolve_git_ref(source_dir: Path, ref: str) -> tuple[str, str]:
    result = run(["git", "-C", str(source_dir), "rev-parse", ref], cwd=ROOT_DIR, capture=True)
    if result.returncode != 0:
        return "", result.stderr.strip() or result.stdout.strip()
    return result.stdout.strip(), ""


def build_env(args: argparse.Namespace, source_name: str, source_dir: Path, profile: dict[str, Any]) -> dict[str, str]:
    env = clean_process_env()
    source = source_metadata(source_name)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else default_output_dir(source_name, profile["id"])
    state_dir = Path(args.state_dir).resolve() if args.state_dir else (STATE_ROOT / source_name / profile["id"])
    env["OUT_DIR"] = str(out_dir)
    env["STATE_DIR"] = str(state_dir)
    env["SOURCE_NAME"] = source_name
    env["SOURCE_REPO_URL"] = source.repo_url
    env["SOURCE_RESOLVED_COMMIT"] = source.resolved_commit
    env["SOURCE_RELEASE_TAG"] = source.release_tag
    env["QEMU_CONTAINER_NAME"] = qemu_container_name(source_name, profile["id"])
    env["UPSTREAM_DIR"] = str(source_dir)
    env["PATCH_ALREADY_APPLIED"] = "1"
    env["VIRTUAL_PROFILE"] = profile["id"]
    env["VIRTUAL_PROFILE_FILE"] = str(profile["path"])
    env["POOL_HOST"] = args.pool_host
    env["POOL_PORT"] = str(args.pool_port)
    env["POOL_USER"] = args.pool_user
    env["POOL_PASS"] = args.pool_pass
    env["POOL_DIFF"] = str(args.pool_diff)
    env["POOL_TLS"] = str(args.pool_tls)
    env["POOL_CERT"] = args.pool_cert
    env["POOL_SUBSCRIBE_AGENT"] = getattr(args, "pool_subscribe_agent", "")
    env["HOSTNAME_VALUE"] = args.hostname
    env["VIRTUAL_ASIC_MODE"] = args.virtual_asic_mode
    env["HTTP_PORT"] = str(getattr(args, "http_port", DEFAULT_HTTP_PORT))
    env["RESET_PERSISTED_STATE"] = "1" if getattr(args, "reset_persisted_state", False) else "0"
    for key, value in source.build_vars.items():
        env[key] = value
    if getattr(args, "json", False):
        env["VIRTUALAXE_DISABLE_TEE"] = "1"
    return env


def build_submit_replay_env(
    args: argparse.Namespace,
    source_name: str,
    source_dir: Path,
    profile: dict[str, Any],
) -> dict[str, str]:
    out_dir = Path(args.out_dir).resolve() if args.out_dir else default_submit_replay_output_dir(source_name, profile["id"])
    state_dir = (
        Path(args.state_dir).resolve()
        if args.state_dir
        else (STATE_ROOT / "submit-replay" / source_name / profile["id"])
    )
    runtime_args = argparse.Namespace(
        out_dir=str(out_dir),
        state_dir=str(state_dir),
        json=True,
        pool_host=args.guest_pool_host,
        pool_port=args.replay_port,
        pool_user=args.pool_user,
        pool_pass=DEFAULT_POOL_PASS,
        pool_diff=args.replay_difficulty,
        pool_tls=DEFAULT_POOL_TLS,
        pool_cert=DEFAULT_POOL_CERT,
        hostname=DEFAULT_HOSTNAME,
        virtual_asic_mode="cpu",
        http_port=args.http_port,
        reset_persisted_state=True,
    )
    env = build_env(runtime_args, source_name, source_dir, profile)
    env["FALLBACK_POOL_HOST"] = args.guest_pool_host
    env["FALLBACK_POOL_PORT"] = str(args.replay_port)
    env["FALLBACK_POOL_USER"] = args.pool_user
    env["FALLBACK_POOL_PASS"] = DEFAULT_POOL_PASS
    env["FALLBACK_POOL_DIFF"] = str(args.replay_difficulty)
    env["FALLBACK_POOL_TLS"] = str(DEFAULT_POOL_TLS)
    env["FALLBACK_POOL_CERT"] = DEFAULT_POOL_CERT
    env["QEMU_CONTAINER_NAME"] = args.qemu_container_name
    env["STRATUM_REPLAY_HOST"] = args.replay_host
    env["STRATUM_REPLAY_PORT"] = str(args.replay_port)
    env["STRATUM_REPLAY_DIFFICULTY"] = str(args.replay_difficulty)
    env["STRATUM_REPLAY_USERNAME"] = args.pool_user
    env["STRATUM_REPLAY_EXTRANONCE1"] = args.replay_extranonce1
    env["STRATUM_REPLAY_EXTRANONCE2_SIZE"] = str(args.replay_extranonce2_size)
    env["STRATUM_REPLAY_TIMEOUT"] = str(args.replay_timeout)
    env["VIRTUALAXE_DISABLE_TEE"] = "1"
    return env


def print_payload(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


def finish_command(args: argparse.Namespace, result: subprocess.CompletedProcess[str], **payload: Any) -> int:
    if getattr(args, "json", False):
        print_payload(
            {
                **payload,
                "returncode": result.returncode,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
            },
            True,
        )
    return result.returncode


def load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_inputs_newer_than_flash(flash_file: Path) -> bool:
    if not flash_file.is_file():
        return True

    flash_mtime = flash_file.stat().st_mtime
    for directory in BUILD_INPUT_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.stat().st_mtime > flash_mtime:
                return True

    for path in BUILD_INPUT_FILES:
        if path.is_file() and path.stat().st_mtime > flash_mtime:
            return True

    return False


def manifest_matches_requested_build(manifest_path: Path, env: dict[str, str], source_name: str, profile_id: str) -> bool:
    if not manifest_path.is_file():
        return False

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    expected = {
        "sourceName": source_name,
        "virtualProfile": profile_id,
        "virtualAsicMode": env.get("VIRTUAL_ASIC_MODE", DEFAULT_VIRTUAL_ASIC_MODE),
        "poolHost": env.get("POOL_HOST", DEFAULT_POOL_HOST),
        "poolPort": int(env.get("POOL_PORT", DEFAULT_POOL_PORT)),
        "poolDifficulty": float(env.get("POOL_DIFF", "1")),
        "poolSubscribeAgent": env.get("POOL_SUBSCRIBE_AGENT", ""),
    }
    return all(manifest.get(key) == value for key, value in expected.items())


def ensure_matching_build(
    args: argparse.Namespace,
    env: dict[str, str],
    *,
    source_name: str,
    profile_id: str,
    capture: bool,
) -> subprocess.CompletedProcess[str] | None:
    out_dir = Path(env["OUT_DIR"])
    flash_file = out_dir / "qemu_flash.bin"
    manifest_file = out_dir / "manifest.json"

    if flash_file.is_file() and manifest_matches_requested_build(manifest_file, env, source_name, profile_id) and not build_inputs_newer_than_flash(flash_file):
        return None

    return run_build_script(env, capture=capture)


def command_doctor(args: argparse.Namespace) -> int:
    sources = source_registry().as_legacy_payload(include_aliases=False)
    payload: dict[str, Any] = {"sources": {}, "profiles": sorted(p.stem for p in PROFILES_DIR.glob("*.json"))}
    for name, entry in sources["sources"].items():
        try:
            source_dir, resolved_entry = ensure_git_source(name, entry, allow_clone=False, allow_fetch=False)
            probe = source_probe(source_dir)
            patch_health: dict[str, Any]
            if probe["capabilities"]["supportsGammaProfiles"]:
                try:
                    worktree = prepare_worktree(name, source_dir, resolved_entry)
                    patch_health = {"status": "ok", "worktree": str(worktree)}
                except SystemExit as exc:
                    patch_health = {"status": "error", "message": str(exc)}
            else:
                patch_health = {
                    "status": "unsupported",
                    "reason": "missing required virtualAxe seams",
                    "missing": probe["missingRequiredCapabilities"],
                }
            payload["sources"][name] = {
                "checkout": str(source_dir),
                "ref": resolved_entry.get("ref"),
                "probe": probe,
                "patchHealth": patch_health,
            }
        except SystemExit as exc:
            payload["sources"][name] = {"error": str(exc)}

    for tool_name in ("git", "python3"):
        result = run([tool_name, "--version"], cwd=ROOT_DIR, capture=True)
        payload[tool_name] = {
            "available": result.returncode == 0,
            "version": (result.stdout.strip() or result.stderr.strip()) if result.returncode == 0 else "",
        }

    print_payload(payload, args.json)
    return 0


def command_patch_check(args: argparse.Namespace) -> int:
    sources = load_sources()
    if args.source not in sources["sources"]:
        raise SystemExit(f"Unknown source {args.source!r}")

    entry = sources["sources"][args.source]
    source_dir, resolved_entry = ensure_git_source(
        args.source,
        entry,
        allow_clone=True,
        allow_fetch=False,
    )
    if args.fetch:
        fetch = run(["git", "-C", str(source_dir), "fetch", "origin"], cwd=ROOT_DIR, capture=True, timeout=120)
        if fetch.returncode != 0:
            if not args.json:
                print(f"patch-check error: fetch failed for {args.source}", file=sys.stderr)
                if fetch.stderr.strip():
                    print(fetch.stderr.strip(), file=sys.stderr)
                elif fetch.stdout.strip():
                    print(fetch.stdout.strip(), file=sys.stderr)
                return fetch.returncode
            return finish_command(
                args,
                fetch,
                command="patch-check",
                status="error",
                phase="fetch",
                source=args.source,
                upstreamRef=args.upstream_ref or resolved_entry.get("ref") or "HEAD",
            )

    upstream_ref = args.upstream_ref or resolved_entry.get("ref") or "HEAD"
    resolved_commit, ref_error = resolve_git_ref(source_dir, upstream_ref)
    series_metadata = patch_series_metadata(args.source)
    target_dir = Path(args.target_dir).resolve() if args.target_dir else Path(
        tempfile.mkdtemp(prefix=f"virtualaxe-patchcheck-{args.source}-")
    )

    env = {
        "SOURCE_NAME": args.source,
        "SOURCE_DIR": str(source_dir),
        "PATCH_TARGET_DIR": str(target_dir),
        "UPSTREAM_REF": resolved_commit or upstream_ref,
    }
    result = run([str(ROOT_DIR / "scripts" / "apply-patches.sh")], cwd=ROOT_DIR, env=env, capture=True)
    applied = [
        line.removeprefix("Applying ").strip()
        for line in (result.stdout or "").splitlines()
        if line.startswith("Applying ")
    ]
    failed_patch = applied[-1] if result.returncode != 0 and applied else ""
    payload = {
        "command": "patch-check",
        "status": "ok" if result.returncode == 0 else "error",
        "source": args.source,
        "sourceDir": str(source_dir),
        "upstreamRef": upstream_ref,
        "resolvedUpstreamCommit": resolved_commit,
        "refError": ref_error,
        "targetDir": str(target_dir),
        "patchCount": len(series_metadata),
        "patches": series_metadata,
        "touchedSurfaces": touched_surface_summary(series_metadata),
        "appliedPatches": applied,
        "failedPatch": failed_patch,
        "returncode": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }

    if args.json:
        print_payload(payload, True)
    else:
        print(f"patch-check {payload['status']}: {args.source} @ {upstream_ref}")
        if resolved_commit:
            print(f"resolved commit: {resolved_commit}")
        elif ref_error:
            print(f"ref resolution: {ref_error}")
        print(f"target: {target_dir}")
        print(f"patches: {len(series_metadata)}")
        print(f"touched files: {len(payload['touchedSurfaces'])}")
        if failed_patch:
            print(f"failed patch: {failed_patch}")
        elif result.returncode != 0:
            print("failed patch: unknown")
        if result.returncode != 0 and result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
    return result.returncode


def command_build(args: argparse.Namespace) -> int:
    sources = load_sources()
    if args.source not in sources["sources"]:
        raise SystemExit(f"Unknown source {args.source!r}")
    profile = load_profile(args.profile)
    source_dir, resolved_entry = ensure_git_source(args.source, sources["sources"][args.source])
    probe = source_probe(source_dir)
    require_shared_virtual_patch_support(args.source, probe)
    worktree = prepare_worktree(args.source, source_dir, resolved_entry)
    env = build_env(args, args.source, worktree, profile)
    result = run_build_script(env, capture=args.json)
    return finish_command(
        args,
        result,
        command="build",
        source=args.source,
        profile=profile["id"],
        outDir=env["OUT_DIR"],
        stateDir=env["STATE_DIR"],
        upstreamDir=str(worktree),
    )


def command_run(args: argparse.Namespace) -> int:
    require_source_support(args.source, "api_boot_verified", "run")
    sources = load_sources()
    profile = load_profile(args.profile)
    source_dir, resolved_entry = ensure_git_source(args.source, sources["sources"][args.source])
    probe = source_probe(source_dir)
    require_shared_virtual_patch_support(args.source, probe)
    worktree = prepare_worktree(args.source, source_dir, resolved_entry)
    env = build_env(args, args.source, worktree, profile)
    build_result = ensure_matching_build(
        args,
        env,
        source_name=args.source,
        profile_id=profile["id"],
        capture=args.json,
    )
    if build_result is not None and build_result.returncode != 0:
        return finish_command(
            args,
            build_result,
            command="run",
            phase="build",
            networkMode="nat",
            source=args.source,
            profile=profile["id"],
            outDir=env["OUT_DIR"],
            stateDir=env["STATE_DIR"],
            upstreamDir=str(worktree),
        )
    script = ROOT_DIR / "scripts" / "run-qemu-nat.sh"
    result = run([str(script)], cwd=ROOT_DIR, env=env, capture=args.json)
    return finish_command(
        args,
        result,
        command="run",
        rebuilt=build_result is not None,
        networkMode="nat",
        source=args.source,
        profile=profile["id"],
        outDir=env["OUT_DIR"],
        stateDir=env["STATE_DIR"],
        upstreamDir=str(worktree),
    )


def command_dashboard(args: argparse.Namespace) -> int:
    require_source_support(args.source, "api_boot_verified", "dashboard")
    sources = load_sources()
    profile = load_profile(args.profile)
    source_dir, resolved_entry = ensure_git_source(args.source, sources["sources"][args.source])
    probe = source_probe(source_dir)
    require_shared_virtual_patch_support(args.source, probe)
    worktree = prepare_worktree(args.source, source_dir, resolved_entry)
    env = build_env(args, args.source, worktree, profile)
    env["HTTP_PORT"] = str(args.http_port)
    env["BASE_URL"] = f"http://127.0.0.1:{args.http_port}"
    env["VIRTUALAXE_DISABLE_TEE"] = "1"
    build_result = ensure_matching_build(
        args,
        env,
        source_name=args.source,
        profile_id=profile["id"],
        capture=False,
    )
    if build_result is not None and build_result.returncode != 0:
        raise SystemExit("Unable to launch the dashboard because the firmware rebuild failed. See out/build.log.")
    ensure = run([str(ENSURE_TEST_PYTHON)], cwd=ROOT_DIR, env=env, capture=True)
    if ensure.returncode != 0:
        raise SystemExit(ensure.stderr.strip() or ensure.stdout.strip() or "Unable to provision the dashboard Python environment.")
    dashboard_python = str(VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable))
    result = run(
        [
            dashboard_python,
            str(DASHBOARD_SCRIPT),
            "--source",
            args.source,
            "--profile",
            profile["id"],
            "--network-mode",
            args.network_mode,
            "--http-port",
            str(args.http_port),
            "--out-dir",
            env["OUT_DIR"],
            "--state-dir",
            env["STATE_DIR"],
            *([] if args.no_auto_start else ["--auto-start"]),
        ],
        cwd=ROOT_DIR,
        env=env,
        capture=False,
    )
    return result.returncode


def command_verify(args: argparse.Namespace) -> int:
    require_source_support(args.source, "api_boot_verified", "verify")
    sources = load_sources()
    profile = load_profile(args.profile)
    source_dir, resolved_entry = ensure_git_source(args.source, sources["sources"][args.source])
    probe = source_probe(source_dir)
    require_shared_virtual_patch_support(args.source, probe)
    worktree = prepare_worktree(args.source, source_dir, resolved_entry)
    env = build_env(args, args.source, worktree, profile)
    script = [str(ROOT_DIR / "scripts" / "run-e2e.sh")]
    if args.api_only:
        script.append("--api-only")
    if args.browser_only:
        script.append("--browser-only")
    result = run(script, cwd=ROOT_DIR, env=env, capture=args.json)
    return finish_command(
        args,
        result,
        command="verify",
        source=args.source,
        profile=profile["id"],
        apiOnly=args.api_only,
        browserOnly=args.browser_only,
        outDir=env["OUT_DIR"],
        stateDir=env["STATE_DIR"],
        upstreamDir=str(worktree),
    )


def command_verify_submit_replay(args: argparse.Namespace) -> int:
    require_source_support(args.source, "submit_replay_verified", "verify-submit-replay")
    sources = load_sources()
    profile = load_profile(args.profile)
    source_dir, resolved_entry = ensure_git_source(args.source, sources["sources"][args.source])
    probe = source_probe(source_dir)
    require_shared_virtual_patch_support(args.source, probe)
    worktree = prepare_worktree(args.source, source_dir, resolved_entry)
    env = build_submit_replay_env(args, args.source, worktree, profile)
    out_dir = Path(env["OUT_DIR"])
    replay_json_path = out_dir / "stratum-replay.json"
    qemu_log_path = out_dir / "qemu.log"
    replay_stderr_path = out_dir / "stratum-replay.err.log"

    build_result = ensure_matching_build(
        args,
        env,
        source_name=args.source,
        profile_id=profile["id"],
        capture=True,
    )
    if build_result is not None and build_result.returncode != 0:
        payload = {
            "command": "verify-submit-replay",
            "phase": "build",
            "source": args.source,
            "profile": profile["id"],
            "outDir": env["OUT_DIR"],
            "stateDir": env["STATE_DIR"],
            "upstreamDir": str(worktree),
            "returncode": build_result.returncode,
            "stdout": build_result.stdout or "",
            "stderr": build_result.stderr or "",
        }
        if args.json:
            print_payload(payload, True)
        else:
            print(f"Submit replay build failed. See {out_dir / 'build.log'}.", file=sys.stderr)
        return build_result.returncode

    stop_result: subprocess.CompletedProcess[str] | None = None
    replay_result = run(
        [str(RUN_QEMU_NAT_SCRIPT), "--submit-replay"],
        cwd=ROOT_DIR,
        env=env,
        capture=True,
        timeout=args.replay_timeout + 180,
    )
    stop_result = run([str(RUN_QEMU_NAT_SCRIPT), "--stop"], cwd=ROOT_DIR, env=env, capture=True)
    replay_payload = load_json_file(replay_json_path)
    payload = {
        "command": "verify-submit-replay",
        "source": args.source,
        "profile": profile["id"],
        "rebuilt": build_result is not None,
        "guestPool": {"host": args.guest_pool_host, "port": args.replay_port},
        "assignedDifficulty": args.replay_difficulty,
        "extranonce1": args.replay_extranonce1,
        "extranonce2Size": args.replay_extranonce2_size,
        "outDir": env["OUT_DIR"],
        "stateDir": env["STATE_DIR"],
        "upstreamDir": str(worktree),
        "logs": {
            "qemu": str(qemu_log_path),
            "replay": str(replay_json_path),
            "replayStderr": str(replay_stderr_path),
        },
        "replay": replay_payload,
        "returncode": replay_result.returncode,
        "stdout": replay_result.stdout or "",
        "stderr": replay_result.stderr or "",
        "stopReturncode": stop_result.returncode if stop_result is not None else None,
    }
    if args.json:
        print_payload(payload, True)
        return replay_result.returncode

    if replay_result.returncode == 0 and replay_payload and replay_payload.get("status") == "accepted":
        submission = replay_payload.get("submission", {})
        share_diff = replay_payload.get("shareDifficulty")
        assigned_diff = replay_payload.get("assignedDifficulty")
        share_text = f"{share_diff:.6g}" if isinstance(share_diff, (int, float)) else "unknown"
        assigned_text = f"{assigned_diff:.6g}" if isinstance(assigned_diff, (int, float)) else "unknown"
        print(
            "Submit replay accepted: "
            f"nonce {submission.get('nonce', 'unknown')} at share difficulty {share_text} "
            f"against assigned difficulty {assigned_text}."
        )
        print(f"Evidence: {replay_json_path}")
        print(f"QEMU log: {qemu_log_path}")
    else:
        status = replay_payload.get("status") if replay_payload else "no replay payload"
        print(f"Submit replay failed during replay phase ({status}).", file=sys.stderr)
        print(f"Evidence directory: {out_dir}", file=sys.stderr)
        if replay_result.stdout.strip():
            print(replay_result.stdout.strip())
        if replay_result.stderr.strip():
            print(replay_result.stderr.strip(), file=sys.stderr)
    return replay_result.returncode


def command_verify_release(args: argparse.Namespace) -> int:
    require_source_support(args.source, "submit_replay_verified", "verify-release")
    mode = "qualification" if args.qualification else args.mode
    command = [
        str(VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable)),
        str(VERIFY_RELEASE_SCRIPT),
        "--source",
        args.source,
        "--pool-user",
        args.pool_user,
        "--http-port",
        str(args.http_port),
        "--mode",
        mode,
    ]
    if args.out_dir:
        command.extend(["--out-dir", args.out_dir])
    if args.run_id:
        command.extend(["--run-id", args.run_id])
    if args.json:
        command.append("--json")

    result = run(command, cwd=ROOT_DIR, capture=args.json)
    return finish_command(
        args,
        result,
        command="verify-release",
        source=args.source,
        poolUser=args.pool_user,
        mode=mode,
        httpPort=args.http_port,
        outDir=args.out_dir or str(ROOT_DIR / "out" / "release-matrix"),
        runId=args.run_id,
    )


def command_verify_test_ci(args: argparse.Namespace) -> int:
    require_source_support(args.source, "api_boot_verified", "verify-test-ci")
    sources = load_sources()
    source_dir, resolved_entry = ensure_git_source(args.source, sources["sources"][args.source])
    probe = source_probe(source_dir)
    require_shared_virtual_patch_support(args.source, probe)
    worktree = prepare_worktree(args.source, source_dir, resolved_entry)
    test_ci_dir = worktree / "test-ci"
    if not test_ci_dir.is_dir():
        profile = load_profile("gamma")
        runtime_args = argparse.Namespace(
            out_dir=args.out_dir,
            state_dir=None,
            json=args.json,
            pool_host=DEFAULT_SUBMIT_REPLAY_GUEST_POOL_HOST,
            pool_port=1,
            pool_user=DEFAULT_POOL_USER,
            pool_pass=DEFAULT_POOL_PASS,
            pool_diff=1.0,
            pool_tls=DEFAULT_POOL_TLS,
            pool_cert=DEFAULT_POOL_CERT,
            pool_subscribe_agent="",
            hostname=DEFAULT_HOSTNAME,
            virtual_asic_mode=DEFAULT_VIRTUAL_ASIC_MODE,
            http_port=DEFAULT_SUBMIT_REPLAY_HTTP_PORT,
            reset_persisted_state=True,
        )
        env = build_env(runtime_args, args.source, worktree, profile)
        env["FALLBACK_POOL_HOST"] = DEFAULT_SUBMIT_REPLAY_GUEST_POOL_HOST
        env["FALLBACK_POOL_PORT"] = "1"
        env["FALLBACK_POOL_USER"] = DEFAULT_POOL_USER
        env["FALLBACK_POOL_PASS"] = DEFAULT_POOL_PASS
        env["FALLBACK_POOL_DIFF"] = "1"
        env["FALLBACK_POOL_TLS"] = str(DEFAULT_POOL_TLS)
        env["FALLBACK_POOL_CERT"] = DEFAULT_POOL_CERT
        env["FALLBACK_POOL_SUBSCRIBE_AGENT"] = ""
        result = run([str(ROOT_DIR / "scripts" / "run-e2e.sh"), "--api-only"], cwd=ROOT_DIR, env=env, capture=args.json)
        return finish_command(
            args,
            result,
            command="verify-test-ci",
            phase="api-boot",
            source=args.source,
            profile=profile["id"],
            outDir=env["OUT_DIR"],
            stateDir=env["STATE_DIR"],
            upstreamDir=str(worktree),
        )

    env = clean_process_env()
    env["UPSTREAM_DIR"] = str(worktree)
    env["OUT_DIR"] = str(Path(args.out_dir).resolve() if args.out_dir else (ROOT_DIR / "out"))
    if args.json:
        env["VIRTUALAXE_DISABLE_TEE"] = "1"
    result = run([str(ROOT_DIR / "scripts" / "verify-test-ci.sh")], cwd=ROOT_DIR, env=env, capture=args.json)
    return finish_command(
        args,
        result,
        command="verify-test-ci",
        source=args.source,
        outDir=env["OUT_DIR"],
        upstreamDir=str(worktree),
    )


def command_state_export(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    state_dir = Path(args.state_dir).resolve() if args.state_dir else (STATE_ROOT / args.source / profile["id"])
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": args.source,
        "profile": profile["id"],
        "stateDir": str(state_dir),
        "nvs": str(state_dir / "nvs.bin"),
    }
    print_payload(payload, args.json)
    return 0


def command_state_import(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    state_dir = Path(args.state_dir).resolve() if args.state_dir else (STATE_ROOT / args.source / profile["id"])
    state_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.input, state_dir / "nvs.bin")
    payload = {"source": args.source, "profile": profile["id"], "imported": args.input, "stateDir": str(state_dir)}
    print_payload(payload, args.json)
    return 0


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_state_reset_dir(args: argparse.Namespace, profile_id: str) -> Path:
    state_root = STATE_ROOT.resolve(strict=False)
    requested = Path(args.state_dir).expanduser() if args.state_dir else STATE_ROOT / args.source / profile_id
    target = requested.resolve(strict=False)

    if target != state_root and path_is_within(target, state_root):
        return target

    if args.state_dir is None:
        raise SystemExit(
            f"Refusing unsafe state reset target {target}: the selected source escapes {state_root}."
        )

    protected_roots = {
        Path("/").resolve(strict=False),
        ROOT_DIR.resolve(strict=False),
        Path.home().resolve(strict=False),
        Path(tempfile.gettempdir()).resolve(strict=False),
    }
    if target == state_root:
        protected_roots.add(state_root)

    for protected in protected_roots:
        if target == protected or path_is_within(protected, target):
            raise SystemExit(
                f"Refusing unsafe state reset target {target}: it is or contains protected path {protected}."
            )

    repo_root = ROOT_DIR.resolve(strict=False)
    if path_is_within(target, repo_root):
        raise SystemExit(
            f"Refusing unsafe state reset target {target}: repository paths outside {state_root} are protected."
        )

    if os.environ.get("VIRTUALAXE_CONFIRM_STATE_RESET") != "1":
        raise SystemExit(
            f"Refusing external state reset target {target}: "
            "set VIRTUALAXE_CONFIRM_STATE_RESET=1 to confirm recursive deletion."
        )
    return target


def command_state_reset(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    state_dir = resolve_state_reset_dir(args, profile["id"])
    if state_dir.exists():
        if not state_dir.is_dir():
            raise SystemExit(f"Refusing state reset target {state_dir}: target is not a directory.")
        shutil.rmtree(state_dir)
    payload = {"source": args.source, "profile": profile["id"], "reset": str(state_dir)}
    print_payload(payload, args.json)
    return 0


def add_runtime_config_arguments(parser: argparse.ArgumentParser, *, include_http_port: bool) -> None:
    parser.add_argument("--pool-host", default=DEFAULT_POOL_HOST)
    parser.add_argument("--pool-port", type=int, default=int(DEFAULT_POOL_PORT))
    parser.add_argument("--pool-user", default=DEFAULT_POOL_USER)
    parser.add_argument("--pool-pass", default=DEFAULT_POOL_PASS)
    parser.add_argument("--pool-diff", type=float, default=DEFAULT_POOL_DIFF)
    parser.add_argument("--pool-tls", type=int, choices=(0, 1, 2), default=DEFAULT_POOL_TLS)
    parser.add_argument("--pool-cert", default=DEFAULT_POOL_CERT)
    parser.add_argument("--pool-subscribe-agent", default="")
    parser.add_argument("--hostname", default=DEFAULT_HOSTNAME)
    parser.add_argument("--virtual-asic-mode", choices=("cpu",), default=DEFAULT_VIRTUAL_ASIC_MODE)
    parser.add_argument("--reset-persisted-state", action="store_true")
    if include_http_port:
        parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="virtualAxe CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    available_profiles = tuple(sorted(p.stem for p in PROFILES_DIR.glob("*.json")))

    doctor = subparsers.add_parser(
        "doctor",
        help="Inspect source caches, patch health, profiles, and required tools.",
        description="Inspect configured sources, prepare patch-health worktrees, and report local tool availability.",
    )
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=command_doctor)

    patch_check = subparsers.add_parser("patch-check", help="Apply the ESP-Miner patch stack in a disposable directory.")
    patch_check.add_argument("--source", default=source_registry().default_source)
    patch_check.add_argument("--upstream-ref")
    patch_check.add_argument("--target-dir")
    patch_check.add_argument("--fetch", action="store_true")
    patch_check.add_argument("--json", action="store_true")
    patch_check.set_defaults(func=command_patch_check)

    runtime_help = {
        "build": "Build a reusable source-specific QEMU firmware image.",
        "run": "Build when needed, then start the selected firmware in QEMU.",
        "verify": "Run local QEMU API and browser integration checks.",
    }
    for name in ("build", "run", "verify"):
        sub = subparsers.add_parser(name, help=runtime_help[name], description=runtime_help[name])
        sub.add_argument("--source", default=source_registry().default_source)
        sub.add_argument("--profile", choices=available_profiles, default="gamma")
        sub.add_argument("--out-dir")
        sub.add_argument("--state-dir")
        sub.add_argument("--json", action="store_true")
        add_runtime_config_arguments(sub, include_http_port=name in ("run", "verify"))
        if name == "run":
            sub.add_argument("--network-mode", choices=("nat",), default="nat")
            sub.set_defaults(func=command_run)
        elif name == "verify":
            sub.add_argument("--api-only", action="store_true")
            sub.add_argument("--browser-only", action="store_true")
            sub.set_defaults(func=command_verify)
        else:
            sub.set_defaults(func=command_build)

    verify_submit_replay = subparsers.add_parser(
        "verify-submit-replay",
        help="Run deterministic guest-side submit-boundary replay.",
        description="Run deterministic local Stratum replay through the guest firmware submit path.",
    )
    verify_submit_replay.add_argument("--source", default=source_registry().default_source)
    verify_submit_replay.add_argument("--profile", choices=available_profiles, default="gamma")
    verify_submit_replay.add_argument("--out-dir")
    verify_submit_replay.add_argument("--state-dir")
    verify_submit_replay.add_argument("--json", action="store_true")
    verify_submit_replay.add_argument("--guest-pool-host", default=DEFAULT_SUBMIT_REPLAY_GUEST_POOL_HOST)
    verify_submit_replay.add_argument("--replay-host", default="0.0.0.0")
    verify_submit_replay.add_argument("--replay-port", type=int, default=DEFAULT_SUBMIT_REPLAY_PORT)
    verify_submit_replay.add_argument("--replay-difficulty", type=float, default=DEFAULT_SUBMIT_REPLAY_DIFFICULTY)
    verify_submit_replay.add_argument("--replay-extranonce1", default=DEFAULT_SUBMIT_REPLAY_EXTRANONCE1)
    verify_submit_replay.add_argument("--replay-extranonce2-size", type=int, default=DEFAULT_SUBMIT_REPLAY_EXTRANONCE2_SIZE)
    verify_submit_replay.add_argument("--replay-timeout", type=float, default=DEFAULT_SUBMIT_REPLAY_TIMEOUT)
    verify_submit_replay.add_argument("--http-port", type=int, default=DEFAULT_SUBMIT_REPLAY_HTTP_PORT)
    verify_submit_replay.add_argument("--pool-user", default=DEFAULT_SUBMIT_REPLAY_USER)
    verify_submit_replay.add_argument("--qemu-container-name", default=DEFAULT_SUBMIT_REPLAY_CONTAINER_NAME)
    verify_submit_replay.set_defaults(func=command_verify_submit_replay)

    dashboard = subparsers.add_parser(
        "dashboard",
        help="Start the local operator dashboard for a virtual runtime.",
    )
    dashboard.add_argument("--source", default=source_registry().default_source)
    dashboard.add_argument("--profile", choices=available_profiles, default="gamma")
    dashboard.add_argument("--out-dir")
    dashboard.add_argument("--state-dir")
    dashboard.add_argument("--network-mode", choices=("nat",), default="nat")
    add_runtime_config_arguments(dashboard, include_http_port=True)
    dashboard.add_argument("--no-auto-start", action="store_true")
    dashboard.set_defaults(func=command_dashboard)

    verify_release = subparsers.add_parser(
        "verify-release",
        help="Run the automated external/live Bitronics and Nerdminers release gate.",
        description="Run automated live pool verification for the selected source.",
    )
    verify_release.add_argument("--source", default=source_registry().default_source)
    verify_release.add_argument("--pool-user", default=os.environ.get("VERIFY_POOL_USER", DEFAULT_VERIFY_RELEASE_POOL_USER))
    verify_release.add_argument("--out-dir")
    verify_release.add_argument("--run-id")
    verify_release.add_argument("--json", action="store_true")
    verify_release.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)
    verify_release.add_argument("--mode", choices=("smoke", "qualification"), default="smoke")
    verify_release.add_argument("--qualification", action="store_true")
    verify_release.set_defaults(func=command_verify_release)

    verify_test_ci = subparsers.add_parser(
        "verify-test-ci",
        help="Run source-specific firmware unit or API-boot proof.",
    )
    verify_test_ci.add_argument("--source", default=source_registry().default_source)
    verify_test_ci.add_argument("--out-dir")
    verify_test_ci.add_argument("--json", action="store_true")
    verify_test_ci.set_defaults(func=command_verify_test_ci)

    state = subparsers.add_parser(
        "state",
        help="Inspect, import, or delete ignored local NVS state.",
    )
    state_subparsers = state.add_subparsers(dest="state_command", required=True)
    state_help = {
        "export": "Report the selected ignored NVS state paths.",
        "import": "Copy an NVS image into the selected ignored state directory.",
        "reset": "Delete the selected local state directory recursively.",
    }
    for sub_name, handler in (("export", command_state_export), ("import", command_state_import), ("reset", command_state_reset)):
        state_parser = state_subparsers.add_parser(sub_name, help=state_help[sub_name])
        state_parser.add_argument("--source", default=source_registry().default_source)
        state_parser.add_argument("--profile", choices=available_profiles, default="gamma")
        state_parser.add_argument("--state-dir")
        state_parser.add_argument("--json", action="store_true")
        if sub_name == "import":
            state_parser.add_argument("--input", required=True)
        state_parser.set_defaults(func=handler)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
