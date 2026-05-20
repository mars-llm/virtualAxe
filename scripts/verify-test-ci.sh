#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/container-runtime.sh"
OUT_DIR="${OUT_DIR:-${ROOT_DIR}/out}"
SOURCE_NAME="${SOURCE:-${SOURCE_NAME:-bitaxe}}"
UPSTREAM_DIR="${UPSTREAM_DIR:-${ROOT_DIR}/.sources/${SOURCE_NAME}}"
IMAGE_NAME="${CONTAINER_IMAGE:-virtualaxe-dev}"
TEST_CI_LOG="${OUT_DIR}/test-ci-qemu.log"

mkdir -p "${OUT_DIR}"

run_test_ci_project() {
  local project_dir="$1"
  local log_file="$2"
  local temp_log_file="${log_file}.tmp"

  cd "${project_dir}"
  export GITHUB_ACTIONS="true"

  idf.py set-target esp32s3
  idf.py build

  python3 - "${temp_log_file}" <<'PY'
import os
import pathlib
import signal
import subprocess
import sys

log_path = pathlib.Path(sys.argv[1])
cmd = ["idf.py", "qemu"]
unity_pass_seen = False

with log_path.open("w", encoding="utf-8") as log_handle:
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )

    try:
        assert process.stdout is not None
        for line in process.stdout:
            sanitized_line = line.replace("\x00", "")
            sys.stdout.write(sanitized_line)
            log_handle.write(sanitized_line)
            log_handle.flush()
            if sanitized_line.strip() == "OK":
                unity_pass_seen = True
                os.killpg(process.pid, signal.SIGTERM)
                break

        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            returncode = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        raise SystemExit("Timed out waiting for test-ci QEMU run to finish")

if not unity_pass_seen:
    raise SystemExit("test-ci QEMU run never reached a passing Unity summary")

if returncode not in (0, -15, 143):
    raise SystemExit(returncode)
PY

  python3 - "${temp_log_file}" <<'PY'
import pathlib
import re
import sys

raw_log = pathlib.Path(sys.argv[1]).read_bytes()
log_text = raw_log.decode("utf-8", errors="ignore").replace("\x00", "")
if "Running all the registered tests" not in log_text:
    raise SystemExit("test-ci QEMU log did not contain the Unity test banner")

ok_match = re.search(r"(?m)^OK$", log_text)
if ok_match is None:
    raise SystemExit("test-ci QEMU log did not contain the Unity OK summary")

unity_log = log_text[:ok_match.end()]
failure_patterns = [
    r":FAIL",
    r"Tests\s+\d+\s+Failures\s+[1-9]\d*",
    r"FAILED",
]
for pattern in failure_patterns:
    if re.search(pattern, unity_log):
        raise SystemExit(f"test-ci QEMU log matched failure pattern: {pattern}")
PY

  mv "${temp_log_file}" "${log_file}"
}

virtualaxe_select_execution_environment

if [[ "${EXECUTION_MODE}" == "native" ]]; then
  run_test_ci_project "${UPSTREAM_DIR}/test-ci" "${TEST_CI_LOG}"
  exit 0
fi

workspace_path() {
  local path="$1"
  case "${path}" in
    "${ROOT_DIR}"/*)
      printf '/workspace%s\n' "${path#"${ROOT_DIR}"}"
      ;;
    *)
      echo "Path ${path} is outside ${ROOT_DIR} and cannot be mounted into the test-ci container." >&2
      exit 1
      ;;
  esac
}

CONTAINER_PROJECT_DIR="$(workspace_path "${UPSTREAM_DIR}")/test-ci"
CONTAINER_LOG_FILE="$(workspace_path "${TEST_CI_LOG}")"

"${CONTAINER_RUNTIME}" run --rm \
  -v "${ROOT_DIR}:/workspace" \
  -w /workspace \
  "${IMAGE_NAME}" \
  bash -lc '
    set -euo pipefail
    '"$(declare -f run_test_ci_project)"'
    run_test_ci_project "'"${CONTAINER_PROJECT_DIR}"'" "'"${CONTAINER_LOG_FILE}"'"
  '
