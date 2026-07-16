import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT_DIR / "scripts" / "vaxe.py"
VIRTUALAXE_MODULE_PATH = ROOT_DIR / "scripts" / "virtualaxe.py"


def load_module():
    spec = importlib.util.spec_from_file_location("vaxe_test_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_virtualaxe_module():
    spec = importlib.util.spec_from_file_location("virtualaxe_test_module", VIRTUALAXE_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_pool_target_accepts_host_port():
    module = load_module()

    target = module.parse_pool_target("public-pool.io:3333")

    assert target.host == "public-pool.io"
    assert target.port == 3333
    assert target.difficulty == 0.0001
    assert target.subscribe_agent == ""


def test_vaxe_parser_disables_sim_actions_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VIRTUAL_BITAXE_SIM_ACTIONS", raising=False)
    monkeypatch.delenv("VIRTUAL_BITAXE_SIM_BACKEND_PORT", raising=False)
    module = load_module()

    args = module.parse_args(["--source", "bitaxe"])

    assert args.source == "bitaxe"
    assert args.sim_actions is False
    assert args.sim_backend_port == 18082
    assert module.runtime_ports(args) == ("18080", "18080")


def test_vaxe_without_arguments_prints_help_without_starting(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    module = load_module()

    monkeypatch.setattr(module, "load_virtualaxe_module", lambda: pytest.fail("bare vaxe must not start runtime setup"))

    assert module.main([]) == 0

    output = capsys.readouterr().out
    assert "usage: vaxe" in output
    assert "Start a source-specific virtualAxe operator session." in output
    assert "./vaxe --source bitaxe" in output
    assert "./vaxe --source nerdnos --pool public" in output
    assert "Bare ./vaxe prints this help and does not start QEMU." in output


def test_vaxe_help_describes_profile_and_simulation_options():
    module = load_module()

    output = " ".join(module.build_parser().format_help().split())

    assert "Virtual hardware profile (gamma is the only supported profile)" in output
    assert "Enable the loopback-only /sim/* UI controls" in output
    assert "Firmware HTTP port behind the Simulation Actions proxy" in output


def test_vaxe_parser_accepts_sim_actions_flag():
    module = load_module()

    args = module.parse_args(["--source", "bitaxe", "--sim-actions", "--sim-backend-port", "19082"])

    assert args.source == "bitaxe"
    assert args.sim_actions is True
    assert args.sim_backend_port == 19082
    assert module.runtime_ports(args) == ("19082", "18080")


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_vaxe_parser_rejects_invalid_sim_backend_port(port: str):
    module = load_module()

    with pytest.raises(SystemExit):
        module.parse_args(["--source", "bitaxe", "--sim-actions", "--sim-backend-port", port])


def test_vaxe_parser_accepts_sim_actions_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIRTUAL_BITAXE_SIM_ACTIONS", "1")
    monkeypatch.setenv("VIRTUAL_BITAXE_SIM_BACKEND_PORT", "19083")
    module = load_module()

    args = module.parse_args(["--source", "bitaxe"])

    assert args.source == "bitaxe"
    assert args.sim_actions is True
    assert args.sim_backend_port == 19083
    assert module.runtime_ports(args) == ("19083", "18080")


def test_vaxe_parser_accepts_source_flag():
    module = load_module()

    args = module.parse_args(["--source", "nerdnos"])

    assert args.source == "nerdnos"


def test_vaxe_parser_rejects_positional_source_shorthand():
    module = load_module()

    with pytest.raises(SystemExit):
        module.parse_args(["nerdnos", "--pool", "public"])


def test_vaxe_parser_requires_explicit_source_for_runtime_options():
    module = load_module()

    with pytest.raises(SystemExit):
        module.parse_args(["--pool", "public"])


def test_vaxe_parser_rejects_conflicting_source_inputs():
    module = load_module()

    with pytest.raises(SystemExit):
        module.parse_args(["bitaxe", "--source", "nerdnos"])


def test_vaxe_parser_rejects_unknown_source():
    module = load_module()

    with pytest.raises(SystemExit):
        module.parse_args(["--source", "missing"])


def test_vaxe_accepts_nerdnos_after_api_boot_support(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = load_module()
    virtualaxe = load_virtualaxe_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "qemu_flash.bin").write_bytes(b"flash")
    calls: list[str] = []

    monkeypatch.setattr(module, "load_virtualaxe_module", lambda: virtualaxe)
    monkeypatch.setattr(virtualaxe, "ensure_git_source", lambda _source, entry: (tmp_path / "source", entry))
    monkeypatch.setattr(virtualaxe, "source_probe", lambda _source_dir: {"missingRequiredCapabilities": []})
    monkeypatch.setattr(virtualaxe, "require_shared_virtual_patch_support", lambda *_args: None)
    monkeypatch.setattr(virtualaxe, "prepare_worktree", lambda *_args: tmp_path / "worktree")
    monkeypatch.setattr(
        virtualaxe,
        "build_env",
        lambda *_args: {
            "OUT_DIR": str(out_dir),
            "STATE_DIR": str(tmp_path / "state"),
            "HTTP_PORT": "18080",
            "BASE_URL": "http://127.0.0.1:18080",
            "QEMU_CONTAINER_NAME": "virtualaxe-qemu-test",
        },
    )
    monkeypatch.setattr(virtualaxe, "manifest_matches_requested_build", lambda *_args: True)
    monkeypatch.setattr(virtualaxe, "build_inputs_newer_than_flash", lambda *_args: False)
    monkeypatch.setattr(module, "runtime_is_active", lambda *_args: False)
    monkeypatch.setattr(module, "ensure_runtime_available", lambda _env: calls.append("runtime"))
    monkeypatch.setattr(module, "launch_dashboard", lambda **_kwargs: 0)

    assert module.main(["--source", "nerdnos"]) == 0
    assert calls == ["runtime"]


def test_vaxe_stops_conflicting_source_runtime_before_starting_requested_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    module = load_module()
    virtualaxe = load_virtualaxe_module()
    nerdnos_out = tmp_path / "out" / "nerdnos" / "gamma"
    bitaxe_out = tmp_path / "out"
    legacy_out = tmp_path / "legacy-out"
    for path in (nerdnos_out, bitaxe_out, legacy_out):
        path.mkdir(parents=True, exist_ok=True)
    stopped: list[tuple[str, str]] = []
    started: list[str] = []

    monkeypatch.setattr(module, "load_virtualaxe_module", lambda: virtualaxe)
    monkeypatch.setattr(virtualaxe, "ensure_git_source", lambda _source, entry: (tmp_path / "source", entry))
    monkeypatch.setattr(virtualaxe, "source_probe", lambda _source_dir: {"missingRequiredCapabilities": []})
    monkeypatch.setattr(virtualaxe, "require_shared_virtual_patch_support", lambda *_args: None)
    monkeypatch.setattr(virtualaxe, "prepare_worktree", lambda *_args: tmp_path / "worktree")
    monkeypatch.setattr(
        virtualaxe,
        "build_env",
        lambda *_args: {
            "OUT_DIR": str(nerdnos_out),
            "STATE_DIR": str(tmp_path / "state" / "nerdnos" / "gamma"),
            "HTTP_PORT": "18080",
            "BASE_URL": "http://127.0.0.1:18080",
            "QEMU_CONTAINER_NAME": "virtualaxe-qemu-nerdnos-gamma",
        },
    )
    monkeypatch.setattr(
        virtualaxe,
        "managed_qemu_runtime_specs",
        lambda _profile_id: [
            ("virtualaxe-qemu", legacy_out),
            ("virtualaxe-qemu-bitaxe-gamma", bitaxe_out),
            ("virtualaxe-qemu-nerdnos-gamma", nerdnos_out),
        ],
    )
    monkeypatch.setattr(virtualaxe, "manifest_matches_requested_build", lambda *_args: True)
    monkeypatch.setattr(virtualaxe, "build_inputs_newer_than_flash", lambda *_args: False)
    monkeypatch.setattr(virtualaxe, "ensure_matching_build", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "runtime_is_active",
        lambda container_name, _out_dir: container_name == "virtualaxe-qemu-bitaxe-gamma",
    )
    monkeypatch.setattr(
        module,
        "stop_runtime",
        lambda env: stopped.append((env["QEMU_CONTAINER_NAME"], env["OUT_DIR"])),
    )
    monkeypatch.setattr(
        module,
        "ensure_runtime_available",
        lambda env: started.append(env["QEMU_CONTAINER_NAME"]),
    )
    monkeypatch.setattr(module, "launch_dashboard", lambda **_kwargs: 0)

    assert module.main(["--source", "nerdnos"]) == 0

    assert stopped == [("virtualaxe-qemu-bitaxe-gamma", str(bitaxe_out))]
    assert started == ["virtualaxe-qemu-nerdnos-gamma"]


def test_vaxe_stops_legacy_runtime_even_when_it_uses_requested_output_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    module = load_module()
    requested_out = tmp_path / "out"
    conflicting_out = tmp_path / "out" / "nerdnos" / "gamma"
    requested_out.mkdir()
    conflicting_out.mkdir(parents=True)
    env = {
        "OUT_DIR": str(requested_out),
        "QEMU_CONTAINER_NAME": "virtualaxe-qemu-bitaxe-gamma",
    }
    stopped: list[tuple[str, str]] = []

    class VirtualAxeStub:
        @staticmethod
        def managed_qemu_runtime_specs(_profile_id):
            return [
                ("virtualaxe-qemu", requested_out),
                ("virtualaxe-qemu-bitaxe-gamma", requested_out),
                ("virtualaxe-qemu-nerdnos-gamma", conflicting_out),
            ]

    monkeypatch.setattr(module, "runtime_is_active", lambda container_name, _out_dir: container_name == "virtualaxe-qemu")
    monkeypatch.setattr(
        module,
        "stop_runtime",
        lambda stop_env: stopped.append((stop_env["QEMU_CONTAINER_NAME"], stop_env["OUT_DIR"])),
    )

    module.stop_conflicting_runtimes(env, virtualaxe=VirtualAxeStub(), profile_id="gamma")

    assert stopped == [("virtualaxe-qemu", str(requested_out))]


def test_launch_dashboard_uses_proxy_web_port_when_sim_actions_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = load_module()
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return module.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    env = {
        "HTTP_PORT": "18082",
        "SIM_ACTIONS_ENABLED": "1",
        "WEB_HTTP_PORT": "18080",
        "OUT_DIR": str(tmp_path / "out"),
        "STATE_DIR": str(tmp_path / "state"),
    }

    result = module.launch_dashboard(source_name="vanilla", profile_name="gamma", env=env)

    assert result == 0
    dashboard_command = calls[-1]
    assert dashboard_command[dashboard_command.index("--http-port") + 1] == "18082"
    assert dashboard_command[dashboard_command.index("--web-http-port") + 1] == "18080"


def test_ensure_runtime_available_reuses_reachable_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = load_module()
    calls: list[str] = []
    env = {
        "BASE_URL": "http://127.0.0.1:18080",
        "HTTP_PORT": "18080",
        "OUT_DIR": str(tmp_path / "out"),
        "QEMU_CONTAINER_NAME": "virtualaxe-qemu",
    }

    monkeypatch.setattr(module, "runtime_is_active", lambda _name, _out_dir: True)
    monkeypatch.setattr(module, "runtime_api_reachable", lambda _env: True)
    monkeypatch.setattr(module, "stop_runtime", lambda _env: calls.append("stop"))
    monkeypatch.setattr(module, "start_runtime", lambda _env: calls.append("start"))
    monkeypatch.setattr(module, "wait_for_http", lambda _env: calls.append("wait"))

    module.ensure_runtime_available(env)

    assert calls == []


def test_ensure_runtime_available_restarts_unreachable_active_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = load_module()
    calls: list[str] = []
    env = {
        "BASE_URL": "http://127.0.0.1:18082",
        "HTTP_PORT": "18082",
        "OUT_DIR": str(tmp_path / "out"),
        "QEMU_CONTAINER_NAME": "virtualaxe-qemu",
    }

    monkeypatch.setattr(module, "runtime_is_active", lambda _name, _out_dir: True)
    monkeypatch.setattr(module, "runtime_api_reachable", lambda _env: False)
    monkeypatch.setattr(module, "stop_runtime", lambda _env: calls.append("stop"))
    monkeypatch.setattr(module, "start_runtime", lambda _env: calls.append("start"))
    monkeypatch.setattr(module, "wait_for_http", lambda _env: calls.append("wait"))

    module.ensure_runtime_available(env)

    assert calls == ["stop", "start", "wait"]


def test_start_runtime_reports_container_runtime_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = load_module()

    def fake_run(command, **kwargs):
        assert kwargs["capture_output"] is True
        assert kwargs["env"]["BACKGROUND"] == "1"
        return module.subprocess.CompletedProcess(command, 125, "", 'proxy already running\n')

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    env = {
        "OUT_DIR": str(tmp_path / "out"),
        "QEMU_CONTAINER_NAME": "virtualaxe-qemu-bitaxe-gamma",
    }

    with pytest.raises(SystemExit) as excinfo:
        module.start_runtime(env)

    message = str(excinfo.value)
    assert "Unable to start virtualAxe." in message
    assert str(tmp_path / "out" / "qemu.log") in message
    assert "proxy already running" in message


def test_wait_for_http_suppresses_expected_startup_curl_noise(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    count_file = tmp_path / "curl-count"
    write_fake_executable(
        bin_dir / "curl",
        f"""#!/usr/bin/env bash
set -euo pipefail
count=0
if [[ -f "{count_file}" ]]; then
  count="$(cat "{count_file}")"
fi
count=$((count + 1))
printf '%s' "${{count}}" > "{count_file}"
case "${{count}}" in
  1)
    echo "curl: (52) Empty reply from server" >&2
    exit 52
    ;;
  2)
    echo "curl: (28) Operation timed out after 5002 milliseconds with 0 bytes received" >&2
    exit 28
    ;;
  *)
    printf '{{}}\\n'
    exit 0
    ;;
esac
""",
    )
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "BASE_URL": "http://127.0.0.1:18080",
        "TIMEOUT_SECONDS": "5",
        "STABLE_SUCCESS_COUNT": "1",
        "CURL_MAX_TIME_SECONDS": "1",
    }

    result = subprocess.run(
        [str(ROOT_DIR / "scripts" / "wait-for-http.sh")],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert count_file.read_text(encoding="utf-8") == "3"


def test_wait_for_http_reports_last_curl_error_on_timeout(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_executable(
        bin_dir / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
echo "curl: (28) Operation timed out after 5002 milliseconds with 0 bytes received" >&2
exit 28
""",
    )
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "BASE_URL": "http://127.0.0.1:18080",
        "TIMEOUT_SECONDS": "1",
        "STABLE_SUCCESS_COUNT": "1",
        "CURL_MAX_TIME_SECONDS": "1",
    }

    result = subprocess.run(
        [str(ROOT_DIR / "scripts" / "wait-for-http.sh")],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Timed out waiting for http://127.0.0.1:18080/api/system/info" in result.stderr
    assert "Last readiness error: curl: (28) Operation timed out" in result.stderr


@pytest.mark.parametrize(
    ("raw", "host", "port", "difficulty", "subscribe_agent"),
    [
        ("public", "public-pool.io", 3333, 0.0001, ""),
        ("bitronics", "pool.bitronics.store", 3334, 0.0001, "NerdMinerV2/virtualAxe-gamma"),
        ("nerdminers", "pool.nerdminers.org", 3333, 0.0005, "NerdMinerV2/virtualAxe-gamma"),
    ],
)
def test_parse_pool_target_accepts_verified_pool_presets(raw: str, host: str, port: int, difficulty: float, subscribe_agent: str):
    module = load_module()

    target = module.parse_pool_target(raw)

    assert target.host == host
    assert target.port == port
    assert target.difficulty == difficulty
    assert target.subscribe_agent == subscribe_agent


@pytest.mark.parametrize("raw", ["public-pool.io", "public-pool.io:notaport", ":3333", "public-pool.io:70000"])
def test_parse_pool_target_rejects_invalid_values(raw: str):
    module = load_module()

    with pytest.raises(SystemExit):
        module.parse_args(["--source", "bitaxe", "--pool", raw])


def test_pool_override_differs_matches_host_and_port():
    module = load_module()
    target = module.PoolTarget(host="public-pool.io", port=3333)

    assert module.pool_override_differs({"stratumURL": "public-pool.io", "stratumPort": 3333}, target) is False
    assert module.pool_override_differs({"stratumURL": "pool.bitronics.store", "stratumPort": 3333}, target) is True
    assert module.pool_override_differs({"stratumURL": "public-pool.io", "stratumPort": 4444}, target) is True


def test_pool_override_differs_checks_verified_pool_runtime_fields():
    module = load_module()
    target = module.parse_pool_target("bitronics")

    assert module.pool_override_differs(
        {
            "stratumURL": "pool.bitronics.store",
            "stratumPort": 3334,
            "stratumSuggestedDifficulty": 0.0001,
            "stratumSubscribeAgent": "NerdMinerV2/virtualAxe-gamma",
        },
        target,
    ) is False
    assert module.pool_override_differs(
        {
            "stratumURL": "pool.bitronics.store",
            "stratumPort": 3334,
            "stratumSuggestedDifficulty": 1,
            "stratumSubscribeAgent": "NerdMinerV2/virtualAxe-gamma",
        },
        target,
    ) is True
    assert module.pool_override_differs(
        {
            "stratumURL": "pool.bitronics.store",
            "stratumPort": 3334,
            "stratumSuggestedDifficulty": 0.0001,
            "stratumSubscribeAgent": "",
        },
        target,
    ) is True


def test_pool_patch_payload_includes_verified_pool_fields():
    module = load_module()

    payload = module.pool_patch_payload(module.parse_pool_target("nerdminers"))

    assert payload == {
        "stratumURL": "pool.nerdminers.org",
        "stratumPort": 3333,
        "stratumSuggestedDifficulty": 0.0005,
        "stratumSubscribeAgent": "NerdMinerV2/virtualAxe-gamma",
    }


def test_virtualaxe_build_env_ignores_ambient_repo_overrides(monkeypatch: pytest.MonkeyPatch):
    module = load_virtualaxe_module()
    profile = module.load_profile("gamma")

    monkeypatch.setenv("POOL_HOST", "pool.bitronics.store")
    monkeypatch.setenv("POOL_PORT", "4444")
    monkeypatch.setenv("POOL_SUBSCRIBE_AGENT", "NerdMinerV2/ambient")
    monkeypatch.setenv("VIRTUAL_BITAXE_SIM_BACKEND_PORT", "19082")
    monkeypatch.setenv("VIRTUAL_BITAXE_SIM_ACTIONS", "1")
    monkeypatch.setenv("VIRTUAL_TRANSPORT_HOST", "10.0.2.2")
    monkeypatch.setenv("STRATUM_REPLAY_PORT", "9999")

    args = type(
        "Args",
        (),
        {
            "out_dir": None,
            "state_dir": None,
            "json": False,
            "pool_host": module.DEFAULT_POOL_HOST,
            "pool_port": int(module.DEFAULT_POOL_PORT),
            "pool_user": module.DEFAULT_POOL_USER,
            "pool_pass": module.DEFAULT_POOL_PASS,
            "pool_diff": module.DEFAULT_POOL_DIFF,
            "pool_tls": module.DEFAULT_POOL_TLS,
            "pool_cert": module.DEFAULT_POOL_CERT,
            "hostname": module.DEFAULT_HOSTNAME,
            "virtual_asic_mode": module.DEFAULT_VIRTUAL_ASIC_MODE,
            "http_port": module.DEFAULT_HTTP_PORT,
            "reset_persisted_state": False,
        },
    )()

    env = module.build_env(args, "vanilla", ROOT_DIR / ".worktrees" / "vanilla" / "test", profile)

    assert env["POOL_HOST"] == module.DEFAULT_POOL_HOST
    assert env["POOL_PORT"] == module.DEFAULT_POOL_PORT
    assert env["POOL_USER"] == module.DEFAULT_POOL_USER
    assert env["POOL_SUBSCRIBE_AGENT"] == ""
    assert env["QEMU_CONTAINER_NAME"] == "virtualaxe-qemu-bitaxe-gamma"
    assert "VIRTUAL_TRANSPORT_HOST" not in env
    assert "VIRTUAL_TRANSPORT_PORT" not in env
    assert "VIRTUAL_BITAXE_SIM_BACKEND_PORT" not in env
    assert "VIRTUAL_BITAXE_SIM_ACTIONS" not in env
    assert "STRATUM_REPLAY_PORT" not in env
    assert env["RESET_PERSISTED_STATE"] == "0"


def test_virtualaxe_build_env_uses_source_specific_qemu_container():
    module = load_virtualaxe_module()
    profile = module.load_profile("gamma")
    args = type(
        "Args",
        (),
        {
            "out_dir": None,
            "state_dir": None,
            "json": False,
            "pool_host": module.DEFAULT_POOL_HOST,
            "pool_port": int(module.DEFAULT_POOL_PORT),
            "pool_user": module.DEFAULT_POOL_USER,
            "pool_pass": module.DEFAULT_POOL_PASS,
            "pool_diff": module.DEFAULT_POOL_DIFF,
            "pool_tls": module.DEFAULT_POOL_TLS,
            "pool_cert": module.DEFAULT_POOL_CERT,
            "hostname": module.DEFAULT_HOSTNAME,
            "virtual_asic_mode": module.DEFAULT_VIRTUAL_ASIC_MODE,
            "http_port": module.DEFAULT_HTTP_PORT,
            "reset_persisted_state": False,
        },
    )()

    bitaxe_env = module.build_env(args, "bitaxe", ROOT_DIR / ".worktrees" / "bitaxe" / "test", profile)
    nerdnos_env = module.build_env(args, "nerdnos", ROOT_DIR / ".worktrees" / "nerdnos" / "test", profile)

    assert bitaxe_env["QEMU_CONTAINER_NAME"] == "virtualaxe-qemu-bitaxe-gamma"
    assert nerdnos_env["QEMU_CONTAINER_NAME"] == "virtualaxe-qemu-nerdnos-gamma"
    assert bitaxe_env["OUT_DIR"] != nerdnos_env["OUT_DIR"]


def test_virtualaxe_run_timeout_output_is_json_serializable(monkeypatch: pytest.MonkeyPatch):
    module = load_virtualaxe_module()

    def fake_run(*_args, **_kwargs):
        raise module.subprocess.TimeoutExpired(["demo"], 1, output=b"stdout-bytes", stderr=b"stderr-bytes")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.run(["demo"], capture=True, timeout=1)

    assert result.returncode == 124
    assert result.stdout == "stdout-bytes"
    assert result.stderr == "stderr-bytes"


def test_virtualaxe_build_progress_parser_accepts_stage_markers():
    module = load_virtualaxe_module()

    parsed = module.parse_build_progress_line("[virtualAxe] [12s] 4/8: Checking AxeOS frontend dependencies.")

    assert parsed == {
        "elapsed": "12s",
        "step": 4,
        "total": 8,
        "message": "Checking AxeOS frontend dependencies.",
    }
    rendered, step, total, marker = module.render_build_progress_line(
        "[virtualAxe] [12s] 4/8: Checking AxeOS frontend dependencies."
    )
    assert step == 4
    assert total == 8
    assert marker == "Checking AxeOS frontend dependencies."
    assert "[############............] 4/8" in rendered


def test_virtualaxe_build_progress_parser_accepts_note_markers():
    module = load_virtualaxe_module()

    rendered, step, total, marker = module.render_build_progress_line(
        "[virtualAxe] [3s] Container image virtualaxe-dev is already available."
    )

    assert step is None
    assert total is None
    assert marker == "Container image virtualaxe-dev is already available."
    assert rendered == "[virtualAxe] note: Container image virtualaxe-dev is already available. (3s)"


def test_virtualaxe_build_progress_parser_keeps_non_marker_lines_diagnostic():
    module = load_virtualaxe_module()

    rendered, step, total, marker = module.render_build_progress_line("[virtualAxe] raw diagnostic")

    assert step is None
    assert total is None
    assert marker == "raw diagnostic"
    assert rendered == "[virtualAxe] note: raw diagnostic"


def test_virtualaxe_build_cockpit_uses_interactive_tty_without_term_prefix():
    module = load_virtualaxe_module()

    class FakeStream:
        def __init__(self, tty: bool):
            self.tty = tty

        def isatty(self):
            return self.tty

    assert module.should_render_build_cockpit(FakeStream(True), {"TERM": "xterm-256color"}) is True
    assert module.should_render_build_cockpit(FakeStream(True), {}) is True
    assert module.should_render_build_cockpit(FakeStream(True), {"TERM": "dumb"}) is True
    assert module.should_render_build_cockpit(FakeStream(False), {"TERM": "xterm-256color"}) is False
    assert module.should_render_build_cockpit(FakeStream(True), {"TERM": "xterm-256color", "CI": "1"}) is False
    assert module.should_colorize_build_cockpit({"TERM": "xterm-256color"}) is True
    assert module.should_colorize_build_cockpit({}) is False
    assert module.should_colorize_build_cockpit({"TERM": "dumb"}) is False
    assert module.should_colorize_build_cockpit({"TERM": "xterm-256color", "NO_COLOR": "1"}) is False


def test_virtualaxe_build_cockpit_lines_include_reference_paths_and_flight_recorder(tmp_path: Path):
    module = load_virtualaxe_module()
    state = module.BuildCockpitState(
        source_name="bitaxe",
        profile_id="gamma",
        out_dir=tmp_path / "out",
        log_path=tmp_path / "out" / "build.log",
        started_at=100.0,
        last_activity_at=140.0,
        current_stage_started_at=120.0,
        log_bytes=2_621_440,
        log_lines=12_345,
        current_step=7,
        total_steps=8,
        current_message="Building firmware and reusable QEMU flash image.",
    )

    lines = "\n".join(module.build_cockpit_lines(state, now=160.0, color=False))

    assert "virtualAxe build cockpit" in lines
    assert "bitaxe/gamma" in lines
    assert "stage 7/8: Build firmware image" in lines
    assert "elapsed 1m00s" in lines
    assert "updated 20s ago" in lines
    assert "running 40s" in lines
    assert "12,345 lines / 2.5 MiB" in lines
    assert "reference 7m52s" in lines
    assert "local clean build" in lines
    assert "qemu_flash.bin" in lines
    assert "manifest.json" in lines
    assert "build.log" in lines
    assert "flight recorder" in lines
    assert "Build firmware image" in lines
    assert "│" in lines
    assert "88%" not in lines


def test_virtualaxe_build_cockpit_elapsed_advances_without_log_activity(tmp_path: Path):
    module = load_virtualaxe_module()
    state = module.BuildCockpitState(
        source_name="bitaxe",
        profile_id="gamma",
        out_dir=tmp_path / "out",
        log_path=tmp_path / "out" / "build.log",
        started_at=100.0,
        last_activity_at=125.0,
        current_stage_started_at=130.0,
        current_step=7,
        total_steps=8,
        current_message="Building firmware and reusable QEMU flash image.",
    )

    first = "\n".join(module.build_cockpit_lines(state, now=160.0, color=False))
    later = "\n".join(module.build_cockpit_lines(state, now=220.0, color=False))

    assert "elapsed 1m00s" in first
    assert "updated 35s ago" in first
    assert "running 30s" in first
    assert "elapsed 2m00s" in later
    assert "updated 1m35s ago" in later
    assert "running 1m30s" in later


def test_virtualaxe_build_cockpit_cursor_visibility_sequences():
    module = load_virtualaxe_module()
    stream = io.StringIO()

    module.set_build_cockpit_cursor_visible(stream, False)
    module.set_build_cockpit_cursor_visible(stream, True)

    assert stream.getvalue() == "\033[?25l\033[?25h"


def test_virtualaxe_build_footer_points_to_image_and_next_commands(tmp_path: Path, capsys):
    module = load_virtualaxe_module()
    out_dir = tmp_path / "out" / "nerdnos" / "gamma"

    module.print_build_dashboard_footer(
        source_name="nerdnos",
        profile_id="gamma",
        out_dir=out_dir,
        log_path=out_dir / "build.log",
        elapsed="2m07s",
        succeeded=True,
    )

    output = capsys.readouterr().err
    assert "QEMU firmware image ready in 2m07s." in output
    assert "qemu_flash.bin" in output
    assert "manifest.json" in output
    assert "./vaxe --source nerdnos" in output
    assert "make verify-submit-replay SOURCE=nerdnos" in output
    assert "make build SOURCE=nerdnos" in output


def test_virtualaxe_build_footer_reports_failure_retry(tmp_path: Path, capsys):
    module = load_virtualaxe_module()
    out_dir = tmp_path / "out"

    module.print_build_dashboard_footer(
        source_name="bitaxe",
        profile_id="gamma",
        out_dir=out_dir,
        log_path=out_dir / "build.log",
        elapsed="31s",
        succeeded=False,
    )

    output = capsys.readouterr().err
    assert "Build failed after 31s." in output
    assert "build.log" in output
    assert "source/profile: bitaxe/gamma" in output
    assert "retry: make build SOURCE=bitaxe" in output


def test_virtualaxe_run_build_script_keeps_captured_mode_line_oriented(monkeypatch: pytest.MonkeyPatch):
    module = load_virtualaxe_module()
    calls: list[str] = []

    def fake_run(command, **kwargs):
        calls.append("run")
        assert command == [str(ROOT_DIR / "scripts" / "build-virtual.sh")]
        assert kwargs["capture"] is True
        return module.subprocess.CompletedProcess(command, 0, "json-safe", "")

    monkeypatch.setattr(module, "run", fake_run)
    monkeypatch.setattr(module, "run_build_with_progress", lambda _env: calls.append("cockpit"))

    result = module.run_build_script({"OUT_DIR": str(ROOT_DIR / "out")}, capture=True)

    assert result.stdout == "json-safe"
    assert calls == ["run"]


def test_virtualaxe_run_build_with_progress_clears_stale_log_before_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    module = load_virtualaxe_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    log_path = out_dir / "build.log"
    log_path.write_text("[virtualAxe] [999s] 8/8: Stale previous build completion.\n", encoding="utf-8")

    class FakeProcess:
        returncode = 0

        def __init__(self, *_args, **_kwargs):
            assert log_path.read_text(encoding="utf-8") == ""
            log_path.write_text("[virtualAxe] [0s] 1/8: Fresh build start.\n", encoding="utf-8")

        def poll(self):
            return self.returncode

    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)

    result = module.run_build_with_progress(
        {"OUT_DIR": str(out_dir), "SOURCE_NAME": "bitaxe", "VIRTUAL_PROFILE": "gamma"}
    )

    assert result.returncode == 0
    assert "Stale previous build completion" not in log_path.read_text(encoding="utf-8")


def write_fake_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def run_container_runtime_helper(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
    }
    (tmp_path / "home").mkdir()
    return subprocess.run(["bash", "-c", script], cwd=ROOT_DIR, env=env, text=True, capture_output=True, check=False)


def test_container_runtime_selector_starts_existing_podman_machine(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_file = tmp_path / "podman-running"
    write_fake_executable(
        bin_dir / "podman",
        f"""#!/usr/bin/env bash
set -euo pipefail
case "${{1:-}}" in
  info)
    [[ -f "{state_file}" ]]
    ;;
  machine)
    case "${{2:-}}" in
      list) exit 0 ;;
      start) touch "{state_file}"; exit 0 ;;
      *) exit 1 ;;
    esac
    ;;
  *) exit 0 ;;
