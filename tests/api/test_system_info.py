import os

from tests.api._http import get_json, require_virtualaxe_api


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:18080")
EXPECTED_DEVICE_MODEL = os.environ.get("EXPECTED_DEVICE_MODEL")
EXPECTED_ASIC_COUNT = os.environ.get("EXPECTED_ASIC_COUNT")
SOURCE_NAME = os.environ.get("SOURCE_NAME", "bitaxe")


def test_system_info_virtual_payload():
    require_virtualaxe_api(BASE_URL, env_vars=("BASE_URL",))
    payload = get_json(f"{BASE_URL}/api/system/info", timeout=10)

    assert "hostname" in payload
    assert "version" in payload
    assert payload["deviceModel"]
    assert payload["asicCount"] >= 1
    if EXPECTED_DEVICE_MODEL:
        assert payload["deviceModel"] == EXPECTED_DEVICE_MODEL
    if EXPECTED_ASIC_COUNT:
        assert payload["asicCount"] == int(EXPECTED_ASIC_COUNT)

    if SOURCE_NAME == "nerdnos":
        assert payload["deviceModel"] == "virtualAxe Gamma"
        assert payload["asicCount"] == 1
        assert payload["fanCount"] >= 1
        assert payload["ASICModel"] == "BM1370"
        assert payload["hostip"]
        assert payload["macAddr"]
        assert payload["wifiStatus"] == "Connected!"
        assert isinstance(payload["stratumURL"], str)
        assert isinstance(payload["stratumPort"], int)
        assert isinstance(payload["stratumUser"], str)
        assert isinstance(payload["fallbackStratumURL"], str)
        assert isinstance(payload["fallbackStratumPort"], int)
        assert isinstance(payload["fallbackStratumUser"], str)
        assert isinstance(payload["poolDifficulty"], (int, float))
        assert isinstance(payload["sharesAccepted"], int)
        assert isinstance(payload["sharesRejected"], int)
        stratum = payload["stratum"]
        assert isinstance(stratum, dict)
        assert isinstance(stratum.get("pools"), list)
        assert stratum["pools"]
        primary_pool = stratum["pools"][0]
        assert isinstance(primary_pool.get("connected"), bool)
        assert isinstance(primary_pool.get("poolDifficulty"), (int, float))
        assert isinstance(primary_pool.get("accepted"), int)
        assert isinstance(primary_pool.get("rejected"), int)
        return

    assert "axeOSVersion" in payload
    assert isinstance(payload["stratumSubscribeAgent"], str)
    assert isinstance(payload["fallbackStratumSubscribeAgent"], str)
    assert payload["hashDomains"] >= 1
    assert payload["isVirtual"] is True
    assert payload["networkType"] == "openeth"
    assert payload["virtualAsicMode"] == "cpu"
    assert isinstance(payload["virtualAsicWorkers"], list)
    if EXPECTED_ASIC_COUNT:
        assert len(payload["virtualAsicWorkers"]) == int(EXPECTED_ASIC_COUNT)
        for index, worker in enumerate(payload["virtualAsicWorkers"]):
            assert worker["asicNr"] == index
            assert worker["laneOffset"] == index
            assert worker["laneStride"] == int(EXPECTED_ASIC_COUNT)
