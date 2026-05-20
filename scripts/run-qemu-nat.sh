#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/container-runtime.sh"
OUT_DIR="${OUT_DIR:-${ROOT_DIR}/out}"
SOURCE_NAME="${SOURCE:-${SOURCE_NAME:-bitaxe}}"
UPSTREAM_DIR="${UPSTREAM_DIR:-${ROOT_DIR}/.sources/${SOURCE_NAME}}"
IMAGE_NAME="${CONTAINER_IMAGE:-virtualaxe-dev}"
HTTP_PORT="${HTTP_PORT:-18080}"
VIRTUAL_ASIC_MODE="${VIRTUAL_ASIC_MODE:-cpu}"
CONTAINER_NAME="${QEMU_CONTAINER_NAME:-virtualaxe-qemu}"
QEMU_PID_FILE="${OUT_DIR}/qemu.pid"
QEMU_CID_FILE="${OUT_DIR}/qemu.cid"
SUBMIT_REPLAY=0
if [[ "${1:-}" == "--submit-replay" ]]; then
  SUBMIT_REPLAY=1
fi
STRATUM_REPLAY_HOST="${STRATUM_REPLAY_HOST:-0.0.0.0}"
STRATUM_REPLAY_PORT="${STRATUM_REPLAY_PORT:-3333}"
STRATUM_REPLAY_DIFFICULTY="${STRATUM_REPLAY_DIFFICULTY:-0.000001}"
STRATUM_REPLAY_USERNAME="${STRATUM_REPLAY_USERNAME:-bc1qvirtualaxereplay.worker}"
STRATUM_REPLAY_EXTRANONCE1="${STRATUM_REPLAY_EXTRANONCE1:-01000000}"
STRATUM_REPLAY_EXTRANONCE2_SIZE="${STRATUM_REPLAY_EXTRANONCE2_SIZE:-4}"
STRATUM_REPLAY_TIMEOUT="${STRATUM_REPLAY_TIMEOUT:-300}"
STRATUM_REPLAY_JSON_FILE="${OUT_DIR}/stratum-replay.json"
STRATUM_REPLAY_STDERR_FILE="${OUT_DIR}/stratum-replay.err.log"

source_field() {
  local field="$1"
  python3 - "${ROOT_DIR}" "${SOURCE_NAME}" "${field}" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
source_name = sys.argv[2]
field = sys.argv[3]
sys.path.insert(0, str(root / "scripts"))
from source_registry import load_source_registry

source = load_source_registry(root / "configs" / "sources.json").get(source_name)
values = source.as_legacy_entry()
print(values.get(field, ""))
PY
}

mkdir -p "${OUT_DIR}"

case "${VIRTUAL_ASIC_MODE}" in
  cpu)
    ;;
  *)
    echo "Unsupported VIRTUAL_ASIC_MODE=${VIRTUAL_ASIC_MODE}. Use cpu." >&2
    exit 1
    ;;
esac

stop_pid_file() {
  local pid_file="$1"
  if [[ -f "${pid_file}" ]]; then
    kill "$(cat "${pid_file}")" >/dev/null 2>&1 || true
    rm -f "${pid_file}"
  fi
}

if [[ "${1:-}" == "--stop" ]]; then
  if command -v podman >/dev/null 2>&1; then
    podman rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi
  if command -v docker >/dev/null 2>&1; then
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi
  stop_pid_file "${QEMU_PID_FILE}"
  rm -f "${QEMU_CID_FILE}"
  exit 0
fi

virtualaxe_select_execution_environment

QEMU_MEMORY_MB="${QEMU_MEMORY_MB:-$(source_field qemuMemoryMb)}"
QEMU_MEMORY_MB="${QEMU_MEMORY_MB:-32}"
case "${QEMU_MEMORY_MB}" in
  ''|*[!0-9]*|0)
    echo "Unsupported QEMU_MEMORY_MB=${QEMU_MEMORY_MB}. Use an integer number of megabytes." >&2
    exit 1
    ;;
esac
QEMU_MACHINE_ARGS=(
  -M esp32s3
  -m "${QEMU_MEMORY_MB}M"
  -drive "file=${OUT_DIR}/qemu_flash.bin,if=mtd,format=raw"
  -drive "file=${OUT_DIR}/qemu_efuse.bin,if=none,format=raw,id=efuse"
  -global driver=nvram.esp32s3.efuse,property=drive,value=efuse
  -global driver=timer.esp32s3.timg,property=wdt_disable,value=true
  # virtualAxe always builds the ESP32-S3 image with octal PSRAM enabled.
  -global driver=ssi_psram,property=is_octal,value=true
  -nographic
  -serial mon:stdio
)

