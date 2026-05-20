#!/usr/bin/env bash

virtualaxe_command_exists() {
  command -v "$1" >/dev/null 2>&1
}

virtualaxe_runtime_usable() {
  local runtime="$1"
  "${runtime}" info >/dev/null 2>&1
}

virtualaxe_try_start_podman_machine() {
  if ! virtualaxe_command_exists podman; then
    return 1
  fi
  if ! podman machine list >/dev/null 2>&1; then
    return 1
  fi

  echo "Podman is installed but not reachable. Trying to start the default Podman machine."
  podman machine start >/dev/null 2>&1 || true
  virtualaxe_runtime_usable podman
}

virtualaxe_select_execution_environment() {
  local diagnostics=()

  EXECUTION_MODE=""
  CONTAINER_RUNTIME=""

  if virtualaxe_command_exists podman; then
    if virtualaxe_runtime_usable podman || virtualaxe_try_start_podman_machine; then
      EXECUTION_MODE="container"
      CONTAINER_RUNTIME="podman"
      return 0
    fi
    diagnostics+=("Podman is installed but is not reachable. Start the existing machine with: podman machine start")
  else
    diagnostics+=("Podman is not installed.")
  fi

  if virtualaxe_command_exists docker; then
    if virtualaxe_runtime_usable docker; then
      EXECUTION_MODE="container"
      CONTAINER_RUNTIME="docker"
      return 0
    fi
    diagnostics+=("Docker is installed but is not reachable. Start Docker Desktop or the Docker daemon.")
  else
    diagnostics+=("Docker is not installed.")
  fi

  if virtualaxe_command_exists idf.py && [[ -n "${IDF_PATH:-}" ]]; then
    EXECUTION_MODE="native"
    return 0
  fi
  diagnostics+=("Native ESP-IDF is not configured because idf.py and IDF_PATH are not both available.")

  echo "No usable container or native ESP-IDF runtime is available." >&2
  for diagnostic in "${diagnostics[@]}"; do
    echo "- ${diagnostic}" >&2
  done
  echo "virtualAxe can start an existing Podman machine, but it will not create or recreate one because that can change local container state." >&2
  return 1
}
