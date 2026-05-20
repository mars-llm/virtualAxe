import argparse
import asyncio
import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest


pytest.importorskip("rich.console")
pytest.importorskip("textual")

from rich.console import Console


ROOT_DIR = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT_DIR / "scripts" / "virtualaxe_dashboard.py"


def load_module():
    spec = importlib.util.spec_from_file_location("virtualaxe_dashboard_test_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def render_to_text(renderable) -> str:
    capture = io.StringIO()
    console = Console(file=capture, width=140, force_terminal=False, color_system=None)
    console.print(renderable)
    return capture.getvalue()


def make_summary(run_id: str, *, status: str, release_gate_status: str, blocking_failures: list[str], mode: str = "smoke") -> dict:
    return {
        "runId": run_id,
        "mode": mode,
        "status": status,
        "profiles": [
            {
                "profile": "gamma",
                "releaseGateStatus": release_gate_status,
                "blockingFailures": blocking_failures,
                "phases": [
                    {"slug": "public", "phaseStatus": "PASSED"},
                    {"slug": "bitronics", "phaseStatus": "PASSED" if not blocking_failures else "FAILED"},
                    {"slug": "nerdminers", "phaseStatus": "PASSED"},
                ],
            }
        ],
    }


def make_snapshot(module):
    return module.DashboardSnapshot(
        session_id="FD1B4969",
        profile_name="Bitaxe Gamma",
        board_version="virtual-gamma",
        firmware_version="test-firmware",
        axeos_version="test-axeos",
        web_url="http://127.0.0.1:18080/",
        api_online=True,
        runtime_label="RUNNING",
        runtime_color="#4caf50",
        runtime_mode="native",
        primary_pool="public-pool.io",
        fallback_pool="pool.bitronics.store",
        pool_state="Connected",
        accepted_shares="1",
        rejected_shares="0",
        best_diff="512",
        pool_difficulty="1",
        uptime="00m 10s",
        ipv4="127.0.0.1",
        temp="virtual",
        fan_rpm="virtual",
        power="not exposed",
        voltage="not exposed",
        workers=[
            module.WorkerCardState(
                asic_nr=0,
                model="BM1370",
                lane_offset=0,
                lane_stride=1,
                jobs_assigned=2,
                state="ACTIVE",
                last_event="share attribution available",
            )
        ],
        build_state=("READY", "Patched firmware image present"),
        patch_state=("APPLIED", "Patched worktree ready"),
        boot_state=("BOOTED", "Firmware booted and API reachable"),
        api_state=("ONLINE", "WebUI reachable"),
        release_gate_state=("PASS", "Run demo passed 1m ago in smoke mode. Public + Bitronics + Nerdminers accepted."),
        persistence_state=("READY", "State file present"),
        test_ci_state=("PASS", "Upstream test-ci QEMU proof passed"),
        action_summary="Mining nominally. Accepted shares visible in the current session.",
        events=[
            module.LogEvent(
                id=1,
                source=module.POOL_SOURCE,
                message="Connected to public-pool.io",
                severity="info",
                timestamp=time.time(),
            )
        ],
    )


def test_event_matches_filter_routes_pool_and_system_events():
    module = load_module()

    assert module.event_matches_filter(module.POOL_SOURCE, module.FILTER_POOL) is True
    assert module.event_matches_filter(module.SHARE_SOURCE, module.FILTER_POOL) is True
    assert module.event_matches_filter(module.API_SOURCE, module.FILTER_POOL) is False
    assert module.event_matches_filter(module.SYSTEM_SOURCE, module.FILTER_SYS) is True
    assert module.event_matches_filter(module.POOL_SOURCE, module.FILTER_SYS) is False


def test_dashboard_source_labels_are_source_specific():
    module = load_module()

    assert module.source_display_name("bitaxe") == "Bitaxe"
    assert module.source_display_name("vanilla") == "Bitaxe"
    assert module.source_display_name("nerdnos") == "NerdNos"
    assert module.source_profile_name("bitaxe", {"id": "gamma", "deviceModel": "Gamma"}) == "Bitaxe Gamma"
    assert module.source_profile_name("nerdnos", {"id": "gamma", "deviceModel": "Gamma"}) == "NerdNos Gamma"
    assert module.source_identity_label("bitaxe") == "IDENT_VIRTUAL_BITAXE"
    assert module.source_identity_label("vanilla") == "IDENT_VIRTUAL_BITAXE"
    assert module.source_identity_label("nerdnos") == "IDENT_VIRTUAL_NERDNOS"


def test_latest_release_summary_prefers_newest_run(tmp_path):
    module = load_module()
    release_root = tmp_path / "release-matrix"
    first = release_root / "20260423-010101"
    second = release_root / "20260423-020202"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "summary.json").write_text('{"runId":"20260423-010101","status":"failed","profiles":[]}', encoding="utf-8")
    (second / "summary.json").write_text('{"runId":"20260423-020202","status":"passed","profiles":[]}', encoding="utf-8")
    os.utime(first / "summary.json", (time.time() - 3600, time.time() - 3600))

    path, summary = module.latest_release_summary(release_root)

    assert path == second / "summary.json"
    assert summary["runId"] == "20260423-020202"