if [[ ! -f "${OUT_DIR}/qemu_flash.bin" ]]; then
  "${ROOT_DIR}/scripts/build-virtual.sh"
fi

run_native_qemu() {
  : > "${OUT_DIR}/qemu.log"
  if [[ "${VIRTUALAXE_DISABLE_TEE:-0}" == "1" ]]; then
    exec >> "${OUT_DIR}/qemu.log" 2>&1
  else
    exec > >(tee "${OUT_DIR}/qemu.log") 2>&1
  fi

  local qemu_cmd=(
    qemu-system-xtensa
    "${QEMU_MACHINE_ARGS[@]}"
    -nic "user,model=open_eth,hostfwd=tcp::${HTTP_PORT}-:80"
  )

  if [[ "${BACKGROUND:-0}" == "1" ]]; then
    exec "${qemu_cmd[@]}"
  else
    exec "${qemu_cmd[@]}"
  fi
}

run_native_submit_replay() {
  : > "${OUT_DIR}/qemu.log"
  : > "${STRATUM_REPLAY_JSON_FILE}"
  : > "${STRATUM_REPLAY_STDERR_FILE}"

  python3 "${ROOT_DIR}/scripts/stratum_replay.py" \
    --host "${STRATUM_REPLAY_HOST}" \
    --port "${STRATUM_REPLAY_PORT}" \
    --difficulty "${STRATUM_REPLAY_DIFFICULTY}" \
    --username "${STRATUM_REPLAY_USERNAME}" \
    --extranonce1 "${STRATUM_REPLAY_EXTRANONCE1}" \
    --extranonce2-size "${STRATUM_REPLAY_EXTRANONCE2_SIZE}" \
    --timeout "${STRATUM_REPLAY_TIMEOUT}" \
    > "${STRATUM_REPLAY_JSON_FILE}" \
    2> "${STRATUM_REPLAY_STDERR_FILE}" &
  local replay_pid="$!"

  qemu-system-xtensa \
    "${QEMU_MACHINE_ARGS[@]}" \
    -nic "user,model=open_eth,hostfwd=tcp::${HTTP_PORT}-:80" \
    >> "${OUT_DIR}/qemu.log" \
    2>&1 &
  local qemu_pid="$!"
  echo "${qemu_pid}" > "${QEMU_PID_FILE}"

  cleanup_submit_replay() {
    kill "${replay_pid}" >/dev/null 2>&1 || true
    kill "${qemu_pid}" >/dev/null 2>&1 || true
    rm -f "${QEMU_PID_FILE}"
  }
  trap cleanup_submit_replay EXIT INT TERM

  local replay_rc=0
  local accepted_seen=0
  while kill -0 "${replay_pid}" >/dev/null 2>&1; do
    if grep -q "\"status\": \"accepted\"" "${STRATUM_REPLAY_JSON_FILE}"; then
      accepted_seen=1
      break
    fi
    sleep 0.1
  done
  if [[ "${accepted_seen}" == "1" ]]; then
    for _ in {1..80}; do
      if grep -q "message result accepted" "${OUT_DIR}/qemu.log"; then
        break
      fi
      sleep 0.1
    done
  fi
  if kill -0 "${replay_pid}" >/dev/null 2>&1; then
    kill "${replay_pid}" >/dev/null 2>&1 || true
    wait "${replay_pid}" >/dev/null 2>&1 || true
  else
    wait "${replay_pid}" || replay_rc=$?
  fi
  kill "${qemu_pid}" >/dev/null 2>&1 || true
  wait "${qemu_pid}" >/dev/null 2>&1 || true
  rm -f "${QEMU_PID_FILE}"
  trap - EXIT INT TERM

  if [[ -s "${STRATUM_REPLAY_JSON_FILE}" ]]; then
    cat "${STRATUM_REPLAY_JSON_FILE}"
  fi
  return "${replay_rc}"
}

