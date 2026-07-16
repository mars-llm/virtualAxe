#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_registry import load_source_registry

PROFILES_DIR = ROOT_DIR / "configs" / "profiles"
VIRTUALAXE_SCRIPT = ROOT_DIR / "scripts" / "virtualaxe.py"
RUN_QEMU_SCRIPT = ROOT_DIR / "scripts" / "run-qemu-nat.sh"
WAIT_FOR_HTTP_SCRIPT = ROOT_DIR / "scripts" / "wait-for-http.sh"
ENSURE_TEST_PYTHON = ROOT_DIR / "scripts" / "ensure-test-python.sh"
DASHBOARD_SCRIPT = ROOT_DIR / "scripts" / "virtualaxe_dashboard.py"
SIM_PROXY_SCRIPT = ROOT_DIR / "scripts" / "simulation_proxy.py"
VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
DEFAULT_HTTP_PORT = "18080"
DEFAULT_SIM_BACKEND_HTTP_PORT = "18082"


@dataclass(frozen=True)
class PoolTarget:
    host: str
    port: int
    difficulty: float | None = None
    subscribe_agent: str | None = None


KNOWN_POOL_TARGETS = {
    "public": PoolTarget("public-pool.io", 3333, 0.0001, ""),
    "publicpool": PoolTarget("public-pool.io", 3333, 0.0001, ""),
    "bitronics": PoolTarget("pool.bitronics.store", 3334, 0.0001, "NerdMinerV2/virtualAxe-gamma"),
    "nerdminers": PoolTarget("pool.nerdminers.org", 3333, 0.0005, "NerdMinerV2/virtualAxe-gamma"),
}


KNOWN_POOL_TARGETS_BY_ENDPOINT = {
    (target.host, target.port): target for target in KNOWN_POOL_TARGETS.values()
}


def parse_pool_target(raw: str) -> PoolTarget:
    normalized = raw.strip().lower()
    if normalized in KNOWN_POOL_TARGETS:
        return KNOWN_POOL_TARGETS[normalized]

    host, sep, port_text = raw.rpartition(":")
    if not sep or not host or not port_text:
        raise argparse.ArgumentTypeError("expected --pool public|bitronics|nerdminers|host:port")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("pool port must be an integer") from exc
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError("pool port must be between 1 and 65535")
    return KNOWN_POOL_TARGETS_BY_ENDPOINT.get((host, port), PoolTarget(host=host, port=port))