def test_dashboard_state_uses_selected_source_for_profile_label(tmp_path, monkeypatch):
    module = load_module()
    args = argparse.Namespace(
        source="nerdnos",
        profile="gamma",
        http_port=18080,
        out_dir=str(tmp_path / "out" / "nerdnos" / "gamma"),
        state_dir=str(tmp_path / "state" / "nerdnos" / "gamma"),
    )
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    Path(args.state_dir).mkdir(parents=True, exist_ok=True)
    profile = {
        "id": "gamma",
        "displayName": "Bitaxe Gamma",
        "deviceModel": "Gamma",
        "boardVersion": "virtual-gamma",
        "asicModel": "BM1370",
        "asicCount": 1,
    }
    event_buffer = module.EventBuffer(Path(args.out_dir))
    runner = module.ActionRunner({"OUT_DIR": args.out_dir}, args, event_buffer)

    monkeypatch.setattr(module, "fetch_json", lambda *_args, **_kwargs: {"boardVersion": "virtual-gamma"})
    monkeypatch.setattr(module, "runtime_is_active", lambda *_args, **_kwargs: "container")

    snapshot = module.DashboardStateAdapter(args, {"QEMU_CONTAINER_NAME": "virtualaxe-qemu-nerdnos-gamma"}, profile, event_buffer).refresh(
        runner,
        "SESSION1",
    )

    assert snapshot.profile_name == "NerdNos Gamma"


def test_infer_release_gate_state_handles_missing_and_failed_runs(tmp_path):
    module = load_module()

    missing_state = module.infer_release_gate_state(None, None)
    assert missing_state[0] == "NOT_RUN"
    assert "pool smoke" in missing_state[1]

    failed_path = tmp_path / "summary.json"
    failed_path.write_text(json_text := '{"runId":"20260423-030303","status":"failed","profiles":[{"profile":"gamma","releaseGateStatus":"FAILED","blockingFailures":["02-secondary"],"phases":[{"slug":"nerdminers","phaseStatus":"FAILED"}]}]}', encoding="utf-8")
    failed_summary = module.load_json(failed_path)
    failed_state = module.infer_release_gate_state(failed_path, failed_summary)

    assert failed_state[0] == "FAIL"
    assert "02-secondary" in failed_state[1]
    assert "20260423-030303" in failed_state[1]


def test_infer_release_gate_state_pass_mentions_stale_run_and_mode(tmp_path):
    module = load_module()
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(make_summary("20260423-040404", status="passed", release_gate_status="PASSED", blocking_failures=[], mode="qualification")), encoding="utf-8")
    os.utime(summary_path, (time.time() - 180, time.time() - 180))

    state = module.infer_release_gate_state(summary_path, module.load_json(summary_path))

    assert state[0] == "PASS"
    assert "20260423-040404" in state[1]
    assert "qualification mode" in state[1]
    assert "Nerdminers accepted" in state[1]
    assert "ago" in state[1]


