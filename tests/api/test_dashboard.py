import os

import pytest

from tests.api._http import get_json, require_virtualaxe_api


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:18080")
SOURCE_NAME = os.environ.get("SOURCE_NAME", "bitaxe")


def test_dashboard_statistics_alias():
    require_virtualaxe_api(BASE_URL, env_vars=("BASE_URL",))
    if SOURCE_NAME != "bitaxe":
        pytest.skip(f"{SOURCE_NAME} does not expose the Bitaxe dashboard statistics alias")
    payload = get_json(f"{BASE_URL}/api/system/statistics/dashboard", timeout=10)

    assert "labels" in payload
    assert "statistics" in payload
    assert isinstance(payload["labels"], list)
    assert isinstance(payload["statistics"], list)