def env_flag_enabled(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def parse_port(raw: str, *, label: str) -> int:
    try:
        port = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be an integer") from exc
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError(f"{label} must be between 1 and 65535")
    return port


def parse_sim_backend_http_port(raw: str) -> int:
    return parse_port(raw, label="--sim-backend-port")


def default_sim_backend_http_port() -> int:
    raw = os.environ.get("VIRTUAL_BITAXE_SIM_BACKEND_PORT", DEFAULT_SIM_BACKEND_HTTP_PORT).strip()
    return parse_port(raw, label="VIRTUAL_BITAXE_SIM_BACKEND_PORT")


def runtime_ports(args: argparse.Namespace) -> tuple[str, str]:
    if args.sim_actions:
        return (str(args.sim_backend_port), DEFAULT_HTTP_PORT)
    return (DEFAULT_HTTP_PORT, DEFAULT_HTTP_PORT)


def build_parser() -> argparse.ArgumentParser:
    available_profiles = tuple(sorted(path.stem for path in PROFILES_DIR.glob("*.json")))
    registry = load_source_registry()
    public_sources = tuple(sorted(registry.sources))
    parser = argparse.ArgumentParser(
        prog="vaxe",
        description="Start a source-specific virtualAxe operator session.",
        epilog=(
            "Examples:\n"
            "  ./vaxe --source bitaxe\n"
            "  ./vaxe --source nerdnos --pool bitronics\n\n"
            "Bare ./vaxe prints this help and does not start QEMU."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source", choices=public_sources, required=True, help="Firmware source to run")
    parser.add_argument(
        "--profile",
        choices=available_profiles,
        default="gamma",
        help="Virtual hardware profile (gamma is the only supported profile)",
    )
    parser.add_argument(
        "--pool",
        type=parse_pool_target,
        help="Use bitronics or nerdminers; public is optional, or provide host:port",
    )
    parser.add_argument(
        "--sim-actions",
        action="store_true",
        default=env_flag_enabled("VIRTUAL_BITAXE_SIM_ACTIONS"),
        help="Enable the loopback-only /sim/* UI controls",
    )
    parser.add_argument(
        "--sim-backend-port",
        type=parse_sim_backend_http_port,
        default=default_sim_backend_http_port(),
        help="Firmware HTTP port behind the Simulation Actions proxy",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args


def load_virtualaxe_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("virtualaxe_cli", VIRTUALAXE_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Unable to load {VIRTUALAXE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fetch_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        status = getattr(response, "status", response.getcode())
        if status < 200 or status >= 300:
            raise RuntimeError(f"GET {url} returned HTTP {status}")
        return json.load(response)


def patch_json(url: str, payload: dict[str, Any], *, timeout: float = 10.0) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", response.getcode())
        if status < 200 or status >= 300:
            raise RuntimeError(f"PATCH {url} returned HTTP {status}")


def post_empty(url: str, *, timeout: float = 10.0) -> None:
    request = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", response.getcode())
        if status < 200 or status >= 300:
            raise RuntimeError(f"POST {url} returned HTTP {status}")


def runtime_is_active(container_name: str, out_dir: Path) -> bool:
    qemu_pid = out_dir / "qemu.pid"
    if qemu_pid.is_file():
        return True

    for runtime in ("podman", "docker"):
        binary = shutil.which(runtime)
        if not binary:
            continue
        result = subprocess.run(
            [binary, "ps", "--format", "{{.Names}}"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and container_name in result.stdout.splitlines():
            return True

    return False


def runtime_api_reachable(env: dict[str, str], *, timeout: float = 1.0) -> bool:
    try:
        fetch_json(f"{env['BASE_URL']}/api/system/info", timeout=timeout)
    except (RuntimeError, TimeoutError, OSError, urllib.error.URLError, json.JSONDecodeError):
        return False
    return True


def wait_for_http(env: dict[str, str]) -> None:
    result = subprocess.run([str(WAIT_FOR_HTTP_SCRIPT)], cwd=ROOT_DIR, env=env, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Timed out waiting for {env['BASE_URL']}/api/system/info")


def stop_runtime(env: dict[str, str]) -> None:
    subprocess.run([str(RUN_QEMU_SCRIPT), "--stop"], cwd=ROOT_DIR, env=env, check=False)


def stop_conflicting_runtimes(
    env: dict[str, str],
    *,
    virtualaxe: ModuleType,
    profile_id: str,
) -> None:
    requested_container = env.get("QEMU_CONTAINER_NAME", "virtualaxe-qemu")
    for container_name, out_dir in virtualaxe.managed_qemu_runtime_specs(profile_id):
        if container_name == requested_container:
            continue
        if not runtime_is_active(container_name, Path(out_dir)):
            continue
        stop_env = env.copy()
        stop_env["QEMU_CONTAINER_NAME"] = container_name
        stop_env["OUT_DIR"] = str(out_dir)
        stop_runtime(stop_env)


def run_checked(cmd: list[str], *, env: dict[str, str], message: str) -> None:
    result = subprocess.run(cmd, cwd=ROOT_DIR, env=env, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        details = "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())
        if details:
            raise SystemExit(f"{message}\n{details}")
        raise SystemExit(message)


def start_runtime(env: dict[str, str]) -> None:
    start_env = env.copy()
    start_env["BACKGROUND"] = "1"
    log_path = Path(env["OUT_DIR"]) / "qemu.log"
    run_checked(
        [str(RUN_QEMU_SCRIPT)],
        env=start_env,
        message=f"Unable to start virtualAxe. See {log_path}.",
    )


def ensure_runtime_available(env: dict[str, str]) -> None:
    if runtime_is_active(env.get("QEMU_CONTAINER_NAME", "virtualaxe-qemu"), Path(env["OUT_DIR"])):
        if runtime_api_reachable(env):
            return
        stop_runtime(env)

    start_runtime(env)
    wait_for_http(env)


def pool_override_differs(system_info: dict[str, Any], target: PoolTarget) -> bool:
    try:
        current_port = int(system_info.get("stratumPort", 0))
    except (TypeError, ValueError):
        current_port = 0
    if system_info.get("stratumURL") != target.host or current_port != target.port:
        return True
    if target.difficulty is not None:
        try:
            current_diff = float(system_info.get("stratumSuggestedDifficulty", system_info.get("poolDifficulty", 0)))
        except (TypeError, ValueError):
            current_diff = 0.0
        if abs(current_diff - target.difficulty) > max(1e-9, target.difficulty * 1e-5):
            return True
    if target.subscribe_agent is not None and system_info.get("stratumSubscribeAgent", "") != target.subscribe_agent:
        return True
    return False


def pool_patch_payload(target: PoolTarget) -> dict[str, Any]:
    payload: dict[str, Any] = {"stratumURL": target.host, "stratumPort": target.port}
    if target.difficulty is not None:
        payload["stratumSuggestedDifficulty"] = target.difficulty
    if target.subscribe_agent is not None:
        payload["stratumSubscribeAgent"] = target.subscribe_agent
    return payload


def launch_dashboard(
    *,
    source_name: str,
    profile_name: str,
    env: dict[str, str],
) -> int:
    ensure = subprocess.run([str(ENSURE_TEST_PYTHON)], cwd=ROOT_DIR, env=env, text=True, capture_output=True, check=False)
    if ensure.returncode != 0:
        raise SystemExit(ensure.stderr.strip() or ensure.stdout.strip() or "Unable to provision the dashboard Python environment.")

    dashboard_python = str(VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable))
    command = [
        dashboard_python,
        str(DASHBOARD_SCRIPT),
        "--source",
        source_name,
        "--profile",
        profile_name,
        "--network-mode",
        "nat",
        "--http-port",
        env["HTTP_PORT"],
        "--out-dir",
        env["OUT_DIR"],
        "--state-dir",
        env["STATE_DIR"],
    ]
    if env.get("SIM_ACTIONS_ENABLED") == "1":
        command.extend(["--web-http-port", env["WEB_HTTP_PORT"]])
    result = subprocess.run(
        command,
        cwd=ROOT_DIR,
        env=env,
        check=False,
    )
    return result.returncode


def start_simulation_proxy(env: dict[str, str]) -> subprocess.Popen:
    log_path = Path(env["OUT_DIR"]) / "sim-actions-proxy.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proxy_env = env.copy()
    proxy_env["PYTHONUNBUFFERED"] = "1"
    command = [
        sys.executable,
        str(SIM_PROXY_SCRIPT),
        "--backend-url",
        f"http://127.0.0.1:{env['HTTP_PORT']}",
        "--listen-host",
        "127.0.0.1",
        "--listen-port",
        env["WEB_HTTP_PORT"],
        "--enabled",
    ]
    handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=ROOT_DIR,
        env=proxy_env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    process.log_handle = handle  # type: ignore[attr-defined]
    base_url = f"http://127.0.0.1:{env['WEB_HTTP_PORT']}"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            fetch_json(f"{base_url}/sim/capabilities", timeout=0.5)
            return process
        except (urllib.error.URLError, RuntimeError, TimeoutError, OSError):
            time.sleep(0.1)
    stop_simulation_proxy(process)
    raise RuntimeError(f"Timed out waiting for Simulation Actions proxy. See {log_path}.")


def stop_simulation_proxy(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    handle = getattr(process, "log_handle", None)
    if handle is not None:
        handle.close()


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        build_parser().print_help()
        return 0
    args = parse_args(argv)
    virtualaxe = load_virtualaxe_module()
    source_name = args.source
    virtualaxe.require_source_support(source_name, "api_boot_verified", "vaxe")
    sources = virtualaxe.load_sources()
    profile = virtualaxe.load_profile(args.profile)
    source_dir, resolved_entry = virtualaxe.ensure_git_source(source_name, sources["sources"][source_name])
    probe = virtualaxe.source_probe(source_dir)
    virtualaxe.require_shared_virtual_patch_support(source_name, probe)
    worktree = virtualaxe.prepare_worktree(source_name, source_dir, resolved_entry)
    firmware_http_port, web_http_port = runtime_ports(args)

    build_args = argparse.Namespace(
        out_dir=None,
        state_dir=None,
        json=False,
        pool_host=virtualaxe.DEFAULT_POOL_HOST,
        pool_port=int(virtualaxe.DEFAULT_POOL_PORT),
        pool_user=virtualaxe.DEFAULT_POOL_USER,
        pool_pass=virtualaxe.DEFAULT_POOL_PASS,
        pool_diff=virtualaxe.DEFAULT_POOL_DIFF,
        pool_tls=virtualaxe.DEFAULT_POOL_TLS,
        pool_cert=virtualaxe.DEFAULT_POOL_CERT,
        hostname=virtualaxe.DEFAULT_HOSTNAME,
        virtual_asic_mode=virtualaxe.DEFAULT_VIRTUAL_ASIC_MODE,
        http_port=int(firmware_http_port),
        reset_persisted_state=False,
    )
    env = virtualaxe.build_env(build_args, source_name, worktree, profile)
    env["HTTP_PORT"] = firmware_http_port
    env["BASE_URL"] = f"http://127.0.0.1:{env['HTTP_PORT']}"
    env["VIRTUALAXE_DISABLE_TEE"] = "1"
    env["WEB_HTTP_PORT"] = web_http_port
    env["SIM_ACTIONS_ENABLED"] = "1" if args.sim_actions else "0"

    out_dir = Path(env["OUT_DIR"])
    flash_file = out_dir / "qemu_flash.bin"
    build_matches = (
        flash_file.is_file()
        and virtualaxe.manifest_matches_requested_build(out_dir / "manifest.json", env, source_name, profile["id"])
        and not virtualaxe.build_inputs_newer_than_flash(flash_file)
    )
    active_runtime = runtime_is_active(env.get("QEMU_CONTAINER_NAME", "virtualaxe-qemu"), out_dir)

    if active_runtime and not build_matches:
        stop_runtime(env)
        active_runtime = False

    if not build_matches:
        build_result = virtualaxe.ensure_matching_build(
            build_args,
            env,
            source_name=source_name,
            profile_id=profile["id"],
            capture=False,
        )
        if build_result is not None and build_result.returncode != 0:
            raise SystemExit("Unable to build virtualAxe. See out/build.log.")

    stop_conflicting_runtimes(env, virtualaxe=virtualaxe, profile_id=profile["id"])
    ensure_runtime_available(env)

    if args.pool is not None:
        info_url = f"{env['BASE_URL']}/api/system/info"
        system_url = f"{env['BASE_URL']}/api/system"
        system_info = fetch_json(info_url)
        if pool_override_differs(system_info, args.pool):
            patch_json(system_url, pool_patch_payload(args.pool))
            post_empty(f"{system_url}/restart")
            wait_for_http(env)

    proxy: subprocess.Popen | None = None
    try:
        if args.sim_actions:
            proxy = start_simulation_proxy(env)
        return launch_dashboard(source_name=source_name, profile_name=profile["id"], env=env)
    finally:
        stop_simulation_proxy(proxy)


if __name__ == "__main__":
    raise SystemExit(main())
