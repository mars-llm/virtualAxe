import os
import time

import pytest

from tests.api._http import get_json, patch_json, require_virtualaxe_api


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:18080")
SOURCE_NAME = os.environ.get("SOURCE_NAME", "bitaxe")


def require_explicit_settings_api():
    if not os.environ.get("BASE_URL"):
        pytest.skip("set BASE_URL to run mutating API settings checks")
    require_virtualaxe_api(BASE_URL)


def require_bitaxe_settings_api():
    if SOURCE_NAME != "bitaxe":
        pytest.skip(f"{SOURCE_NAME} exposes a source-specific settings API")


def wait_for_settings(expected: dict[str, object], *, timeout: float = 10.0):
    deadline = time.time() + timeout
    last_payload = None
    while time.time() < deadline:
        last_payload = get_json(f"{BASE_URL}/api/system/info", timeout=timeout)
        if all(setting_matches(last_payload.get(key), value) for key, value in expected.items()):
            return last_payload
        time.sleep(0.25)
    raise AssertionError(f"expected settings {expected}, last payload was {last_payload}")


def setting_matches(actual: object, expected: object) -> bool:
    if isinstance(expected, float):
        try:
            actual_float = float(actual)
        except (TypeError, ValueError):
            return False
        return abs(actual_float - expected) <= max(1e-6, abs(expected) * 1e-5)
    return actual == expected


def test_system_settings_patch_roundtrip():
    require_explicit_settings_api()
    require_bitaxe_settings_api()

    system_info = get_json(f"{BASE_URL}/api/system/info", timeout=10)
    original_settings = {
        "fallbackStratumUser": system_info["fallbackStratumUser"],
        "stratumSubscribeAgent": system_info["stratumSubscribeAgent"],
        "statsFrequency": system_info["statsFrequency"],
    }
    suffix = ".pytest-check"
    fallback_user = original_settings["fallbackStratumUser"]
    if fallback_user.endswith(suffix):
        fallback_user = f"{fallback_user}.restored"

    mutated_settings = {
        "fallbackStratumUser": f"{fallback_user}{suffix}",
        "stratumSubscribeAgent": "NerdMinerV2/pytest-check",
        "statsFrequency": 31 if original_settings["statsFrequency"] != 31 else 32,
    }

    try:
        patch_json(f"{BASE_URL}/api/system", mutated_settings, timeout=10)
        wait_for_settings(mutated_settings)
    finally:
        patch_json(f"{BASE_URL}/api/system", original_settings, timeout=10)
        wait_for_settings(original_settings)


def test_system_settings_patch_accepts_large_form_payload():
    require_explicit_settings_api()
    require_bitaxe_settings_api()

    system_info = get_json(f"{BASE_URL}/api/system/info", timeout=10)
    original_settings = {
        "display": system_info["display"],
        "rotation": system_info["rotation"],
        "invertscreen": system_info["invertscreen"],
        "displayTimeout": system_info["displayTimeout"],
        "coreVoltage": system_info["coreVoltage"],
        "frequency": system_info["frequency"],
        "autofanspeed": system_info["autofanspeed"],
        "minFanSpeed": system_info["minFanSpeed"],
        "manualFanSpeed": system_info["manualFanSpeed"],
        "temptarget": system_info["temptarget"],
        "overheat_mode": system_info["overheat_mode"],
        "statsFrequency": system_info["statsFrequency"],
    }
    mutated_settings = dict(original_settings)
    mutated_settings["frequency"] = 490 if system_info["frequency"] != 490 else 500

    try:
        patch_json(f"{BASE_URL}/api/system", mutated_settings, timeout=10)
        wait_for_settings({"frequency": mutated_settings["frequency"]})
    finally:
        patch_json(f"{BASE_URL}/api/system", original_settings, timeout=10)
        wait_for_settings({"frequency": original_settings["frequency"]})


def test_pool_settings_patch_keeps_system_info_responsive():
    require_explicit_settings_api()
    require_bitaxe_settings_api()

    system_info = get_json(f"{BASE_URL}/api/system/info", timeout=10)
    original_settings = {
        "stratumURL": system_info["stratumURL"],
        "stratumPort": system_info["stratumPort"],
        "stratumUser": system_info["stratumUser"],
        "stratumSubscribeAgent": system_info["stratumSubscribeAgent"],
        "stratumSuggestedDifficulty": system_info["stratumSuggestedDifficulty"],
        "fallbackStratumURL": system_info["fallbackStratumURL"],
        "fallbackStratumPort": system_info["fallbackStratumPort"],
        "fallbackStratumUser": system_info["fallbackStratumUser"],
        "fallbackStratumSubscribeAgent": system_info["fallbackStratumSubscribeAgent"],
        "fallbackStratumSuggestedDifficulty": system_info["fallbackStratumSuggestedDifficulty"],
    }
    mutated_settings = {
        "stratumURL": "pool.bitronics.store",
        "stratumPort": 3334,
        "stratumUser": "bc1qexample.bitronics-check",
        "stratumSubscribeAgent": "NerdMinerV2/pytest-check",
        "stratumSuggestedDifficulty": 0.0005,
        "fallbackStratumURL": "pool.bitronics.store",
        "fallbackStratumPort": 3334,
        "fallbackStratumUser": "bc1qexample.bitronics-check",
        "fallbackStratumSubscribeAgent": "NerdMinerV2/pytest-check",
        "fallbackStratumSuggestedDifficulty": 0.0005,
    }

    try:
        patch_json(f"{BASE_URL}/api/system", mutated_settings, timeout=10)
        payload = wait_for_settings(mutated_settings)
        assert payload["stratumURL"] == mutated_settings["stratumURL"]
        assert payload["fallbackStratumURL"] == mutated_settings["fallbackStratumURL"]
    finally:
        patch_json(f"{BASE_URL}/api/system", original_settings, timeout=10)
        wait_for_settings(original_settings)
