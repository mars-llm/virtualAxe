import gzip
import json
import os
import socket
import urllib.parse
import urllib.request

import pytest


def probe_virtualaxe_api(base_url: str, *, timeout: float = 2.0):
    info_url = f"{base_url}/api/system/info"
    try:
        payload = get_json(info_url, timeout=timeout)
    except Exception:
        return None

    if payload.get("isVirtual") is not True:
        return None

    return payload

def require_live_endpoint(url: str, *, timeout: float = 1.0, env_vars: tuple[str, ...] = ()) -> None:
    if any(os.environ.get(name) for name in env_vars):
        return

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host is None:
        pytest.skip(f"unable to resolve endpoint host for {url}")

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return
    except OSError as exc:
        pytest.skip(f"live endpoint {url} unavailable: {exc}")


def require_virtualaxe_api(base_url: str, *, timeout: float = 2.0, env_vars: tuple[str, ...] = ()) -> None:
    if any(os.environ.get(name) for name in env_vars):
        return

    info_url = f"{base_url}/api/system/info"
    payload = probe_virtualaxe_api(base_url, timeout=timeout)
    if payload is None:
        pytest.skip(f"{info_url} did not return a virtualAxe payload")


def get_json(url: str, timeout: float = 10.0):
    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip, identity"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", response.getcode())
        if status < 200 or status >= 300:
            raise RuntimeError(f"GET {url} returned HTTP {status}")
        body = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        return json.loads(body.decode("utf-8"))


def patch_json(url: str, payload: dict, timeout: float = 10.0):
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