if [[ "${EXECUTION_MODE}" == "native" ]]; then
  if [[ "${SUBMIT_REPLAY}" == "1" ]]; then
    run_native_submit_replay
    exit $?
  fi
  if [[ "${BACKGROUND:-0}" == "1" ]]; then
    stop_pid_file "${QEMU_PID_FILE}"
    run_native_qemu > /dev/null 2>&1 &
    echo "$!" > "${QEMU_PID_FILE}"
    echo "QEMU process started with pid $(cat "${QEMU_PID_FILE}")"
  else
    run_native_qemu
  fi
  exit 0
fi

workspace_path() {
  local path="$1"
  case "${path}" in
    "${ROOT_DIR}"/*)
      printf '/workspace%s\n' "${path#"${ROOT_DIR}"}"
      ;;
    *)
      echo "Path ${path} is outside ${ROOT_DIR} and cannot be mounted into the QEMU container." >&2
      exit 1
      ;;
  esac
}

CONTAINER_UPSTREAM_DIR="$(workspace_path "${UPSTREAM_DIR}")"
CONTAINER_OUT_DIR="$(workspace_path "${OUT_DIR}")"

QEMU_CMD='
  set -euo pipefail
  : > "'"${CONTAINER_OUT_DIR}"'/qemu.log"
  if [[ "'"${VIRTUALAXE_DISABLE_TEE:-0}"'" == "1" ]]; then
    exec >> "'"${CONTAINER_OUT_DIR}"'/qemu.log" 2>&1
  else
    exec > >(tee "'"${CONTAINER_OUT_DIR}"'/qemu.log") 2>&1
  fi
  exec qemu-system-xtensa \
    -M esp32s3 \
    -m "'"${QEMU_MEMORY_MB}"'M" \
    -drive "file='"${CONTAINER_OUT_DIR}"'/qemu_flash.bin,if=mtd,format=raw" \
    -drive "file='"${CONTAINER_OUT_DIR}"'/qemu_efuse.bin,if=none,format=raw,id=efuse" \
    -global driver=nvram.esp32s3.efuse,property=drive,value=efuse \
    -global driver=timer.esp32s3.timg,property=wdt_disable,value=true \
    -global driver=ssi_psram,property=is_octal,value=true \
    -nic "user,model=open_eth,hostfwd=tcp::'"${HTTP_PORT}"'-:80" \
    -nographic \
    -serial mon:stdio
'

if [[ "${SUBMIT_REPLAY}" == "1" ]]; then
  QEMU_CMD='
    set -euo pipefail
    : > "'"${CONTAINER_OUT_DIR}"'/qemu.log"
    : > "'"${CONTAINER_OUT_DIR}"'/stratum-replay.json"
    : > "'"${CONTAINER_OUT_DIR}"'/stratum-replay.err.log"

    python3 /workspace/scripts/stratum_replay.py \
      --host "${STRATUM_REPLAY_HOST}" \
      --port "${STRATUM_REPLAY_PORT}" \
      --difficulty "${STRATUM_REPLAY_DIFFICULTY}" \
      --username "${STRATUM_REPLAY_USERNAME}" \
      --extranonce1 "${STRATUM_REPLAY_EXTRANONCE1}" \
      --extranonce2-size "${STRATUM_REPLAY_EXTRANONCE2_SIZE}" \
      --timeout "${STRATUM_REPLAY_TIMEOUT}" \
      > "'"${CONTAINER_OUT_DIR}"'/stratum-replay.json" \
      2> "'"${CONTAINER_OUT_DIR}"'/stratum-replay.err.log" &
    replay_pid="$!"

    qemu-system-xtensa \
      -M esp32s3 \
      -m "'"${QEMU_MEMORY_MB}"'M" \
      -drive "file='"${CONTAINER_OUT_DIR}"'/qemu_flash.bin,if=mtd,format=raw" \
      -drive "file='"${CONTAINER_OUT_DIR}"'/qemu_efuse.bin,if=none,format=raw,id=efuse" \
      -global driver=nvram.esp32s3.efuse,property=drive,value=efuse \
      -global driver=timer.esp32s3.timg,property=wdt_disable,value=true \
      -global driver=ssi_psram,property=is_octal,value=true \
      -nic "user,model=open_eth,hostfwd=tcp::'"${HTTP_PORT}"'-:80" \
      -nographic \
      -serial mon:stdio \
      >> "'"${CONTAINER_OUT_DIR}"'/qemu.log" \
      2>&1 &
    qemu_pid="$!"

    cleanup_submit_replay() {
      kill "${replay_pid}" >/dev/null 2>&1 || true
      kill "${qemu_pid}" >/dev/null 2>&1 || true
    }
    trap cleanup_submit_replay EXIT INT TERM

    replay_rc=0
    accepted_seen=0
    while kill -0 "${replay_pid}" >/dev/null 2>&1; do
      if grep -q "\"status\": \"accepted\"" "'"${CONTAINER_OUT_DIR}"'/stratum-replay.json"; then
        accepted_seen=1
        break
      fi
      sleep 0.1
    done
    if [[ "${accepted_seen}" == "1" ]]; then
      for _ in {1..80}; do
        if grep -q "message result accepted" "'"${CONTAINER_OUT_DIR}"'/qemu.log"; then
          break
        fi
        sleep 0.1
      done
    fi
    if kill -0 "${replay_pid}" >/dev/null 2>&1; then
      kill "${replay_pid}" >/dev/null 2>&1 || true
      wait "${replay_pid}" >/dev/null 2>&1 || true
    else
      wait "${replay_pid}" || replay_rc=$?
    fi
    kill "${qemu_pid}" >/dev/null 2>&1 || true
    wait "${qemu_pid}" >/dev/null 2>&1 || true
    trap - EXIT INT TERM

    if [[ -s "'"${CONTAINER_OUT_DIR}"'/stratum-replay.json" ]]; then
      cat "'"${CONTAINER_OUT_DIR}"'/stratum-replay.json"
    fi
    exit "${replay_rc}"
  '
fi

CONTAINER_ENV_ARGS=(
  -e VIRTUALAXE_DISABLE_TEE="${VIRTUALAXE_DISABLE_TEE:-0}"
)
if [[ "${SUBMIT_REPLAY}" == "1" ]]; then
  CONTAINER_ENV_ARGS+=(
    -e STRATUM_REPLAY_HOST="${STRATUM_REPLAY_HOST}"
    -e STRATUM_REPLAY_PORT="${STRATUM_REPLAY_PORT}"
    -e STRATUM_REPLAY_DIFFICULTY="${STRATUM_REPLAY_DIFFICULTY}"
    -e STRATUM_REPLAY_USERNAME="${STRATUM_REPLAY_USERNAME}"
    -e STRATUM_REPLAY_EXTRANONCE1="${STRATUM_REPLAY_EXTRANONCE1}"
    -e STRATUM_REPLAY_EXTRANONCE2_SIZE="${STRATUM_REPLAY_EXTRANONCE2_SIZE}"
    -e STRATUM_REPLAY_TIMEOUT="${STRATUM_REPLAY_TIMEOUT}"
  )
fi

"${CONTAINER_RUNTIME}" rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

if [[ "${SUBMIT_REPLAY}" == "1" ]]; then
  exec "${CONTAINER_RUNTIME}" run --rm \
    --name "${CONTAINER_NAME}" \
    -p "127.0.0.1:${HTTP_PORT}:${HTTP_PORT}" \
    -v "${ROOT_DIR}:/workspace" \
    -w /workspace \
    "${CONTAINER_ENV_ARGS[@]}" \
    "${IMAGE_NAME}" \
    bash -lc "${QEMU_CMD}"
elif [[ "${BACKGROUND:-0}" == "1" ]]; then
  "${CONTAINER_RUNTIME}" run -d \
    --name "${CONTAINER_NAME}" \
    -p "127.0.0.1:${HTTP_PORT}:${HTTP_PORT}" \
    -v "${ROOT_DIR}:/workspace" \
    -w /workspace \
    "${CONTAINER_ENV_ARGS[@]}" \
    "${IMAGE_NAME}" \
    bash -lc "${QEMU_CMD}" > "${QEMU_CID_FILE}"
  echo "QEMU container started as ${CONTAINER_NAME}"
else
  exec "${CONTAINER_RUNTIME}" run --rm -it \
    --name "${CONTAINER_NAME}" \
    -p "127.0.0.1:${HTTP_PORT}:${HTTP_PORT}" \
    -v "${ROOT_DIR}:/workspace" \
    -w /workspace \
    "${CONTAINER_ENV_ARGS[@]}" \
    "${IMAGE_NAME}" \
    bash -lc "${QEMU_CMD}"
fi
