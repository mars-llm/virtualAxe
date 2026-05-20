#!/usr/bin/env python3
from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


ACTION_TYPES = ("overheat", "high_error_rate", "fan_failure")
SEVERITIES = ("low", "medium", "high", "critical")

OVERHEAT_TARGETS = {
    "low": 66.0,
    "medium": 76.0,
    "high": 88.0,
    "critical": 96.0,
}
ERROR_TARGETS = {
    "low": 2.0,
    "medium": 6.0,
    "high": 14.0,
    "critical": 28.0,
}
FAN_FACTORS = {
    "low": 0.65,
    "medium": 0.35,
    "high": 0.08,
    "critical": 0.0,
}
ERROR_COUNT_TARGETS = {
    "low": 25,
    "medium": 80,
    "high": 180,
    "critical": 420,
}
BASELINE_TEMP_C = 46.0
BASELINE_VR_TEMP_C = 43.0
BASELINE_FAN_RPM = 3200.0
BASELINE_FAN_SPEED = 45.0
BASELINE_EFFICIENCY_J_TH = 25.0
MIN_PRESENTATION_POWER_W = 0.000001
DEFAULT_OVERHEAT_RAMP_SECONDS = 45.0


class SimulationValidationError(ValueError):
    pass


@dataclass
class SimulationAction:
    id: str
    type: str
    severity: str
    started_at_ms: int
    duration_ms: int | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def active_at(self, now_ms: int) -> bool:
        if self.duration_ms is None:
            return True
        return now_ms < self.started_at_ms + self.duration_ms

    def public_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "severity": self.severity,
            "startedAt": self.started_at_ms,
        }
        if self.duration_ms is not None:
            payload["durationMs"] = self.duration_ms
        return payload


