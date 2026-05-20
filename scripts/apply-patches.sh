#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_NAME="${SOURCE:-${SOURCE_NAME:-bitaxe}}"
UPSTREAM_DIR="${SOURCE_DIR:-${ROOT_DIR}/.sources/${SOURCE_NAME}}"
SOURCES_FILE="${ROOT_DIR}/configs/sources.json"
PATCH_BRANCH="${PATCH_BRANCH:-virtualaxe-worktree}"
PATCH_TARGET_DIR="${PATCH_TARGET_DIR:-}"
export GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-virtualAxe patch check}"
export GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-virtualaxe@example.invalid}"

resolve_path() {
  python3 - "$1" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).expanduser().resolve(strict=False))
PY
}

path_within() {
  python3 - "$1" "$2" <<'PY'
import sys
from pathlib import Path

target = Path(sys.argv[1])
parent = Path(sys.argv[2])
try:
    target.relative_to(parent)
except ValueError:
    raise SystemExit(1)
raise SystemExit(0)
PY
}

refuse_patch_target() {
  echo "Refusing unsafe PATCH_TARGET_DIR=${PATCH_TARGET_DIR:-<empty>}: $1" >&2
  exit 1
}

validate_patch_target_dir() {
  if [[ -z "${PATCH_TARGET_DIR}" ]]; then
    refuse_patch_target "set PATCH_TARGET_DIR to a disposable virtualAxe worktree path"
  fi

  local target_resolved
  local root_resolved
  local home_resolved
  local worktrees_resolved
  local tmp_resolved
  local slash_tmp_resolved
  target_resolved="$(resolve_path "${PATCH_TARGET_DIR}")"
  root_resolved="$(resolve_path "${ROOT_DIR}")"
  home_resolved="$(resolve_path "${HOME}")"
  worktrees_resolved="$(resolve_path "${ROOT_DIR}/.worktrees")"
  tmp_resolved="$(resolve_path "${TMPDIR:-/tmp}")"
  slash_tmp_resolved="$(resolve_path "/tmp")"

  case "${target_resolved}" in
    /|"${root_resolved}"|"${home_resolved}")
      refuse_patch_target "target resolves to a protected directory (${target_resolved})"
      ;;
  esac

  local protected_tree
  for protected_tree in \
    "${root_resolved}/.git" \
    "${root_resolved}/.sources" \
    "${root_resolved}/.state" \
    "${root_resolved}/configs" \
    "${root_resolved}/docs" \
    "${root_resolved}/out" \
    "${root_resolved}/patches" \
    "${root_resolved}/scripts" \
    "${root_resolved}/tests"; do
    if [[ "${target_resolved}" == "${protected_tree}" ]] || path_within "${target_resolved}" "${protected_tree}"; then
      refuse_patch_target "target resolves inside protected repository state (${protected_tree})"
    fi
  done

  if [[ "${target_resolved}" != "${worktrees_resolved}" ]] && path_within "${target_resolved}" "${worktrees_resolved}"; then
    printf '%s\n' "${target_resolved}"
    return
  fi

  local target_parent
  local target_name
  target_parent="$(dirname "${target_resolved}")"
  target_name="$(basename "${target_resolved}")"
  if [[ "${target_name}" == virtualaxe-* ]] && {
    [[ "${target_parent}" == "${tmp_resolved}" ]] || [[ "${target_parent}" == "${slash_tmp_resolved}" ]]
  }; then
    printf '%s\n' "${target_resolved}"
    return
  fi

  if [[ "${VIRTUALAXE_CONFIRM_PATCH_TARGET_DELETE:-0}" == "1" ]]; then
    printf '%s\n' "${target_resolved}"
    return
  fi

  refuse_patch_target "use a repo .worktrees/* target, a /tmp/virtualaxe-* target, or set VIRTUALAXE_CONFIRM_PATCH_TARGET_DELETE=1 for a custom disposable path"
}

read_source_ref() {
  read_source_field ref
}

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

UPSTREAM_REF="${UPSTREAM_REF:-$(read_source_ref)}"
PATCH_SERIES_FILE="${PATCH_SERIES_FILE:-${ROOT_DIR}/$(read_source_field patchSeries)}"
INIT_SUBMODULES="$(read_source_field initSubmodules)"
SERIES_FILE="${PATCH_SERIES_FILE}"
PATCH_DIR="$(dirname "${SERIES_FILE}")"

PATCH_TARGET_DIR="$(validate_patch_target_dir)"

if [[ -z "${SOURCE_DIR:-}" ]]; then
  UPSTREAM_DIR="$("${ROOT_DIR}/scripts/sync-upstream.sh")"
fi

if [[ ! -d "${PATCH_DIR}" ]]; then
  echo "Patch directory not found: ${PATCH_DIR}" >&2
  exit 1
fi

if [[ -n "${PATCH_TARGET_DIR}" ]]; then
  echo "Replacing patch target: ${PATCH_TARGET_DIR}" >&2
  rm -rf "${PATCH_TARGET_DIR}"
  git clone --no-local "${UPSTREAM_DIR}" "${PATCH_TARGET_DIR}" >/dev/null
  UPSTREAM_DIR="${PATCH_TARGET_DIR}"
  git -C "${UPSTREAM_DIR}" checkout --detach "${UPSTREAM_REF}" >/dev/null
  if [[ "${INIT_SUBMODULES}" == "True" || "${INIT_SUBMODULES}" == "true" || "${INIT_SUBMODULES}" == "1" ]]; then
    git -C "${UPSTREAM_DIR}" submodule update --init --recursive >/dev/null
  fi
fi

if [[ ! -f "${SERIES_FILE}" ]]; then
  echo "Patch series file not found: ${SERIES_FILE}" >&2
  exit 1
fi

git -C "${UPSTREAM_DIR}" am --abort >/dev/null 2>&1 || true

while IFS= read -r patch_name; do
  [[ -z "${patch_name}" ]] && continue
  [[ "${patch_name}" == \#* ]] && continue

  patch="${PATCH_DIR}/${patch_name}"
  if [[ ! -f "${patch}" ]]; then
    echo "Patch listed in series not found: ${patch}" >&2
    exit 1
  fi

  echo "Applying $(basename "${patch}")"
  if ! git -C "${UPSTREAM_DIR}" am --3way "${patch}"; then
    git -C "${UPSTREAM_DIR}" am --abort || true
    echo "Failed to apply $(basename "${patch}")" >&2
    exit 1
  fi
done < "${SERIES_FILE}"
