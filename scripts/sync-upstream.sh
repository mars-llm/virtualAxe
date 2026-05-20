#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_NAME="${SOURCE:-${SOURCE_NAME:-bitaxe}}"
SOURCES_FILE="${ROOT_DIR}/configs/sources.json"

read_source_field() {
  local field="$1"
  python3 - "${ROOT_DIR}" "${SOURCES_FILE}" "${SOURCE_NAME}" "${field}" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
sources_file = Path(sys.argv[2])
source_name = sys.argv[3]
field = sys.argv[4]
sys.path.insert(0, str(root / "scripts"))
from source_registry import load_source_registry

source = load_source_registry(sources_file).get(source_name)
values = source.as_legacy_entry()
print(values.get(field, ""))
PY
}

REPO_URL="$(read_source_field repoUrl)"
UPSTREAM_REF="${UPSTREAM_REF:-$(read_source_field ref)}"
UPSTREAM_DIR="${ROOT_DIR}/.sources/${SOURCE_NAME}"
INIT_SUBMODULES="$(read_source_field initSubmodules)"

if [[ -z "${REPO_URL}" ]]; then
  echo "Source ${SOURCE_NAME} does not define repoUrl in ${SOURCES_FILE}" >&2
  exit 1
fi

if [[ ! -d "${UPSTREAM_DIR}/.git" ]]; then
  mkdir -p "$(dirname "${UPSTREAM_DIR}")"
  git clone "${REPO_URL}" "${UPSTREAM_DIR}" >&2
fi

if [[ -n "$(git -C "${UPSTREAM_DIR}" status --porcelain --untracked-files=no)" && "${FORCE:-0}" != "1" ]]; then
  echo "Source checkout is dirty: ${UPSTREAM_DIR}. Re-run with FORCE=1 if you want to continue." >&2
  exit 1
fi

if [[ -n "${UPSTREAM_REF}" ]]; then
  if ! git -C "${UPSTREAM_DIR}" rev-parse --verify --quiet "${UPSTREAM_REF}^{commit}" >/dev/null; then
    git -C "${UPSTREAM_DIR}" fetch origin "${UPSTREAM_REF}" >&2
  fi
  git -C "${UPSTREAM_DIR}" checkout --quiet --detach "${UPSTREAM_REF}" >&2
else
  git -C "${UPSTREAM_DIR}" fetch origin >&2
fi

if [[ "${INIT_SUBMODULES}" == "True" || "${INIT_SUBMODULES}" == "true" || "${INIT_SUBMODULES}" == "1" ]]; then
  git -C "${UPSTREAM_DIR}" submodule update --init --recursive >&2
fi

printf '%s\n' "${UPSTREAM_DIR}"
