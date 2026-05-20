#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-${ROOT_DIR}/out}"
SOURCE_NAME="${SOURCE:-${SOURCE_NAME:-bitaxe}}"
VIRTUAL_PROFILE="${VIRTUAL_PROFILE:-gamma}"
STATE_DIR="${STATE_DIR:-${ROOT_DIR}/.state/${SOURCE_NAME}/${VIRTUAL_PROFILE}}"
HTTP_PORT="${HTTP_PORT:-18080}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${HTTP_PORT}}"
QEMU_FLASH_FILE="${OUT_DIR}/qemu_flash.bin"
NVS_FILE="${OUT_DIR}/nvs.bin"
STATE_NVS_FILE="${STATE_DIR}/nvs.bin"
ACTIVE_CSV_FILE="${OUT_DIR}/config-active.csv"

mkdir -p "${OUT_DIR}"
mkdir -p "${STATE_DIR}"

FLASH_EXISTED=0
NVS_EXISTED=0
STATE_NVS_EXISTED=0
ACTIVE_CSV_EXISTED=0
FLASH_BACKUP=""
NVS_BACKUP=""
STATE_NVS_BACKUP=""
ACTIVE_CSV_BACKUP=""
declare -a TEMP_FILES=()

backup_file() {
  local source="$1"
  local backup_var="$2"

  if [[ ! -f "${source}" ]]; then
    printf -v "${backup_var}" '%s' ""
    return
  fi

  local backup
  backup="$(mktemp "${OUT_DIR}/$(basename "${source}").backup.XXXXXX")"
  cp "${source}" "${backup}"
  TEMP_FILES+=("${backup}")
  printf -v "${backup_var}" '%s' "${backup}"
}