class SimulationActions:
    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.clock = clock or time.time
        self.id_factory = id_factory or (lambda: f"act_{uuid.uuid4().hex[:12]}")
        self.actions: dict[str, SimulationAction] = {}

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    def capabilities(self) -> dict[str, Any]:
        return {"enabled": True, "actions": list(ACTION_TYPES)}

    def state(self) -> dict[str, Any]:
        self._purge_expired()
        return {"enabled": True, "active": [action.public_state() for action in self.actions.values()]}

    def start_action(self, payload: dict[str, Any]) -> dict[str, str]:
        action_type = self._action_type(payload)
        severity = self._severity(payload)
        duration_ms = self._duration_ms(payload)
        params = self._params(payload)
        action = SimulationAction(
            id=self.id_factory(),
            type=action_type,
            severity=severity,
            started_at_ms=self._now_ms(),
            duration_ms=duration_ms,
            params=params,
        )
        self.actions[action.id] = action
        return {"id": action.id, "type": action.type, "status": "active"}

    def stop_action(self, action_id: str) -> bool:
        self._purge_expired()
        return self.actions.pop(action_id, None) is not None

    def reset(self) -> None:
        self.actions.clear()

    def apply_telemetry_overlay(self, telemetry: dict[str, Any]) -> dict[str, Any]:
        self._purge_expired()
        overlaid = copy.deepcopy(telemetry)
        self._apply_presentation_baseline(overlaid)
        for action in self.actions.values():
            if action.type == "overheat":
                self._apply_overheat(overlaid, action)
            elif action.type == "high_error_rate":
                self._apply_high_error_rate(overlaid, action)
            elif action.type == "fan_failure":
                self._apply_fan_failure(overlaid, action)
        return overlaid

    def _purge_expired(self) -> None:
        now_ms = self._now_ms()
        expired = [action_id for action_id, action in self.actions.items() if not action.active_at(now_ms)]
        for action_id in expired:
            self.actions.pop(action_id, None)

    def _action_type(self, payload: dict[str, Any]) -> str:
        action_type = str(payload.get("type", "")).strip()
        if action_type not in ACTION_TYPES:
            raise SimulationValidationError(f"unsupported simulation action type: {action_type or '<missing>'}")
        return action_type

    def _severity(self, payload: dict[str, Any]) -> str:
        severity = str(payload.get("severity", "medium")).strip().lower()
        if severity not in SEVERITIES:
            raise SimulationValidationError(f"unsupported simulation severity: {severity or '<missing>'}")
        return severity

    def _duration_ms(self, payload: dict[str, Any]) -> int | None:
        raw = payload.get("durationMs")
        if raw is None:
            return None
        if isinstance(raw, bool):
            raise SimulationValidationError("durationMs must be a positive integer")
        try:
            duration_ms = int(raw)
        except (TypeError, ValueError) as exc:
            raise SimulationValidationError("durationMs must be a positive integer") from exc
        if duration_ms <= 0:
            raise SimulationValidationError("durationMs must be a positive integer")
        return duration_ms

    def _params(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = payload.get("params", {})
        if params is None:
            return {}
        if not isinstance(params, dict):
            raise SimulationValidationError("params must be an object")
        return dict(params)

    def _elapsed_progress(self, action: SimulationAction, default_ramp_seconds: float = 0.0) -> float:
        ramp_seconds = self._float_param(action.params, "rampSeconds")
        if ramp_seconds is None:
            ramp_seconds = default_ramp_seconds
        if ramp_seconds is None or ramp_seconds <= 0:
            return 1.0
        elapsed_seconds = max(0.0, (self._now_ms() - action.started_at_ms) / 1000.0)
        return min(1.0, elapsed_seconds / ramp_seconds)

    def _apply_presentation_baseline(self, telemetry: dict[str, Any]) -> None:
        self._raise_number(telemetry, "temp", BASELINE_TEMP_C, 1.0)
        self._raise_number(telemetry, "vrTemp", BASELINE_VR_TEMP_C, 1.0)
        self._raise_number(telemetry, "fanrpm", BASELINE_FAN_RPM, 1.0)
        self._raise_number(telemetry, "fanspeed", BASELINE_FAN_SPEED, 1.0)
        self._apply_power_baseline(telemetry)

    def _apply_power_baseline(self, telemetry: dict[str, Any]) -> None:
        power = self._number(telemetry.get("power"))
        hashrate_gh = self._first_positive_number(telemetry, ("hashRate", "hashRate_1m", "hashRate_10m", "hashRate_1h"))
        if power is None or hashrate_gh is None or power > 0 or hashrate_gh <= 0:
            return
        target_power = max((hashrate_gh / 1000.0) * BASELINE_EFFICIENCY_J_TH, MIN_PRESENTATION_POWER_W)
        telemetry["power"] = round(target_power, 6)

    def _apply_overheat(self, telemetry: dict[str, Any], action: SimulationAction) -> None:
        target = self._float_param(action.params, "targetTemp") or OVERHEAT_TARGETS[action.severity]
        progress = self._elapsed_progress(action, DEFAULT_OVERHEAT_RAMP_SECONDS)
        self._raise_number(telemetry, "temp", target, progress)
        self._raise_number(telemetry, "vrTemp", target + 4.0, progress)

    def _apply_high_error_rate(self, telemetry: dict[str, Any], action: SimulationAction) -> None:
        target = ERROR_TARGETS[action.severity]
        current = self._number(telemetry.get("errorPercentage"))
        if current is not None:
            telemetry["errorPercentage"] = self._format_like(telemetry["errorPercentage"], max(current, target))
        self._raise_hashrate_monitor_error_count(telemetry, ERROR_COUNT_TARGETS[action.severity])

    def _apply_fan_failure(self, telemetry: dict[str, Any], action: SimulationAction) -> None:
        factor = FAN_FACTORS[action.severity]
        current = self._number(telemetry.get("fanrpm"))
        if current is not None:
            telemetry["fanrpm"] = self._format_like(telemetry["fanrpm"], max(0.0, current * factor))

    def _raise_hashrate_monitor_error_count(self, telemetry: dict[str, Any], target: int) -> None:
        monitor = telemetry.get("hashrateMonitor")
        if not isinstance(monitor, dict):
            return
        asics = monitor.get("asics")
        if not isinstance(asics, list):
            return
        for asic in asics:
            if not isinstance(asic, dict):
                continue
            current = self._number(asic.get("errorCount"))
            if current is None or target <= current:
                continue
            asic["errorCount"] = self._format_like(asic["errorCount"], float(target))

    def _raise_number(self, telemetry: dict[str, Any], key: str, target: float, progress: float) -> None:
        current = self._number(telemetry.get(key))
        if current is None:
            return
        if target <= current:
            return
        value = current + ((target - current) * progress)
        telemetry[key] = self._format_like(telemetry[key], value)

    def _number(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _first_positive_number(self, telemetry: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            value = self._number(telemetry.get(key))
            if value is not None and value > 0:
                return value
        return None

    def _float_param(self, params: dict[str, Any], key: str) -> float | None:
        if key not in params:
            return None
        return self._number(params.get(key))

    def _format_like(self, original: Any, value: float) -> int | float | str:
        if isinstance(original, int) and not isinstance(original, bool):
            return int(round(value))
        if isinstance(original, float):
            return round(value, 2)
        if isinstance(original, str):
            return str(round(value, 2))
        return round(value, 2)
