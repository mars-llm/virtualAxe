import copy

import pytest

from scripts.simulation_actions import SimulationActions, SimulationValidationError


BASE_TELEMETRY = {
    "temp": 0.0,
    "temp2": 0.0,
    "vrTemp": 0.0,
    "fanrpm": 0,
    "fan2rpm": 0,
    "fanspeed": 0,
    "power": 0,
    "errorPercentage": 0.25,
    "hashRate": 12345.6,
    "hashRate_1m": 12000.0,
    "sharesAccepted": 4,
    "sharesRejected": 1,
    "poolConnectionInfo": "Connected",
    "stratumURL": "public-pool.io",
    "virtualAsicWorkers": [{"asicNr": 0, "jobsAssigned": 12}],
    "hashrateMonitor": {
        "asics": [
            {
                "total": 0.000012,
                "domains": [0.000012],
                "errorCount": 7,
            }
        ]
    },
}


def make_engine() -> SimulationActions:
    now = [1000.0]

    def clock() -> float:
        return now[0]

    engine = SimulationActions(clock=clock, id_factory=lambda: f"act_{len(engine.actions) + 1}")  # type: ignore[name-defined]
    engine.test_clock = now  # type: ignore[attr-defined]
    return engine


def test_rejects_unknown_action_type():
    engine = make_engine()

    with pytest.raises(SimulationValidationError):
        engine.start_action({"type": "hashrate_drop", "severity": "medium"})


def test_rejects_invalid_severity():
    engine = make_engine()

    with pytest.raises(SimulationValidationError):
        engine.start_action({"type": "overheat", "severity": "severe"})


def test_rejects_invalid_duration():
    engine = make_engine()

    with pytest.raises(SimulationValidationError):
        engine.start_action({"type": "overheat", "severity": "low", "durationMs": 0})


def test_start_stop_and_reset_action_lifecycle():
    engine = make_engine()

    started = engine.start_action({"type": "overheat", "severity": "high", "durationMs": 120000})

    assert started["id"] == "act_1"
    assert started["type"] == "overheat"
    assert started["status"] == "active"
    assert [action["id"] for action in engine.state()["active"]] == ["act_1"]

    assert engine.stop_action("act_1") is True
    assert engine.state()["active"] == []

    engine.start_action({"type": "fan_failure", "severity": "medium"})
    engine.reset()
    assert engine.state()["active"] == []


def test_duration_expiry_removes_action():
    engine = make_engine()
    engine.start_action({"type": "overheat", "severity": "medium", "durationMs": 1000})

    engine.test_clock[0] += 1.5  # type: ignore[attr-defined]

    assert engine.state()["active"] == []


def test_capabilities_are_limited_to_first_supported_actions():
    engine = make_engine()

    assert engine.capabilities() == {
        "enabled": True,
        "actions": ["overheat", "high_error_rate", "fan_failure"],
    }


def test_no_active_actions_apply_only_presentation_baseline():
    engine = make_engine()
    telemetry = copy.deepcopy(BASE_TELEMETRY)

    overlaid = engine.apply_telemetry_overlay(telemetry)

    assert overlaid["temp"] == 46.0
    assert overlaid["temp2"] == 0.0
    assert overlaid["vrTemp"] == 43.0
    assert overlaid["fanrpm"] == 3200
    assert overlaid["fan2rpm"] == 0
    assert overlaid["fanspeed"] == 45
    assert overlaid["power"] > 0
    for key in ("hashRate", "hashRate_1m", "sharesAccepted", "sharesRejected", "poolConnectionInfo", "stratumURL"):
        assert overlaid[key] == telemetry[key]


def test_presentation_power_can_use_rolling_hashrate_when_instant_hashrate_is_zero():
    engine = make_engine()
    telemetry = copy.deepcopy(BASE_TELEMETRY)
    telemetry["hashRate"] = 0
    telemetry["hashRate_1m"] = 0.00008

    overlaid = engine.apply_telemetry_overlay(telemetry)

    assert overlaid["power"] > 0
    assert overlaid["hashRate"] == 0
    assert overlaid["hashRate_1m"] == 0.00008


def test_overheat_raises_single_gamma_temperature_and_vr_temperature():
    engine = make_engine()
    engine.start_action({"type": "overheat", "severity": "high", "params": {"targetTemp": 88, "rampSeconds": 0}})

    overlaid = engine.apply_telemetry_overlay(BASE_TELEMETRY)

    assert overlaid["temp"] > BASE_TELEMETRY["temp"]
    assert overlaid["temp2"] == BASE_TELEMETRY["temp2"]
    assert overlaid["vrTemp"] > BASE_TELEMETRY["vrTemp"]


def test_overheat_uses_default_ramp_to_avoid_chart_spike():
    engine = make_engine()
    engine.start_action({"type": "overheat", "severity": "high", "params": {"targetTemp": 88}})

    just_started = engine.apply_telemetry_overlay(BASE_TELEMETRY)
    engine.test_clock[0] += 15  # type: ignore[attr-defined]
    ramping = engine.apply_telemetry_overlay(BASE_TELEMETRY)

    assert just_started["temp"] == 46.0
    assert 46.0 < ramping["temp"] < 88.0


def test_high_error_rate_raises_existing_error_telemetry():
    engine = make_engine()
    engine.start_action({"type": "high_error_rate", "severity": "critical"})

    overlaid = engine.apply_telemetry_overlay(BASE_TELEMETRY)

    assert overlaid["errorPercentage"] > BASE_TELEMETRY["errorPercentage"]
    assert overlaid["hashrateMonitor"]["asics"][0]["errorCount"] > BASE_TELEMETRY["hashrateMonitor"]["asics"][0]["errorCount"]


def test_fan_failure_lowers_single_gamma_fan_rpm():
    engine = make_engine()
    engine.start_action({"type": "fan_failure", "severity": "high"})

    overlaid = engine.apply_telemetry_overlay(BASE_TELEMETRY)

    assert 0 < overlaid["fanrpm"] < 3200
    assert overlaid["fan2rpm"] == BASE_TELEMETRY["fan2rpm"]
    assert overlaid["fanspeed"] == 45


def test_actions_do_not_change_hashrate_shares_pool_or_worker_fields():
    engine = make_engine()
    engine.start_action({"type": "overheat", "severity": "critical", "params": {"rampSeconds": 0}})
    engine.start_action({"type": "high_error_rate", "severity": "critical"})
    engine.start_action({"type": "fan_failure", "severity": "critical"})

    overlaid = engine.apply_telemetry_overlay(BASE_TELEMETRY)

    for key in (
        "hashRate",
        "hashRate_1m",
        "sharesAccepted",
        "sharesRejected",
        "poolConnectionInfo",
        "stratumURL",
        "virtualAsicWorkers",
    ):
        assert overlaid[key] == BASE_TELEMETRY[key]
    assert overlaid["hashrateMonitor"]["asics"][0]["total"] == BASE_TELEMETRY["hashrateMonitor"]["asics"][0]["total"]
    assert overlaid["hashrateMonitor"]["asics"][0]["domains"] == BASE_TELEMETRY["hashrateMonitor"]["asics"][0]["domains"]


def test_overlay_does_not_add_simulation_metadata():
    engine = make_engine()
    engine.start_action({"type": "overheat", "severity": "medium", "params": {"rampSeconds": 0}})

    overlaid = engine.apply_telemetry_overlay(BASE_TELEMETRY)

    forbidden = {"simulationMode", "activeSimulation", "simulated", "simulationActions", "simState"}
    assert forbidden.isdisjoint(overlaid)