write_settings_json() {
  local output="$1"
  shift

  python3 - "${output}" "$@" <<'PY'
import json
import sys

output = sys.argv[1]
args = sys.argv[2:]
if len(args) % 3 != 0:
    raise SystemExit("expected key/type/value triples")

payload = {}
for index in range(0, len(args), 3):
    key, value_type, raw_value = args[index:index + 3]
    if value_type == "int":
        value = int(raw_value)
    elif value_type == "bool":
        value = raw_value == "true"
    else:
        value = raw_value
    payload[key] = value

with open(output, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY
}

cleanup() {
  "${ROOT_DIR}/scripts/run-qemu-nat.sh" --stop >/dev/null 2>&1 || true

  if [[ "${FLASH_EXISTED}" == "1" && -n "${FLASH_BACKUP}" ]]; then
    cp "${FLASH_BACKUP}" "${QEMU_FLASH_FILE}"
  fi

  if [[ "${NVS_EXISTED}" == "1" && -n "${NVS_BACKUP}" ]]; then
    cp "${NVS_BACKUP}" "${NVS_FILE}"
  elif [[ "${NVS_EXISTED}" == "0" ]]; then
    rm -f "${NVS_FILE}"
  fi

  if [[ "${STATE_NVS_EXISTED}" == "1" && -n "${STATE_NVS_BACKUP}" ]]; then
    cp "${STATE_NVS_BACKUP}" "${STATE_NVS_FILE}"
  elif [[ "${STATE_NVS_EXISTED}" == "0" ]]; then
    rm -f "${STATE_NVS_FILE}"
  fi

  if [[ "${ACTIVE_CSV_EXISTED}" == "1" && -n "${ACTIVE_CSV_BACKUP}" ]]; then
    cp "${ACTIVE_CSV_BACKUP}" "${ACTIVE_CSV_FILE}"
  elif [[ "${ACTIVE_CSV_EXISTED}" == "0" ]]; then
    rm -f "${ACTIVE_CSV_FILE}"
  fi

  rm -f "${TEMP_FILES[@]}"
}
trap cleanup EXIT

if [[ -f "${QEMU_FLASH_FILE}" ]]; then
  FLASH_EXISTED=1
fi
if [[ -f "${NVS_FILE}" ]]; then
  NVS_EXISTED=1
fi
if [[ -f "${STATE_NVS_FILE}" ]]; then
  STATE_NVS_EXISTED=1
fi
if [[ -f "${ACTIVE_CSV_FILE}" ]]; then
  ACTIVE_CSV_EXISTED=1
fi

backup_file "${QEMU_FLASH_FILE}" FLASH_BACKUP
backup_file "${NVS_FILE}" NVS_BACKUP
backup_file "${STATE_NVS_FILE}" STATE_NVS_BACKUP
backup_file "${ACTIVE_CSV_FILE}" ACTIVE_CSV_BACKUP

if [[ ! -f "${QEMU_FLASH_FILE}" ]]; then
  RESET_PERSISTED_STATE=1 "${ROOT_DIR}/scripts/build-virtual.sh"
fi

info_field() {
  local field="$1"
  python3 -c '
import json
import sys

field = sys.argv[1]
payload = json.load(sys.stdin)
print(payload[field])
' "${field}"
}

system_info_matches() {
  local expected_json="$1"
  python3 -c '
import json
import sys

expected_path = sys.argv[1]
with open(expected_path, "r", encoding="utf-8") as handle:
    expected = json.load(handle)
actual = json.load(sys.stdin)

for key, value in expected.items():
    if actual.get(key) != value:
        raise SystemExit(1)
' "${expected_json}"
}

wait_for_expected_settings() {
  local expected_json="$1"
  local timeout_seconds="${2:-20}"
  local deadline=$((SECONDS + timeout_seconds))
  local payload=""

  while (( SECONDS < deadline )); do
    payload="$(curl -fsS "${BASE_URL}/api/system/info")"
    if printf '%s' "${payload}" | system_info_matches "${expected_json}"; then
      return 0
    fi
    sleep 0.25
  done

  echo "Timed out waiting for expected settings from ${expected_json}. Last payload: ${payload}" >&2
  return 1
}

assert_manifest_nvs_seed_mode() {
  local expected_mode="$1"

  python3 - "${OUT_DIR}/manifest.json" "${expected_mode}" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
expected_mode = sys.argv[2]
payload = json.loads(manifest_path.read_text(encoding="utf-8"))
actual_mode = payload.get("nvsSeedMode")
if actual_mode != expected_mode:
    raise SystemExit(f"expected nvsSeedMode={expected_mode}, got {actual_mode!r}")
PY
}

start_qemu() {
  HTTP_PORT="${HTTP_PORT}" BACKGROUND=1 "${ROOT_DIR}/scripts/run-qemu-nat.sh"
  HTTP_PORT="${HTTP_PORT}" "${ROOT_DIR}/scripts/wait-for-http.sh"
}

stop_qemu() {
  "${ROOT_DIR}/scripts/run-qemu-nat.sh" --stop >/dev/null
}

PATCH_FILE="$(mktemp "${OUT_DIR}/settings-patch.XXXXXX")"
EXPECTED_FILE="$(mktemp "${OUT_DIR}/settings-expected.XXXXXX")"
TEMP_FILES+=("${PATCH_FILE}" "${EXPECTED_FILE}")

temporary_hostname() {
  local original="$1"
  local suffix="-persist-check"
  if [[ "${original}" == *"${suffix}" ]]; then
    suffix="-persist-restore"
  fi

  local prefix_length=$((32 - ${#suffix}))
  if (( prefix_length < 1 )); then
    prefix_length=1
  fi
  printf '%s%s\n' "${original:0:prefix_length}" "${suffix}"
}

start_qemu

INITIAL_INFO="$(curl -fsS "${BASE_URL}/api/system/info")"
ORIGINAL_HOSTNAME="$(printf '%s' "${INITIAL_INFO}" | info_field hostname)"
ORIGINAL_FALLBACK_USER="$(printf '%s' "${INITIAL_INFO}" | info_field fallbackStratumUser)"
ORIGINAL_MANUAL_FAN_SPEED="$(printf '%s' "${INITIAL_INFO}" | info_field manualFanSpeed)"

TEMP_HOSTNAME="$(temporary_hostname "${ORIGINAL_HOSTNAME}")"
TEMP_FALLBACK_USER="${ORIGINAL_FALLBACK_USER}.persist-check"
if [[ "${ORIGINAL_MANUAL_FAN_SPEED}" == "33" ]]; then
  TEMP_MANUAL_FAN_SPEED=34
else
  TEMP_MANUAL_FAN_SPEED=33
fi

write_settings_json "${PATCH_FILE}" \
  hostname str "${TEMP_HOSTNAME}" \
  fallbackStratumUser str "${TEMP_FALLBACK_USER}" \
  manualFanSpeed int "${TEMP_MANUAL_FAN_SPEED}"

curl -fsS -X PATCH \
  -H "Content-Type: application/json" \
  --data "@${PATCH_FILE}" \
  "${BASE_URL}/api/system" >/dev/null

write_settings_json "${EXPECTED_FILE}" \
  hostname str "${TEMP_HOSTNAME}" \
  fallbackStratumUser str "${TEMP_FALLBACK_USER}" \
  manualFanSpeed int "${TEMP_MANUAL_FAN_SPEED}"
wait_for_expected_settings "${EXPECTED_FILE}"
sleep 2

stop_qemu
start_qemu
wait_for_expected_settings "${EXPECTED_FILE}"

stop_qemu
"${ROOT_DIR}/scripts/build-virtual.sh"
assert_manifest_nvs_seed_mode "preserved"

start_qemu
wait_for_expected_settings "${EXPECTED_FILE}"

echo "Verified persisted settings across restart and rebuild."