esac
""",
    )
    script = f"""
set -euo pipefail
source "{ROOT_DIR / 'scripts' / 'container-runtime.sh'}"
virtualaxe_select_execution_environment
printf '%s/%s\\n' "${{EXECUTION_MODE}}" "${{CONTAINER_RUNTIME}}"
"""

    result = run_container_runtime_helper(tmp_path, script)

    assert result.returncode == 0
    assert result.stdout.splitlines()[-1] == "container/podman"
    assert "Trying to start the default Podman machine" in result.stdout


def test_container_runtime_selector_falls_back_to_docker_when_podman_unusable(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_executable(
        bin_dir / "podman",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "machine" && "${2:-}" == "list" ]]; then
  exit 1
fi
exit 125
""",
    )
    write_fake_executable(
        bin_dir / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "info" ]]
""",
    )
    script = f"""
set -euo pipefail
source "{ROOT_DIR / 'scripts' / 'container-runtime.sh'}"
virtualaxe_select_execution_environment
printf '%s/%s\\n' "${{EXECUTION_MODE}}" "${{CONTAINER_RUNTIME}}"
"""

    result = run_container_runtime_helper(tmp_path, script)

    assert result.returncode == 0
    assert result.stdout.strip() == "container/docker"


def test_container_runtime_selector_reports_unusable_host_without_recreating_podman(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_executable(
        bin_dir / "podman",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "machine" && "${2:-}" == "list" ]]; then
  exit 1
fi
exit 125
""",
    )
    write_fake_executable(
        bin_dir / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
exit 1
""",
    )
    script = f"""
set -euo pipefail
source "{ROOT_DIR / 'scripts' / 'container-runtime.sh'}"
virtualaxe_select_execution_environment
"""

    result = run_container_runtime_helper(tmp_path, script)

    assert result.returncode == 1
    assert "No usable container or native ESP-IDF runtime is available." in result.stderr
    assert "Podman is installed but is not reachable" in result.stderr
    assert "Docker is installed but is not reachable" in result.stderr
    assert "will not create or recreate" in result.stderr


def test_virtualaxe_source_probe_allows_clean_upstream_before_virtual_seam_patch(tmp_path: Path):
    module = load_virtualaxe_module()
    source_dir = tmp_path / "clean-upstream"
    (source_dir / "main" / "http_server" / "axe-os").mkdir(parents=True)
    (source_dir / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8")
    (source_dir / "main" / "http_server" / "http_server.c").write_text("void http_server(void) {}\n", encoding="utf-8")
    (source_dir / "main" / "device_config.c").write_text("void device_config(void) {}\n", encoding="utf-8")

    probe = module.source_probe(source_dir)

    assert probe["capabilities"]["hasVirtualAsicSeam"] is False
    assert probe["capabilities"]["supportsGammaProfiles"] is True
    assert probe["missingRequiredCapabilities"] == []


def test_virtualaxe_source_probe_accepts_nerdnos_cpp_layout(tmp_path: Path):
    module = load_virtualaxe_module()
    source_dir = tmp_path / "nerdnos"
    (source_dir / "main" / "http_server" / "axe-os").mkdir(parents=True)
    (source_dir / "main" / "boards").mkdir(parents=True)
    (source_dir / "components" / "bm1397" / "include").mkdir(parents=True)
    (source_dir / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8")
    (source_dir / "main" / "http_server" / "http_server.cpp").write_text("void http_server() {}\n", encoding="utf-8")
    (source_dir / "main" / "boards" / "board.h").write_text("class Board {};\n", encoding="utf-8")
    (source_dir / "components" / "bm1397" / "include" / "asic.h").write_text("class Asic {};\n", encoding="utf-8")

    probe = module.source_probe(source_dir)

    assert probe["flavor"] == "nerdnos-esp-miner"
    assert probe["capabilities"]["supportsGammaProfiles"] is True
    assert probe["missingRequiredCapabilities"] == []


def test_virtualaxe_prepare_worktree_passes_selected_source_to_patch_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = load_virtualaxe_module()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    captured_env: dict[str, str] = {}

    def fake_run(command, *, env=None, **_kwargs):
        captured_env.update(env or {})
        return module.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module, "WORKTREES_DIR", tmp_path / "worktrees")
    monkeypatch.setattr(module, "run", fake_run)

    target = module.prepare_worktree("nerdnos", source_dir, {"ref": "c18abafe"})

    assert target == tmp_path / "worktrees" / "nerdnos" / "c18abafe"
    assert captured_env["SOURCE_NAME"] == "nerdnos"
    assert captured_env["SOURCE_DIR"] == str(source_dir)
    assert captured_env["PATCH_TARGET_DIR"] == str(target)


def test_virtualaxe_source_support_gates_runtime_commands():
    module = load_virtualaxe_module()

    module.require_source_support("bitaxe", "api_boot_verified", "run")
    module.require_source_support("nerdnos", "api_boot_verified", "run")
    module.require_source_support("nerdnos", "submit_replay_verified", "verify-submit-replay")
    module.require_source_support("nerdnos", "live_verified", "verify-release")


def test_virtualaxe_verify_submit_replay_command_allows_nerdnos_after_submit_replay_support(monkeypatch: pytest.MonkeyPatch):
    module = load_virtualaxe_module()
    calls: list[str] = []

    def fake_ensure_git_source(*_args, **_kwargs):
        calls.append("ensure_git_source")
        raise RuntimeError("source gate passed")

    monkeypatch.setattr(module, "ensure_git_source", fake_ensure_git_source)

    parser = module.build_parser()
    args = parser.parse_args(["verify-submit-replay", "--source", "nerdnos"])

    with pytest.raises(RuntimeError, match="source gate passed"):
        module.command_verify_submit_replay(args)
    assert calls == ["ensure_git_source"]


def test_virtualaxe_verify_test_ci_command_allows_nerdnos_after_submit_replay_support(monkeypatch: pytest.MonkeyPatch):
    module = load_virtualaxe_module()
    calls: list[str] = []

    def fake_ensure_git_source(*_args, **_kwargs):
        calls.append("ensure_git_source")
        raise RuntimeError("source gate passed")

    monkeypatch.setattr(module, "ensure_git_source", fake_ensure_git_source)

    parser = module.build_parser()
    args = parser.parse_args(["verify-test-ci", "--source", "nerdnos"])

    with pytest.raises(RuntimeError, match="source gate passed"):
        module.command_verify_test_ci(args)
    assert calls == ["ensure_git_source"]


def test_virtualaxe_verify_test_ci_uses_api_boot_smoke_when_source_has_no_test_ci(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
):
    module = load_virtualaxe_module()
    source_dir = tmp_path / "source"
    (source_dir / "main" / "http_server" / "axe-os").mkdir(parents=True)
    (source_dir / "CMakeLists.txt").write_text("idf_component_register()\n", encoding="utf-8")
    (source_dir / "main" / "http_server" / "http_server.cpp").write_text("void http_server() {}\n", encoding="utf-8")
    (source_dir / "main" / "boards").mkdir(parents=True)
    (source_dir / "main" / "boards" / "board.h").write_text("class Board {};\n", encoding="utf-8")
    (source_dir / "components" / "bm1397" / "include").mkdir(parents=True)
    (source_dir / "components" / "bm1397" / "include" / "asic.h").write_text("class Asic {};\n", encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    calls: list[tuple[list[str], dict[str, str], bool]] = []

    monkeypatch.setattr(module, "ensure_git_source", lambda *_args, **_kwargs: (source_dir, {}))
    monkeypatch.setattr(module, "prepare_worktree", lambda *_args, **_kwargs: worktree)

    def fake_run(command, *, env=None, capture=False, **_kwargs):
        calls.append(([str(part) for part in command], env or {}, capture))
        return module.subprocess.CompletedProcess(command, 0, "api boot ok", "")

    monkeypatch.setattr(module, "run", fake_run)

    parser = module.build_parser()
    args = parser.parse_args(["verify-test-ci", "--source", "nerdnos", "--json"])

    assert module.command_verify_test_ci(args) == 0
    assert len(calls) == 1
    command, env, capture = calls[0]
    assert command == [str(ROOT_DIR / "scripts" / "run-e2e.sh"), "--api-only"]
    assert env["SOURCE_NAME"] == "nerdnos"
    assert env["VIRTUAL_PROFILE"] == "gamma"
    assert env["POOL_HOST"] == module.DEFAULT_SUBMIT_REPLAY_GUEST_POOL_HOST
    assert env["POOL_PORT"] == "1"
    assert env["HTTP_PORT"] == str(module.DEFAULT_SUBMIT_REPLAY_HTTP_PORT)
    assert env["FALLBACK_POOL_HOST"] == module.DEFAULT_SUBMIT_REPLAY_GUEST_POOL_HOST
    assert env["FALLBACK_POOL_PORT"] == "1"
    assert env["RESET_PERSISTED_STATE"] == "1"
    assert capture is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["phase"] == "api-boot"
    assert payload["source"] == "nerdnos"
    assert payload["profile"] == "gamma"


def test_virtualaxe_parser_rejects_unsupported_gamma_duo_profile():
    module = load_virtualaxe_module()
    parser = module.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["build", "--profile", "gamma-duo"])


def test_virtualaxe_parser_rejects_host_virtual_asic_mode():
    module = load_virtualaxe_module()
    parser = module.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["build", "--virtual-asic-mode", "host"])


def test_virtualaxe_parser_accepts_fractional_pool_difficulty():
    module = load_virtualaxe_module()
    parser = module.build_parser()

    args = parser.parse_args(["build", "--pool-diff", "0.001"])

    assert args.pool_diff == 0.001


def test_virtualaxe_verify_release_parser_defaults_to_gamma_pool_smoke_suite():
    module = load_virtualaxe_module()

    parser = module.build_parser()
    args = parser.parse_args(["verify-release"])

    assert args.command == "verify-release"
    assert args.source == "bitaxe"
    assert args.pool_user == module.DEFAULT_VERIFY_RELEASE_POOL_USER
    assert args.http_port == module.DEFAULT_HTTP_PORT
    assert args.mode == "smoke"
    assert args.qualification is False


def test_virtualaxe_patch_check_parser_defaults_to_configured_pin():
    module = load_virtualaxe_module()

    parser = module.build_parser()
    args = parser.parse_args(["patch-check", "--json"])

    assert args.command == "patch-check"
    assert args.source == "bitaxe"
    assert args.upstream_ref is None
    assert args.fetch is False
    assert args.json is True


def test_virtualaxe_patch_check_metadata_reports_patch_surfaces():
    module = load_virtualaxe_module()

    metadata = module.patch_series_metadata()
    surfaces = module.touched_surface_summary(metadata)

    assert len(metadata) == 13
    assert metadata[-1]["patch"] == "0048-virtual-pool-support-low-difficulty-interoperability.patch"
    assert "components/asic/virtual_asic.c" in surfaces
    assert "0046-virtual-guard-submit-boundary-with-work-generations.patch" in surfaces["main/tasks/asic_result_task.c"]


def test_virtualaxe_patch_check_metadata_supports_nerdnos_submit_replay_series():
    module = load_virtualaxe_module()

    metadata = module.patch_series_metadata("nerdnos")

    assert len(metadata) == 6
    assert metadata[0]["patch"] == "0001-nerdnos-add-virtual-gamma-api-boot-path.patch"
    assert metadata[1]["patch"] == "0002-nerdnos-add-virtual-asic-submit-path.patch"
    assert metadata[2]["patch"] == "0003-nerdnos-keep-virtual-mining-api-responsive.patch"
    assert metadata[3]["patch"] == "0004-nerdnos-low-difficulty-pool-interoperability.patch"
    assert metadata[4]["patch"] == "0005-nerdnos-precompute-virtual-nonce-search-material.patch"
    assert metadata[5]["patch"] == "0006-nerdnos-brand-virtualaxe-header.patch"


def test_virtualaxe_doctor_reports_canonical_sources_only(monkeypatch: pytest.MonkeyPatch, capsys):
    module = load_virtualaxe_module()

    class Registry:
        def as_legacy_payload(self, *, include_aliases: bool = True):
            assert include_aliases is False
            return {
                "sources": {
                    "bitaxe": {"ref": "bitaxe-pin"},
                    "nerdnos": {"ref": "nerdnos-pin"},
                }
            }

    monkeypatch.setattr(module, "source_registry", lambda: Registry())
    monkeypatch.setattr(module, "ensure_git_source", lambda name, entry, **_kwargs: (Path(f"/tmp/{name}"), entry))
    monkeypatch.setattr(
        module,
        "source_probe",
        lambda _source_dir: {
            "capabilities": {"supportsGammaProfiles": False},
            "missingRequiredCapabilities": [],
        },
    )
    monkeypatch.setattr(
        module,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "tool version\n", ""),
    )

    args = type("Args", (), {"json": True})()

    assert module.command_doctor(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert set(payload["sources"]) == {"bitaxe", "nerdnos"}


def test_virtualaxe_patch_check_applies_resolved_upstream_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    module = load_virtualaxe_module()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    captured_env: dict[str, str] = {}

    monkeypatch.setattr(
        module,
        "load_sources",
        lambda: {"sources": {"vanilla": {"repoUrl": "https://example.invalid/esp-miner", "ref": "pin"}}},
    )
    monkeypatch.setattr(module, "ensure_git_source", lambda *_args, **_kwargs: (source_dir, {"ref": "pin"}))
    monkeypatch.setattr(module, "resolve_git_ref", lambda _source_dir, _ref: ("abc123resolved", ""))
    monkeypatch.setattr(module, "patch_series_metadata", lambda *_args: [])
    monkeypatch.setattr(module, "touched_surface_summary", lambda _metadata: {})

    def fake_run(command, *, env=None, **_kwargs):
        captured_env.update(env or {})
        return module.subprocess.CompletedProcess(command, 0, "Applying 0001.patch\n", "")

    monkeypatch.setattr(module, "run", fake_run)

    args = type(
        "Args",
        (),
        {
            "source": "vanilla",
            "fetch": False,
            "upstream_ref": "origin/master",
            "target_dir": str(tmp_path / "target"),
            "json": True,
        },
    )()

    assert module.command_patch_check(args) == 0
    assert captured_env["UPSTREAM_REF"] == "abc123resolved"
    assert '"upstreamRef": "origin/master"' in capsys.readouterr().out


def test_virtualaxe_verify_release_parser_accepts_pool_user_override():
    module = load_virtualaxe_module()

    parser = module.build_parser()
    args = parser.parse_args(["verify-release", "--pool-user", "bc1testpooluser"])

    assert args.pool_user == "bc1testpooluser"


def test_virtualaxe_verify_release_parser_accepts_qualification_mode():
    module = load_virtualaxe_module()

    parser = module.build_parser()
    args = parser.parse_args(["verify-release", "--qualification"])

    assert args.command == "verify-release"
    assert args.qualification is True


def test_virtualaxe_verify_submit_replay_parser_defaults_to_gamma_cpu_harness():
    module = load_virtualaxe_module()

    parser = module.build_parser()
    args = parser.parse_args(["verify-submit-replay"])

    assert args.command == "verify-submit-replay"
    assert args.source == "bitaxe"
    assert args.profile == "gamma"
    assert args.guest_pool_host == module.DEFAULT_SUBMIT_REPLAY_GUEST_POOL_HOST
    assert args.replay_port == module.DEFAULT_SUBMIT_REPLAY_PORT
    assert args.replay_difficulty < 1.0
    assert args.replay_extranonce1 == module.DEFAULT_SUBMIT_REPLAY_EXTRANONCE1
    assert args.replay_extranonce2_size == module.DEFAULT_SUBMIT_REPLAY_EXTRANONCE2_SIZE
    assert args.http_port == module.DEFAULT_SUBMIT_REPLAY_HTTP_PORT


def test_virtualaxe_submit_replay_env_forces_local_cpu_replay(monkeypatch: pytest.MonkeyPatch):
    module = load_virtualaxe_module()
    profile = module.load_profile("gamma")
    replay_difficulty = 0.0005
    monkeypatch.setenv("STRATUM_REPLAY_PORT", "9999")

    args = type(
        "Args",
        (),
        {
            "out_dir": None,
            "state_dir": None,
            "guest_pool_host": module.DEFAULT_SUBMIT_REPLAY_GUEST_POOL_HOST,
            "replay_host": "0.0.0.0",
            "replay_port": module.DEFAULT_SUBMIT_REPLAY_PORT,
            "replay_difficulty": replay_difficulty,
            "replay_extranonce1": module.DEFAULT_SUBMIT_REPLAY_EXTRANONCE1,
            "replay_extranonce2_size": module.DEFAULT_SUBMIT_REPLAY_EXTRANONCE2_SIZE,
            "replay_timeout": module.DEFAULT_SUBMIT_REPLAY_TIMEOUT,
            "pool_user": module.DEFAULT_SUBMIT_REPLAY_USER,
            "http_port": module.DEFAULT_SUBMIT_REPLAY_HTTP_PORT,
            "qemu_container_name": module.DEFAULT_SUBMIT_REPLAY_CONTAINER_NAME,
        },
    )()

    env = module.build_submit_replay_env(args, "vanilla", ROOT_DIR / ".worktrees" / "vanilla" / "test", profile)

    assert env["POOL_HOST"] == module.DEFAULT_SUBMIT_REPLAY_GUEST_POOL_HOST
    assert env["POOL_PORT"] == str(module.DEFAULT_SUBMIT_REPLAY_PORT)
    assert env["POOL_DIFF"] == str(replay_difficulty)
    assert env["FALLBACK_POOL_HOST"] == module.DEFAULT_SUBMIT_REPLAY_GUEST_POOL_HOST
    assert env["FALLBACK_POOL_DIFF"] == str(replay_difficulty)
    assert env["VIRTUAL_ASIC_MODE"] == "cpu"
    assert env["RESET_PERSISTED_STATE"] == "1"
    assert env["STRATUM_REPLAY_PORT"] == str(module.DEFAULT_SUBMIT_REPLAY_PORT)
    assert env["STRATUM_REPLAY_DIFFICULTY"] == str(replay_difficulty)
    assert env["STRATUM_REPLAY_EXTRANONCE1"] == module.DEFAULT_SUBMIT_REPLAY_EXTRANONCE1
    assert env["STRATUM_REPLAY_EXTRANONCE2_SIZE"] == str(module.DEFAULT_SUBMIT_REPLAY_EXTRANONCE2_SIZE)


def test_virtualaxe_run_parser_rejects_tap_network_mode():
    module = load_virtualaxe_module()

    parser = module.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--network-mode", "tap"])


def test_virtualaxe_verify_live_parser_is_not_public_surface():
    module = load_virtualaxe_module()

    parser = module.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["verify-live"])
