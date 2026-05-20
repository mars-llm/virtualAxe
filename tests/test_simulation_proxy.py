from __future__ import annotations

import json
import gzip
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

from scripts.simulation_actions import SimulationActions
from scripts.simulation_proxy import make_handler


BASE_INFO = {
    "temp": 0,
    "temp2": 0,
    "vrTemp": 0,
    "fanrpm": 0,
    "fan2rpm": 0,
    "fanspeed": 0,
    "power": 0,
    "errorPercentage": 0.1,
    "hashRate": 12000,
    "hashRate_1m": 11900,
    "sharesAccepted": 3,
    "sharesRejected": 0,
    "poolConnectionInfo": "Connected",
    "stratumURL": "public-pool.io",
}


class BackendHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/system/info":
            self._send_json(200, BASE_INFO)
        elif self.path == "/api/system/asic":
            self._send_json(200, {"frequency": 575, "voltage": 1200})
        elif self.path == "/api/system/statistics/dashboard":
            self._send_json(200, {"labels": [], "statistics": []})
        elif self.path == "/":
            self._send_html("<html><body><main>AxeOS</main></body></html>")
        elif self.path == "/asset.js":
            self._send_gzip("application/javascript", b"console.log('AxeOS asset');")
        else:
            self._send_json(404, {"error": "not found"})

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_gzip(self, content_type: str, raw_body: bytes) -> None:
        body = gzip.compress(raw_body)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GzipBackendHandler(BackendHandler):
    def do_GET(self) -> None:
        if self.path == "/api/system/info":
            self._send_gzip("application/json", json.dumps(BASE_INFO).encode("utf-8"))
        elif self.path == "/":
            self._send_gzip("text/html; charset=utf-8", b"<html><body><main>AxeOS</main></body></html>")
        else:
            super().do_GET()


class WebSocketBackendHandler(BackendHandler):
    def do_GET(self) -> None:
        if self.path == "/api/ws":
            self.connection.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"\r\n"
                b"backend-log"
            )
            return
        super().do_GET()


