#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import select
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:
    from scripts.simulation_actions import SimulationActions, SimulationValidationError
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from simulation_actions import SimulationActions, SimulationValidationError


DEFAULT_BACKEND_URL = "http://127.0.0.1:18080"
DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 18082
MAX_REQUEST_BYTES = 64 * 1024
SIM_METADATA_KEYS = {
    "simulationMode",
    "activeSimulation",
    "simulated",
    "simulationActions",
    "simState",
}

OVERLAY_JS = r"""
(() => {
  const ROOT_ID = "virtualaxe-sim-actions";
  if (document.getElementById(ROOT_ID)) return;

  const root = document.createElement("div");
  root.id = ROOT_ID;
  root.innerHTML = `
    <style>
      #${ROOT_ID} {
        position: fixed;
        right: 16px;
        bottom: 16px;
        z-index: 2147483647;
        font: 13px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #f7f7f7;
      }
      #${ROOT_ID} button,
      #${ROOT_ID} select {
        font: inherit;
      }
      #${ROOT_ID} .box {
        width: 300px;
        background: rgba(18, 22, 27, 0.96);
        border: 1px solid rgba(255, 255, 255, 0.18);
        border-radius: 8px;
        box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
        overflow: hidden;
      }
      #${ROOT_ID} .bar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 8px 10px;
        background: rgba(65, 86, 106, 0.40);
      }
      #${ROOT_ID} .body {
        padding: 10px;
      }
      #${ROOT_ID} label {
        display: flex;
        align-items: center;
        gap: 4px;
        color: #9ba8b5;
        font-size: 11px;
        margin-bottom: 3px;
      }
      #${ROOT_ID} .help {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 13px;
        height: 13px;
        border-radius: 50%;
        border: 1px solid rgba(255, 255, 255, 0.26);
        color: #dce6ef;
        font-size: 10px;
        cursor: help;
      }
      #${ROOT_ID}.collapsed .body {
        display: none;
      }
      #${ROOT_ID} .row {
        display: flex;
        gap: 6px;
        margin-top: 8px;
        flex-wrap: wrap;
      }
      #${ROOT_ID} button {
        border: 1px solid rgba(255, 255, 255, 0.16);
        border-radius: 5px;
        background: #223041;
        color: #fff;
        padding: 5px 8px;
        cursor: pointer;
      }
      #${ROOT_ID} button:hover {
        background: #304357;
      }
      #${ROOT_ID} button.primary {
        background: #2f6f58;
      }
      #${ROOT_ID} button.primary:hover {
        background: #39886c;
      }
      #${ROOT_ID} select {
        background: #111820;
        color: #fff;
        border: 1px solid rgba(255, 255, 255, 0.18);
        border-radius: 5px;
        padding: 4px 6px;
      }
      #${ROOT_ID} .list {
        margin-top: 10px;
        color: #d5dde8;
      }
      #${ROOT_ID} .item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 6px;
        padding: 5px 0;
        border-top: 1px solid rgba(255, 255, 255, 0.10);
      }
      #${ROOT_ID} .muted {
        color: #9ba8b5;
      }
    </style>
    <div class="box">
      <div class="bar">
        <strong>Simulation Actions</strong>
        <button type="button" data-toggle>Hide</button>
      </div>
      <div class="body">
        <div class="muted" data-status>No active actions.</div>
        <div class="row">
          <div>
            <label for="sim-action-type">Condition <span class="help" title="Which virtual condition AxeOS should see through normal telemetry.">?</span></label>
            <select id="sim-action-type" data-action-type>
              <option value="overheat">Overheat</option>
              <option value="high_error_rate">High error rate</option>
              <option value="fan_failure">Fan failure</option>
            </select>
          </div>
          <div>
            <label for="sim-action-severity">Severity <span class="help" title="How strongly the selected condition changes presentation telemetry.">?</span></label>
            <select id="sim-action-severity" data-severity>
              <option value="low">low</option>
              <option value="medium" selected>medium</option>
              <option value="high">high</option>
              <option value="critical">critical</option>
            </select>
          </div>
          <div>
            <label for="sim-action-duration">Duration <span class="help" title="How long the condition remains active. Manual runs until stopped or reset.">?</span></label>
            <select id="sim-action-duration" data-duration>
              <option value="30000">30s</option>
              <option value="120000" selected>2m</option>
              <option value="">manual</option>
            </select>
          </div>
        </div>
        <div class="row">
          <button type="button" class="primary" data-start>Start</button>
          <button type="button" data-reset>Reset</button>
        </div>
        <div class="list" data-list><span class="muted">No active actions.</span></div>
      </div>
    </div>`;
  document.body.appendChild(root);

  const request = async (url, options = {}) => {
    const response = await fetch(url, {
      ...options,
      headers: {"Content-Type": "application/json", ...(options.headers || {})}
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  };

  const renderState = (state) => {
    const list = root.querySelector("[data-list]");
    const status = root.querySelector("[data-status]");
    if (!state.active.length) {
      status.textContent = "No active actions.";
      list.innerHTML = '<span class="muted">No active actions.</span>';
      return;
    }
    status.textContent = `${state.active.length} active action${state.active.length === 1 ? "" : "s"}.`;
    list.innerHTML = "";
    for (const action of state.active) {
      const row = document.createElement("div");
      row.className = "item";
      row.innerHTML = `<span>${action.type} <span class="muted">${action.severity}</span></span>`;
      const stop = document.createElement("button");
      stop.type = "button";
      stop.textContent = "Stop";
      stop.addEventListener("click", async () => {
        await request("/sim/actions/stop", {method: "POST", body: JSON.stringify({id: action.id})});
        await refresh();
      });
      row.appendChild(stop);
      list.appendChild(row);
    }
  };

  const refresh = async () => renderState(await request("/sim/state"));

  root.querySelector("[data-toggle]").addEventListener("click", (event) => {
    root.classList.toggle("collapsed");
    event.currentTarget.textContent = root.classList.contains("collapsed") ? "Show" : "Hide";
  });
  root.querySelector("[data-reset]").addEventListener("click", async () => {
    await request("/sim/reset", {method: "POST", body: "{}"});
    await refresh();
  });
  root.querySelector("[data-start]").addEventListener("click", async () => {
    const duration = root.querySelector("[data-duration]").value;
    const payload = {
      type: root.querySelector("[data-action-type]").value,
      severity: root.querySelector("[data-severity]").value,
    };
    if (duration) payload.durationMs = Number(duration);
    await request("/sim/actions/start", {method: "POST", body: JSON.stringify(payload)});
    await refresh();
  });
  refresh().catch(() => {});
  setInterval(() => refresh().catch(() => {}), 3000);
})();
""".strip()


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def make_handler(
    *,
    backend_url: str,
    actions: SimulationActions,
    enabled: bool,
    inject_overlay: bool = True,
) -> type[BaseHTTPRequestHandler]:
    backend_base = backend_url.rstrip("/")
    backend_parts = urllib.parse.urlsplit(backend_base)
    backend_host = backend_parts.hostname or "127.0.0.1"
    backend_port = backend_parts.port or (443 if backend_parts.scheme == "https" else 80)
    backend_netloc = f"{backend_host}:{backend_port}"

    class SimulationProxyHandler(BaseHTTPRequestHandler):
        server_version = "virtualAxe-sim-proxy/1"

        def do_GET(self) -> None:
            if self.path == "/sim/capabilities":
                self._handle_sim_get(actions.capabilities())
            elif self.path == "/sim/state":
                self._handle_sim_get(actions.state())
            elif self.path == "/sim/overlay.js":
                self._handle_overlay()
            elif self.path.startswith("/sim/"):
                self._send_error_json(404, "simulation actions are not available")
            elif self._is_websocket_upgrade():
                self._proxy_websocket()
            else:
                self._proxy_request()

        def do_POST(self) -> None:
            if self.path == "/sim/actions/start":
                self._handle_action_start()
            elif self.path == "/sim/actions/stop":
                self._handle_action_stop()
            elif self.path == "/sim/reset":
                actions.reset()
                self._send_json(200, {"status": "reset"})
            elif self.path.startswith("/sim/"):
                self._send_error_json(404, "simulation actions are not available")
            else:
                self._proxy_request()

        def do_PATCH(self) -> None:
            self._proxy_request()

        def do_PUT(self) -> None:
            self._proxy_request()

        def do_DELETE(self) -> None:
            self._proxy_request()

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _handle_sim_get(self, payload: dict[str, Any]) -> None:
            if not enabled:
                self._send_error_json(404, "simulation actions are not available")
                return
            self._send_json(200, payload)

        def _handle_overlay(self) -> None:
            if not enabled:
                self._send_error_json(404, "simulation actions are not available")
                return
            body = OVERLAY_JS.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_action_start(self) -> None:
            if not enabled:
                self._send_error_json(404, "simulation actions are not available")
                return
            try:
                payload = self._read_json_body()
                result = actions.start_action(payload)
            except SimulationValidationError as exc:
                self._send_error_json(400, str(exc))
                return
            except ValueError as exc:
                self._send_error_json(400, str(exc))
                return
            self._send_json(200, result)

        def _handle_action_stop(self) -> None:
            if not enabled:
                self._send_error_json(404, "simulation actions are not available")
                return
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self._send_error_json(400, str(exc))
                return
            action_id = str(payload.get("id", "")).strip()
            if not action_id:
                self._send_error_json(400, "id is required")
                return
            if not actions.stop_action(action_id):
                self._send_error_json(404, "action not found")
                return
            self._send_json(200, {"id": action_id, "status": "stopped"})

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > MAX_REQUEST_BYTES:
                raise ValueError("request body is too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError("request body must be JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

        def _proxy_request(self) -> None:
            target_url = f"{backend_base}{self.path}"
            body = None
            if self.command in {"POST", "PATCH", "PUT"}:
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length) if length else b""
            request = urllib.request.Request(
                target_url,
                data=body,
                headers=self._forward_headers(),
                method=self.command,
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    response_body = response.read()
                    headers = dict(response.headers.items())
                    status = response.status
            except urllib.error.HTTPError as exc:
                response_body = exc.read()
                headers = dict(exc.headers.items())
                status = exc.code
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                self._send_error_json(502, f"backend request failed: {exc}")
                return

            if enabled and self.command == "GET" and self._is_system_info_path() and self._is_json_response(headers):
                response_body, headers = self._overlay_system_info(response_body, headers)
            elif enabled and inject_overlay and self.command == "GET" and self._is_html_response(headers):
                response_body, headers = self._inject_overlay(response_body, headers)

            self.send_response(status)
            for key, value in headers.items():
                if key.lower() in {"connection", "content-length", "transfer-encoding"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def _overlay_system_info(self, response_body: bytes, headers: dict[str, str]) -> tuple[bytes, dict[str, str]]:
            editable = self._editable_response_body(response_body, headers)
            if editable is None:
                return response_body, headers
            editable_body, editable_headers = editable
            try:
                payload = json.loads(editable_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return response_body, headers
            if not isinstance(payload, dict):
                return response_body, headers
            overlaid = actions.apply_telemetry_overlay(payload)
            for key in SIM_METADATA_KEYS:
                overlaid.pop(key, None)
            updated_headers = dict(editable_headers)
            updated_headers["Content-Type"] = "application/json"
            return json_bytes(overlaid), updated_headers

        def _inject_overlay(self, response_body: bytes, headers: dict[str, str]) -> tuple[bytes, dict[str, str]]:
            editable = self._editable_response_body(response_body, headers)
            if editable is None:
                return response_body, headers
            editable_body, editable_headers = editable
            try:
                html = editable_body.decode("utf-8")
            except UnicodeDecodeError:
                return response_body, headers
            script = '<script src="/sim/overlay.js"></script>'
            if script in html:
                return response_body, headers
            lower = html.lower()
            index = lower.rfind("</body>")
            if index >= 0:
                html = f"{html[:index]}{script}{html[index:]}"
            else:
                html = f"{html}{script}"
            updated_headers = dict(editable_headers)
            updated_headers["Content-Type"] = "text/html; charset=utf-8"
            return html.encode("utf-8"), updated_headers

        def _editable_response_body(
            self,
            response_body: bytes,
            headers: dict[str, str],
        ) -> tuple[bytes, dict[str, str]] | None:
            encoding = self._header_value(headers, "Content-Encoding").strip().lower()
            if not encoding or encoding == "identity":
                return response_body, dict(headers)
            if encoding != "gzip":
                return None
            try:
                decoded = gzip.decompress(response_body)
            except (EOFError, OSError):
                return None
            updated_headers = dict(headers)
            self._remove_header(updated_headers, "Content-Encoding")
            return decoded, updated_headers

        def _forward_headers(self) -> dict[str, str]:
            headers: dict[str, str] = {}
            for key, value in self.headers.items():
                if key.lower() in {"host", "connection", "content-length", "accept-encoding"}:
                    continue
                headers[key] = value
            return headers

        def _is_websocket_upgrade(self) -> bool:
            connection = self.headers.get("Connection", "")
            upgrade = self.headers.get("Upgrade", "")
            return (
                "upgrade" in connection.lower()
                and upgrade.lower() == "websocket"
            )

        def _proxy_websocket(self) -> None:
            if backend_parts.scheme != "http":
                self._send_error_json(502, "websocket proxy requires an http backend")
                return
            try:
                with socket.create_connection((backend_host, backend_port), timeout=5) as backend_socket:
                    backend_socket.sendall(self._websocket_request_bytes())
                    self._tunnel_sockets(backend_socket)
            except OSError as exc:
                self._send_error_json(502, f"backend websocket failed: {exc}")
            finally:
                self.close_connection = True

        def _websocket_request_bytes(self) -> bytes:
            request_target = self.path
            lines = [f"{self.command} {request_target} HTTP/1.1"]
            saw_host = False
            for key, value in self.headers.items():
                if key.lower() == "host":
                    lines.append(f"Host: {backend_netloc}")
                    saw_host = True
                else:
                    lines.append(f"{key}: {value}")
            if not saw_host:
                lines.append(f"Host: {backend_netloc}")
            lines.extend(["", ""])
            return "\r\n".join(lines).encode("iso-8859-1")

        def _tunnel_sockets(self, backend_socket: socket.socket) -> None:
            client_socket = self.connection
            client_socket.setblocking(False)
            backend_socket.setblocking(False)
            sockets = [client_socket, backend_socket]
            while sockets:
                readable, _, exceptional = select.select(sockets, [], sockets, 0.5)
                if exceptional:
                    return
                for source in readable:
                    try:
                        chunk = source.recv(65536)
                    except OSError:
                        return
                    if not chunk:
                        return
                    target = backend_socket if source is client_socket else client_socket
                    try:
                        target.sendall(chunk)
                    except OSError:
                        return

        def _is_system_info_path(self) -> bool:
            parsed = urllib.parse.urlsplit(self.path)
            return parsed.path == "/api/system/info"

        def _is_json_response(self, headers: dict[str, str]) -> bool:
            return "json" in self._header_value(headers, "Content-Type").lower()

        def _is_html_response(self, headers: dict[str, str]) -> bool:
            return "text/html" in self._header_value(headers, "Content-Type").lower()

        def _header_value(self, headers: dict[str, str], name: str) -> str:
            for key, value in headers.items():
                if key.lower() == name.lower():
                    return value
            return ""

        def _remove_header(self, headers: dict[str, str], name: str) -> None:
            for key in list(headers):
                if key.lower() == name.lower():
                    headers.pop(key, None)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, message: str) -> None:
            self._send_json(status, {"error": message})

    return SimulationProxyHandler


def serve(
    *,
    backend_url: str,
    listen_host: str,
    listen_port: int,
    enabled: bool,
) -> None:
    actions = SimulationActions()
    handler = make_handler(
        backend_url=backend_url,
        actions=actions,
        enabled=enabled,
        inject_overlay=enabled,
    )
    server = ThreadingHTTPServer((listen_host, listen_port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="virtualAxe local Simulation Actions proxy")
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument("--listen-host", default=DEFAULT_LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--enabled", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    serve(
        backend_url=args.backend_url,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        enabled=args.enabled,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