def test_dashboard_mount_renders_release_gate_and_handles_controls(tmp_path, monkeypatch):
    module = load_module()
    snapshot = make_snapshot(module)
    opened_urls: list[str] = []

    monkeypatch.setattr(module, "load_profile", lambda _profile: {"id": "gamma", "displayName": "Bitaxe Gamma", "boardVersion": "virtual-gamma", "asicModel": "BM1370", "asicCount": 1})
    monkeypatch.setattr(module, "runtime_is_active", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(module.DashboardStateAdapter, "refresh", lambda self, runner, session_id: snapshot)
    monkeypatch.setattr(module.webbrowser, "open", lambda url: opened_urls.append(url) or True)

    args = argparse.Namespace(
        source="vanilla",
        profile="gamma",
        network_mode="nat",
        http_port=18080,
        out_dir=str(tmp_path / "out"),
        state_dir=str(tmp_path / "state"),
        auto_start=False,
    )
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    Path(args.state_dir).mkdir(parents=True, exist_ok=True)

    app = module.BitaxeDashboardApp(args, {"HTTP_PORT": "18080", "OUT_DIR": args.out_dir, "STATE_DIR": args.state_dir})
    started_actions: list[str] = []
    quit_actions: list[str] = []
    app.runner.start = lambda label, fn: started_actions.append(label) or True
    app.action_quit = lambda: quit_actions.append("quit")

    async def exercise():
        async with app.run_test() as pilot:
            await pilot.pause()
            health_panel = app.query_one("#health-card", module.Static).content
            health_text = render_to_text(health_panel)
            assert "Pool Smoke" in health_text
            assert "Public + Bitronics + Nerdminers" in health_text
            assert "accepted." in health_text

            header_text = render_to_text(app.query_one("#header-left", module.Static).content)
            assert "virtualaxe-operator.sys" in header_text
            assert "FD1B4969" in header_text
            assert "BITAXE GAMMA" in header_text

            identity_text = render_to_text(app.query_one("#identity-card-view", module.Static).content)
            assert "IDENT_VIRTUAL_BITAXE" in identity_text
            assert "Board:" in identity_text
            assert "virtual-gamma" in identity_text
            assert "Firmware:" in identity_text
            assert "test-firmware" in identity_text
            assert "AxeOS:" in identity_text
            assert "test-axeos" in identity_text
            assert "WebUI:" in identity_text
            assert "http://127.0.0.1:18080/" in identity_text
            assert "OPEN WEBUI" in str(app.query_one("#show-web-url", module.Button).label)

            mining_text = render_to_text(app.query_one("#mining-card", module.Static).content)
            assert "MINING_CONFIG" in mining_text
            assert "public-pool.io" in mining_text
            assert "pool.bitronics.store" in mining_text

            asic_text = render_to_text(app.query_one("#asic-card-list", module.Static).content)
            assert "ASIC_LANE_REPORTS" in asic_text
            assert "ASIC_00" in asic_text
            assert "ACTIVE" in asic_text
            assert "BM1370" in asic_text
            assert "share attribution available" in asic_text

            stats_text = render_to_text(app.query_one("#stats-card", module.Static).content)
            for expected in (
                "RUNTIME STATS / SENSORS",
                "Shares (Acc/Rej)",
                "1 / 0",
                "Best Diff",
                "512",
                "Pool Diff",
                "1",
                "Connected",
                "127.0.0.1",
                "virtual",
                "not exposed",
            ):
                assert expected in stats_text

            command_labels = " ".join(str(button.label) for button in app.query(".command-button"))
            assert "O WEBUI" in command_labels
            assert "Q QUIT" in command_labels

            await pilot.press("2")
            assert app.current_filter == module.FILTER_POOL
            await pilot.press("3")
            assert app.current_filter == module.FILTER_SYS
            app.query_one("#cmd-filter", module.Button).press()
            await pilot.pause()
            assert app.current_filter == module.FILTER_ALL

            app.query_one("#cmd-start", module.Button).press()
            await pilot.pause()
            app.query_one("#cmd-stop", module.Button).press()
            await pilot.pause()
            app.query_one("#cmd-restart", module.Button).press()
            await pilot.pause()
            app.query_one("#cmd-rebuild", module.Button).press()
            await pilot.pause()
            app.query_one("#cmd-test-ci", module.Button).press()
            await pilot.pause()
            app.query_one("#cmd-verify", module.Button).press()
            await pilot.pause()
            assert started_actions == ["start", "stop", "restart", "rebuild", "test-ci", "verify"]

            app.query_one("#cmd-pause", module.Button).press()
            await pilot.pause()
            assert app.paused is True
            assert app.query_one("#paused-banner", module.Static).display is True
            app.query_one("#cmd-pause", module.Button).press()
            await pilot.pause()
            assert app.paused is False

            app.query_one("#show-web-url", module.Button).press()
            await pilot.pause()
            assert opened_urls == ["http://127.0.0.1:18080/"]
            assert not isinstance(app.screen_stack[-1], module.UrlOverlayScreen)

            app.query_one("#cmd-quit", module.Button).press()
            await pilot.pause()
            assert quit_actions == ["quit"]

    asyncio.run(exercise())


def test_dashboard_webui_action_shows_url_only_when_browser_launch_fails(tmp_path, monkeypatch):
    module = load_module()
    snapshot = make_snapshot(module)

    monkeypatch.setattr(module, "load_profile", lambda _profile: {"id": "gamma", "displayName": "Bitaxe Gamma", "boardVersion": "virtual-gamma", "asicModel": "BM1370", "asicCount": 1})
    monkeypatch.setattr(module, "runtime_is_active", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(module.DashboardStateAdapter, "refresh", lambda self, runner, session_id: snapshot)
    monkeypatch.setattr(module.webbrowser, "open", lambda _url: False)

    args = argparse.Namespace(
        source="bitaxe",
        profile="gamma",
        network_mode="nat",
        http_port=18080,
        out_dir=str(tmp_path / "out"),
        state_dir=str(tmp_path / "state"),
        auto_start=False,
    )
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    Path(args.state_dir).mkdir(parents=True, exist_ok=True)

    app = module.BitaxeDashboardApp(args, {"HTTP_PORT": "18080", "OUT_DIR": args.out_dir, "STATE_DIR": args.state_dir})

    async def exercise():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#show-web-url", module.Button).press()
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], module.UrlOverlayScreen)
            overlay = app.screen_stack[-1].query_one("#url-overlay", module.Static).content
            overlay_text = render_to_text(overlay)
            assert "BROWSER LAUNCH UNAVAILABLE" in overlay_text
            assert "http://127.0.0.1:18080/" in overlay_text

    asyncio.run(exercise())
