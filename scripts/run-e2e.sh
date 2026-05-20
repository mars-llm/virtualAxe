#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-${ROOT_DIR}/out}"
MODE="all"
HTTP_PORT="${HTTP_PORT:-18080}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${HTTP_PORT}}"
VIRTUAL_PROFILE="${VIRTUAL_PROFILE:-gamma}"
SOURCE_NAME="${SOURCE_NAME:-${SOURCE:-bitaxe}}"

EXPECTED_DEVICE_MODEL="Gamma"
EXPECTED_ASIC_COUNT=1
if [[ "${SOURCE_NAME}" == "nerdnos" ]]; then
  EXPECTED_DEVICE_MODEL="virtualAxe Gamma"
fi

build_inputs_newer_than_flash() {
  local flash_file="$1"

  if find "${ROOT_DIR}/patches/esp-miner" "${ROOT_DIR}/configs" -type f -newer "${flash_file}" | read -r _; then
    return 0
  fi

  local input
  for input in \
    "${ROOT_DIR}/scripts/apply-patches.sh" \
    "${ROOT_DIR}/scripts/build-virtual.sh" \
    "${ROOT_DIR}/scripts/render-virtual-config.py" \
    "${ROOT_DIR}/scripts/sync-upstream.sh"; do
    if [[ "${input}" -nt "${flash_file}" ]]; then
      return 0
    fi
  done

  return 1
}

should_rebuild() {
  [[ ! -f "${OUT_DIR}/qemu_flash.bin" ]] && return 0

  if [[ "${RESET_PERSISTED_STATE:-0}" == "1" ]]; then
    return 0
  fi

  if [[ ! -f "${OUT_DIR}/manifest.json" ]]; then
    return 0
  fi

  if ! python3 - "${OUT_DIR}/manifest.json" <<'PY'
import json
import os
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "sourceName": os.environ.get("SOURCE_NAME", "bitaxe"),
    "virtualProfile": os.environ.get("VIRTUAL_PROFILE", "gamma"),
    "virtualAsicMode": os.environ.get("VIRTUAL_ASIC_MODE", "cpu"),
    "poolHost": os.environ.get("POOL_HOST", "public-pool.io"),
    "poolPort": int(os.environ.get("POOL_PORT", "3333")),
    "poolDifficulty": float(os.environ.get("POOL_DIFF", "1")),
    "poolSubscribeAgent": os.environ.get("POOL_SUBSCRIBE_AGENT", ""),
}

for key, value in expected.items():
    if manifest.get(key) != value:
        raise SystemExit(1)
PY
  then
    return 0
  fi

  if build_inputs_newer_than_flash "${OUT_DIR}/qemu_flash.bin"; then
    return 0
  fi

  return 1
}

case "${1:-}" in
  --api-only)
    MODE="api"
    ;;
  --browser-only)
    MODE="browser"
    ;;
  "")
    ;;
  *)
    echo "Unsupported option: ${1}" >&2
    exit 1
    ;;
esac

cleanup() {
  "${ROOT_DIR}/scripts/run-qemu-nat.sh" --stop >/dev/null 2>&1 || true
}
trap cleanup EXIT

if should_rebuild; then
  "${ROOT_DIR}/scripts/build-virtual.sh"
fi

HTTP_PORT="${HTTP_PORT}" BACKGROUND=1 "${ROOT_DIR}/scripts/run-qemu-nat.sh"
HTTP_PORT="${HTTP_PORT}" "${ROOT_DIR}/scripts/wait-for-http.sh"

if [[ "${MODE}" != "browser" ]]; then
  "${ROOT_DIR}/scripts/ensure-test-python.sh"

  BASE_URL="${BASE_URL}" \
  EXPECTED_DEVICE_MODEL="${EXPECTED_DEVICE_MODEL}" \
  EXPECTED_ASIC_COUNT="${EXPECTED_ASIC_COUNT}" \
    "${ROOT_DIR}/.venv/bin/python" -m pytest -q "${ROOT_DIR}/tests/api"
fi

if [[ "${MODE}" != "api" ]]; then
  if [[ ! -d "${ROOT_DIR}/tests/browser/node_modules/@playwright/test" ]]; then
    if [[ -f "${ROOT_DIR}/tests/browser/package-lock.json" ]]; then
      npm ci --prefix "${ROOT_DIR}/tests/browser"
    else
      npm install --prefix "${ROOT_DIR}/tests/browser"
    fi
  fi
  BASE_URL="${BASE_URL}" npm --prefix "${ROOT_DIR}/tests/browser" exec -- \
    playwright test --config "${ROOT_DIR}/tests/browser/playwright.config.ts"
fi
