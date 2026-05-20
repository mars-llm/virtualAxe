#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/virtualaxe-clean-clone-smoke.XXXXXX")"
CLONE_DIR="${RUN_ROOT}/virtualAxe"
SUCCESS=0

cleanup() {
  if [[ "${SUCCESS}" == "1" ]]; then
    rm -rf "${RUN_ROOT}"
  else
    echo "clean-clone-smoke failed; preserved clone at ${CLONE_DIR}" >&2
  fi
}
trap cleanup EXIT

if ! git -C "${ROOT_DIR}" diff --quiet || ! git -C "${ROOT_DIR}" diff --cached --quiet; then
  echo "clean-clone-smoke requires a clean tracked worktree." >&2
  exit 1
fi

echo "[clean-clone-smoke] cloning ${ROOT_DIR}"
git clone --quiet "${ROOT_DIR}" "${CLONE_DIR}"

run_step() {
  echo "[clean-clone-smoke] $*"
  "$@"
}

cd "${CLONE_DIR}"
run_step ./vaxe
run_step make help
run_step make drift-check
run_step make validate-lite
run_step make build SOURCE=bitaxe
run_step make build SOURCE=nerdnos

SUCCESS=1
echo "[clean-clone-smoke] passed"