@contextmanager
def running_server(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def running_proxy(
    *,
    enabled: bool,
    actions: SimulationActions | None = None,
    backend_handler: type[BaseHTTPRequestHandler] = BackendHandler,
) -> Iterator[str]:
    with running_server(backend_handler) as backend_url:
        handler = make_handler(
            backend_url=backend_url,
            actions=actions or SimulationActions(),
            enabled=enabled,
            inject_overlay=enabled,
        )
        with running_server(handler) as proxy_url:
            yield proxy_url


def get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode("utf-8")


def get_response(url: str) -> tuple[dict[str, str], bytes]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return dict(response.headers.items()), response.read()


def get_websocket_upgrade(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    assert parsed.hostname is not None
    assert parsed.port is not None
    with socket.create_connection((parsed.hostname, parsed.port), timeout=5) as client:
        client.sendall(
            (
                "GET /api/ws HTTP/1.1\r\n"
                f"Host: {parsed.hostname}:{parsed.port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            ).encode("ascii")
        )
        client.settimeout(5)
        chunks: list[bytes] = []
        while b"backend-log" not in b"".join(chunks):
            chunks.append(client.recv(4096))
        return b"".join(chunks)


def test_sim_endpoints_return_404_when_proxy_disabled():
    with running_proxy(enabled=False) as base_url:
        try:
            urllib.request.urlopen(f"{base_url}/sim/capabilities", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected disabled /sim/capabilities to return 404")


def test_overlay_asset_is_only_served_when_enabled():
    with running_proxy(enabled=False) as base_url:
        try:
            urllib.request.urlopen(f"{base_url}/sim/overlay.js", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected disabled /sim/overlay.js to return 404")

    with running_proxy(enabled=True) as base_url:
        body = get_text(f"{base_url}/sim/overlay.js")
        assert "Simulation Actions" in body
        assert "Which virtual condition AxeOS should see through normal telemetry." in body
        assert "How strongly the selected condition changes presentation telemetry." in body
        assert "Manual runs until stopped or reset." in body


def test_capabilities_state_start_stop_and_reset_when_enabled():
    with running_proxy(enabled=True) as base_url:
        assert get_json(f"{base_url}/sim/capabilities") == {
            "enabled": True,
            "actions": ["overheat", "high_error_rate", "fan_failure"],
        }
        assert get_json(f"{base_url}/sim/state") == {"enabled": True, "active": []}

        status, started = post_json(
            f"{base_url}/sim/actions/start",
            {"type": "overheat", "severity": "high", "durationMs": 30000},
        )

        assert status == 200
        assert started["type"] == "overheat"
        assert started["status"] == "active"
        assert len(get_json(f"{base_url}/sim/state")["active"]) == 1

        status, stopped = post_json(f"{base_url}/sim/actions/stop", {"id": started["id"]})
        assert status == 200
        assert stopped == {"id": started["id"], "status": "stopped"}
        assert get_json(f"{base_url}/sim/state")["active"] == []

        post_json(f"{base_url}/sim/actions/start", {"type": "fan_failure", "severity": "medium"})
        post_json(f"{base_url}/sim/reset", {})
        assert get_json(f"{base_url}/sim/state")["active"] == []


def test_invalid_action_is_rejected():
    with running_proxy(enabled=True) as base_url:
        request = urllib.request.Request(
            f"{base_url}/sim/actions/start",
            data=b'{"type":"hashrate_drop","severity":"medium"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("expected invalid action to return 400")


def test_system_info_overlay_changes_existing_telemetry_without_metadata():
    actions = SimulationActions(id_factory=lambda: "act_test")
    actions.start_action({"type": "overheat", "severity": "high", "params": {"rampSeconds": 0}})

    with running_proxy(enabled=True, actions=actions) as base_url:
        payload = get_json(f"{base_url}/api/system/info")

    assert payload["temp"] > BASE_INFO["temp"]
    assert payload["temp2"] == BASE_INFO["temp2"]
    assert payload["power"] > 0
    assert payload["hashRate"] == BASE_INFO["hashRate"]
    assert payload["sharesAccepted"] == BASE_INFO["sharesAccepted"]
    assert payload["stratumURL"] == BASE_INFO["stratumURL"]
    forbidden = {"simulationMode", "activeSimulation", "simulated", "simulationActions", "simState"}
    assert forbidden.isdisjoint(payload)


def test_non_system_info_api_responses_are_forwarded_without_overlay():
    actions = SimulationActions(id_factory=lambda: "act_test")
    actions.start_action({"type": "overheat", "severity": "high"})

    with running_proxy(enabled=True, actions=actions) as base_url:
        assert get_json(f"{base_url}/api/system/asic") == {"frequency": 575, "voltage": 1200}
        assert get_json(f"{base_url}/api/system/statistics/dashboard") == {"labels": [], "statistics": []}


def test_html_injection_only_happens_when_enabled():
    with running_proxy(enabled=False) as base_url:
        html = get_text(f"{base_url}/")
        assert "/sim/overlay.js" not in html

    with running_proxy(enabled=True) as base_url:
        html = get_text(f"{base_url}/")
        assert '<script src="/sim/overlay.js"></script>' in html


def test_gzipped_html_is_decoded_before_overlay_injection():
    with running_proxy(enabled=True, backend_handler=GzipBackendHandler) as base_url:
        headers, body = get_response(f"{base_url}/")

    assert "Content-Encoding" not in headers
    html = body.decode("utf-8")
    assert "<main>AxeOS</main>" in html
    assert '<script src="/sim/overlay.js"></script>' in html


def test_gzipped_pass_through_responses_keep_content_encoding():
    with running_proxy(enabled=True, backend_handler=GzipBackendHandler) as base_url:
        headers, body = get_response(f"{base_url}/asset.js")

    assert headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(body) == b"console.log('AxeOS asset');"


def test_gzipped_system_info_is_decoded_before_telemetry_overlay():
    actions = SimulationActions(id_factory=lambda: "act_test")
    actions.start_action({"type": "overheat", "severity": "high", "params": {"rampSeconds": 0}})

    with running_proxy(enabled=True, actions=actions, backend_handler=GzipBackendHandler) as base_url:
        headers, body = get_response(f"{base_url}/api/system/info")

    assert "Content-Encoding" not in headers
    payload = json.loads(body.decode("utf-8"))
    assert payload["temp"] > BASE_INFO["temp"]
    assert payload["hashRate"] == BASE_INFO["hashRate"]


def test_websocket_upgrade_is_tunneled_to_backend():
    with running_proxy(enabled=True, backend_handler=WebSocketBackendHandler) as base_url:
        response = get_websocket_upgrade(base_url)

    assert b"101 Switching Protocols" in response
    assert b"backend-log" in response
