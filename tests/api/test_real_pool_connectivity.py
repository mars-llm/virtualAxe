import os
import socket
import time

import pytest

from tests.api._http import get_json, require_virtualaxe_api


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:18080")
POOL_HOST = os.environ.get("REAL_POOL_HOST", "example.com")
POOL_PORT = int(os.environ.get("REAL_POOL_PORT", "3333"))
SOURCE_NAME = os.environ.get("SOURCE_NAME", "bitaxe")


def test_real_pool_host_resolves_and_tcp_connects():
    if POOL_HOST == "example.com":
        pytest.skip("set REAL_POOL_HOST to run live real-pool connectivity checks")

    infos = socket.getaddrinfo(POOL_HOST, POOL_PORT, type=socket.SOCK_STREAM)
    assert infos

    for family, socktype, proto, _, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(5)
        try:
            sock.connect(sockaddr)
            return
        except OSError:
            continue
        finally:
            sock.close()

    raise AssertionError(f"unable to open TCP connection to {POOL_HOST}:{POOL_PORT}")


def test_real_pool_session_visible_in_virtualaxe_api():
    if POOL_HOST == "example.com":
        pytest.skip("set REAL_POOL_HOST to run live real-pool connectivity checks")

    require_virtualaxe_api(BASE_URL, env_vars=("BASE_URL",))

    deadline = time.time() + 90
    last_payload = None
    while time.time() < deadline:
        last_payload = get_json(f"{BASE_URL}/api/system/info", timeout=15)
        if SOURCE_NAME == "nerdnos":
            stratum = last_payload.get("stratum") or {}
            pools = stratum.get("pools") or []
            primary_pool = pools[0] if pools else {}
            api_pool_matches = (
                last_payload.get("stratumURL") == POOL_HOST
                and int(last_payload.get("stratumPort", 0) or 0) == POOL_PORT
            )
            nested_pool_matches = (
                primary_pool.get("url") == POOL_HOST
                and int(primary_pool.get("port", 0) or 0) == POOL_PORT
            )
            pool_difficulty = max(
                float(last_payload.get("poolDifficulty", 0) or 0),
                float(primary_pool.get("poolDifficulty", 0) or 0),
            )
            if (api_pool_matches or nested_pool_matches) and pool_difficulty > 0 and bool(last_payload.get("stratumUser")):
                return
        elif (
            last_payload.get("poolDifficulty", 0) > 0
            and last_payload.get("blockHeight", 0) > 0
            and bool(last_payload.get("poolConnectionInfo"))
            and bool(last_payload.get("stratumUser"))
        ):
            return
        time.sleep(1)

    if SOURCE_NAME == "nerdnos":
        raise AssertionError(
            "expected the NerdNos firmware API to show a live primary pool session with difficulty; "
            f"last payload was {last_payload}"
        )

    raise AssertionError(
        "expected the Bitaxe firmware API to show a live pool session with difficulty and block height; "
        f"last payload was {last_payload}"
    )
