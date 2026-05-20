#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/container-runtime.sh"
SOURCE_NAME="${SOURCE:-${SOURCE_NAME:-bitaxe}}"
VIRTUAL_PROFILE="${VIRTUAL_PROFILE:-gamma}"
if [[ -z "${OUT_DIR:-}" ]]; then
  if [[ "${SOURCE_NAME}" == "bitaxe" || "${SOURCE_NAME}" == "vanilla" ]]; then
    OUT_DIR="${ROOT_DIR}/out"
  else
    OUT_DIR="${ROOT_DIR}/out/${SOURCE_NAME}/${VIRTUAL_PROFILE}"
  fi
fi
STATE_DIR="${STATE_DIR:-${ROOT_DIR}/.state/${SOURCE_NAME}/${VIRTUAL_PROFILE}}"
UPSTREAM_DIR="${UPSTREAM_DIR:-${ROOT_DIR}/.sources/${SOURCE_NAME}}"
AXEOS_UI_DIR="${UPSTREAM_DIR}/main/http_server/axe-os"
IMAGE_NAME="${CONTAINER_IMAGE:-virtualaxe-dev}"
QEMU_FLASH_FILE="${OUT_DIR}/qemu_flash.bin"
NVS_FILE="${OUT_DIR}/nvs.bin"
STATE_NVS_FILE="${STATE_DIR}/nvs.bin"
NVS_OFFSET="$((0x9000))"
NVS_SIZE="$((0x6000))"
VIRTUAL_PROFILE_FILE="${VIRTUAL_PROFILE_FILE:-${ROOT_DIR}/configs/profiles/${VIRTUAL_PROFILE}.json}"
BUILD_PROGRESS_TOTAL=8
BUILD_PROGRESS_STEP=0
BUILD_PROGRESS_START_EPOCH="$(date +%s)"

build_progress_elapsed() {
  local now
  now="$(date +%s)"
  printf '%ss' "$((now - BUILD_PROGRESS_START_EPOCH))"
}

progress_phase() {
  BUILD_PROGRESS_STEP=$((BUILD_PROGRESS_STEP + 1))
  printf '[virtualAxe] [%s] %s/%s: %s\n' \
    "$(build_progress_elapsed)" \
    "${BUILD_PROGRESS_STEP}" \
    "${BUILD_PROGRESS_TOTAL}" \
    "$*"
}

progress_note() {
  printf '[virtualAxe] [%s] %s\n' "$(build_progress_elapsed)" "$*"
}

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

NVS_TEMPLATE_FILE="${NVS_TEMPLATE_FILE:-${ROOT_DIR}/$(source_field nvsTemplate)}"

POOL_HOST="${POOL_HOST:-public-pool.io}"
POOL_PORT="${POOL_PORT:-3333}"
POOL_USER="${POOL_USER:-1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa}"
POOL_PASS="${POOL_PASS:-x}"
POOL_DIFF="${POOL_DIFF:-0.0001}"
POOL_TLS="${POOL_TLS:-0}"
POOL_CERT="${POOL_CERT:-x}"
POOL_SUBSCRIBE_AGENT="${POOL_SUBSCRIBE_AGENT:-}"
HOSTNAME_VALUE="${HOSTNAME_VALUE:-virtualaxe}"
VIRTUAL_ASIC_MODE="${VIRTUAL_ASIC_MODE:-cpu}"

default_distinct_fallback_host() {
  local primary_host="$1"
  case "${primary_host}" in
    pool.bitronics.store)
      printf 'pool.nerdminers.org\n'
      ;;
    pool.nerdminers.org)
      printf 'public-pool.io\n'
      ;;
    *)
      printf 'pool.bitronics.store\n'
      ;;
  esac
}

default_distinct_fallback_port() {
  local primary_host="$1"
  case "${primary_host}" in
    public-pool.io)
      printf '3334\n'
      ;;
    *)
      printf '3333\n'
      ;;
  esac
}

default_pool_difficulty() {
  local host="$1"
  case "${host}" in
    public-pool.io)
      printf '0.0001\n'
      ;;
    pool.bitronics.store)
      printf '0.0001\n'
      ;;
    pool.nerdminers.org)
      printf '0.0005\n'
      ;;
    *)
      printf '1\n'
      ;;
  esac
}

default_pool_subscribe_agent() {
  local host="$1"
  case "${host}" in
    pool.bitronics.store|pool.nerdminers.org)
      printf 'NerdMinerV2/virtualAxe-gamma\n'
      ;;
    *)
      printf '\n'
      ;;
  esac
}

FALLBACK_POOL_HOST="${FALLBACK_POOL_HOST:-$(default_distinct_fallback_host "${POOL_HOST}")}"
FALLBACK_POOL_PORT="${FALLBACK_POOL_PORT:-$(default_distinct_fallback_port "${POOL_HOST}")}"
FALLBACK_POOL_USER="${FALLBACK_POOL_USER:-${POOL_USER}}"
FALLBACK_POOL_PASS="${FALLBACK_POOL_PASS:-${POOL_PASS}}"
FALLBACK_POOL_DIFF="${FALLBACK_POOL_DIFF:-$(default_pool_difficulty "${FALLBACK_POOL_HOST}")}"
FALLBACK_POOL_TLS="${FALLBACK_POOL_TLS:-${POOL_TLS}}"
FALLBACK_POOL_CERT="${FALLBACK_POOL_CERT:-${POOL_CERT}}"
FALLBACK_POOL_SUBSCRIBE_AGENT="${FALLBACK_POOL_SUBSCRIBE_AGENT:-$(default_pool_subscribe_agent "${FALLBACK_POOL_HOST}")}"

RESEED_NVS=0
RESEED_REASONS=()

register_reseed_override() {
  local name="$1"
  local default_value="$2"
  if [[ "${!name+x}" == "x" && "${!name}" != "${default_value}" ]]; then
    RESEED_NVS=1
    RESEED_REASONS+=("${name}")
  fi
}

register_reseed_override POOL_HOST "public-pool.io"
register_reseed_override POOL_PORT "3333"
register_reseed_override POOL_USER "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
register_reseed_override POOL_PASS "x"
register_reseed_override POOL_DIFF "0.0001"
register_reseed_override POOL_TLS "0"
register_reseed_override POOL_CERT "x"
register_reseed_override POOL_SUBSCRIBE_AGENT ""
register_reseed_override FALLBACK_POOL_HOST "$(default_distinct_fallback_host "${POOL_HOST}")"
register_reseed_override FALLBACK_POOL_PORT "$(default_distinct_fallback_port "${POOL_HOST}")"
register_reseed_override FALLBACK_POOL_USER "${POOL_USER}"
register_reseed_override FALLBACK_POOL_PASS "${POOL_PASS}"
register_reseed_override FALLBACK_POOL_DIFF "$(default_pool_difficulty "${FALLBACK_POOL_HOST}")"
register_reseed_override FALLBACK_POOL_TLS "${POOL_TLS}"
register_reseed_override FALLBACK_POOL_CERT "${POOL_CERT}"
register_reseed_override FALLBACK_POOL_SUBSCRIBE_AGENT "$(default_pool_subscribe_agent "${FALLBACK_POOL_HOST}")"
register_reseed_override HOSTNAME_VALUE "virtualaxe"
register_reseed_override VIRTUAL_ASIC_MODE "cpu"

RESET_PERSISTED_STATE="${RESET_PERSISTED_STATE:-0}"
case "${RESET_PERSISTED_STATE}" in
  0|1)
    ;;
  *)
    echo "Unsupported RESET_PERSISTED_STATE=${RESET_PERSISTED_STATE}. Use 0 or 1." >&2
    exit 1
    ;;
esac

if [[ "${RESET_PERSISTED_STATE}" == "1" ]]; then
  RESEED_NVS=1
  RESEED_REASONS+=("RESET_PERSISTED_STATE")
fi

workspace_path() {
  local path="$1"
  case "${path}" in
    "${ROOT_DIR}"/*)
      printf '/workspace%s\n' "${path#"${ROOT_DIR}"}"
      ;;
    *)
      echo "Path ${path} is outside ${ROOT_DIR} and cannot be mounted into the build container." >&2
      exit 1
      ;;
  esac
}

mkdir -p "${OUT_DIR}"
mkdir -p "${STATE_DIR}"
: > "${OUT_DIR}/build.log"
if [[ "${VIRTUALAXE_DISABLE_TEE:-0}" == "1" ]]; then
  exec >> "${OUT_DIR}/build.log" 2>&1
else
  exec > >(tee "${OUT_DIR}/build.log") 2>&1
fi

progress_phase "Preparing ${SOURCE_NAME}/${VIRTUAL_PROFILE} build output."

case "${VIRTUAL_ASIC_MODE}" in
  cpu)
    ;;
  *)
    echo "Unsupported VIRTUAL_ASIC_MODE=${VIRTUAL_ASIC_MODE}. Use cpu." >&2
    exit 1
    ;;
esac

virtualaxe_select_execution_environment

progress_phase "Using ${EXECUTION_MODE} build environment${CONTAINER_RUNTIME:+ via ${CONTAINER_RUNTIME}}."

if [[ ! -f "${VIRTUAL_PROFILE_FILE}" ]]; then
  echo "Virtual profile not found: ${VIRTUAL_PROFILE_FILE}" >&2
  exit 1
fi

ensure_container_image() {
  if "${CONTAINER_RUNTIME}" image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
    progress_note "Container image ${IMAGE_NAME} is already available."
    return
  fi

  progress_note "Building missing virtualAxe development image ${IMAGE_NAME} with ${CONTAINER_RUNTIME}."
  "${CONTAINER_RUNTIME}" build -t "${IMAGE_NAME}" -f "${ROOT_DIR}/docker/Dockerfile.dev" "${ROOT_DIR}"
}

install_axeos_frontend_dependencies() {
  local package_dir="$1"

  if [[ -d "${package_dir}/node_modules" ]]; then
    return
  fi

  echo "Installing AxeOS frontend dependencies in ${package_dir}."
  if command -v npm >/dev/null 2>&1; then
    if [[ -f "${package_dir}/package-lock.json" ]]; then
      npm ci --prefix "${package_dir}"
    else
      npm install --prefix "${package_dir}"
    fi
    return
  fi

  if [[ "${EXECUTION_MODE}" == "container" ]]; then
    local container_package_dir
    container_package_dir="$(workspace_path "${package_dir}")"
    "${CONTAINER_RUNTIME}" run --rm \
      -v "${ROOT_DIR}:/workspace" \
      -w /workspace \
      "${IMAGE_NAME}" \
      bash -lc '
        set -euo pipefail
        package_dir="$1"
        if [[ -f "${package_dir}/package-lock.json" ]]; then
          npm ci --prefix "${package_dir}"
        else
          npm install --prefix "${package_dir}"
        fi
      ' bash "${container_package_dir}"
    return
  fi

  echo "AxeOS frontend dependencies are missing in ${package_dir}/node_modules, and npm is not available." >&2
  echo "Install npm or use Docker/Podman so virtualAxe can provision them during the first build." >&2
  exit 1
}

if [[ "${EXECUTION_MODE}" == "container" ]]; then
  ensure_container_image
fi

if [[ "${PATCH_ALREADY_APPLIED:-0}" != "1" ]]; then
  progress_phase "Applying the ${SOURCE_NAME} patch stack."
  "${ROOT_DIR}/scripts/apply-patches.sh"
else
  progress_phase "Using already-patched ${SOURCE_NAME} worktree."
fi

SOURCE_AXEOS_UI_DIR="${ROOT_DIR}/.sources/${SOURCE_NAME}/main/http_server/axe-os"
progress_phase "Checking AxeOS frontend dependencies."
if [[ ! -d "${AXEOS_UI_DIR}/node_modules" && -d "${SOURCE_AXEOS_UI_DIR}/node_modules" ]]; then
  echo "Copying cached AxeOS frontend dependencies into ${AXEOS_UI_DIR}."
  cp -R "${SOURCE_AXEOS_UI_DIR}/node_modules" "${AXEOS_UI_DIR}/node_modules"
fi

if [[ ! -d "${AXEOS_UI_DIR}/node_modules" ]]; then
  install_axeos_frontend_dependencies "${AXEOS_UI_DIR}"
fi

ACTIVE_CSV="${OUT_DIR}/config-active.csv"
SDKCONFIG_OVERRIDE_FILE="${OUT_DIR}/sdkconfig.virtual.generated"
NVS_SEED_MODE="seeded"

progress_phase "Rendering virtual config, pool defaults, and sdkconfig overrides."
python3 "${ROOT_DIR}/scripts/render-virtual-config.py" \
  --template-csv "${NVS_TEMPLATE_FILE}" \
  --profile-json "${VIRTUAL_PROFILE_FILE}" \
  --output-csv "${ACTIVE_CSV}" \
  --output-sdkconfig "${SDKCONFIG_OVERRIDE_FILE}" \
  --hostname "${HOSTNAME_VALUE}" \
  --pool-host "${POOL_HOST}" \
  --pool-port "${POOL_PORT}" \
  --pool-user "${POOL_USER}" \
  --pool-pass "${POOL_PASS}" \
  --pool-diff "${POOL_DIFF}" \
  --pool-tls "${POOL_TLS}" \
  --pool-cert "${POOL_CERT}" \
  --pool-subscribe-agent "${POOL_SUBSCRIBE_AGENT}" \
  --fallback-pool-host "${FALLBACK_POOL_HOST}" \
  --fallback-pool-port "${FALLBACK_POOL_PORT}" \
  --fallback-pool-user "${FALLBACK_POOL_USER}" \
  --fallback-pool-pass "${FALLBACK_POOL_PASS}" \
  --fallback-pool-diff "${FALLBACK_POOL_DIFF}" \
  --fallback-pool-tls "${FALLBACK_POOL_TLS}" \
  --fallback-pool-cert "${FALLBACK_POOL_CERT}" \
  --fallback-pool-subscribe-agent "${FALLBACK_POOL_SUBSCRIBE_AGENT}" \
  --virtual-asic-mode "${VIRTUAL_ASIC_MODE}"

extract_existing_nvs_partition() {
  local flash_file="$1"
  local nvs_file="$2"

  python3 - "${flash_file}" "${nvs_file}" "${NVS_OFFSET}" "${NVS_SIZE}" <<'PY'
import sys
from pathlib import Path

flash_path = Path(sys.argv[1])
nvs_path = Path(sys.argv[2])
offset = int(sys.argv[3])
size = int(sys.argv[4])

blob = flash_path.read_bytes()
if len(blob) < offset + size:
    raise SystemExit(f"{flash_path} is too small to contain the NVS partition")

nvs_path.write_bytes(blob[offset:offset + size])
PY
}

can_migrate_existing_flash() {
  local manifest_file="${OUT_DIR}/manifest.json"

  if [[ ! -f "${QEMU_FLASH_FILE}" ]]; then
    return 1
  fi
  if [[ ! -f "${manifest_file}" ]]; then
    return 0
  fi

  python3 - "${manifest_file}" "${SOURCE_NAME}" "${VIRTUAL_PROFILE}" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.exit(0 if manifest.get("sourceName") == sys.argv[2] and manifest.get("virtualProfile") == sys.argv[3] else 1)
PY
}

sync_state_from_existing_flash() {
  if [[ "${RESEED_NVS}" != "0" ]]; then
    return
  fi

  if ! can_migrate_existing_flash; then
    return
  fi

  if [[ ! -f "${STATE_NVS_FILE}" || "${QEMU_FLASH_FILE}" -nt "${STATE_NVS_FILE}" ]]; then
    echo "Refreshing persisted virtualAxe settings from ${QEMU_FLASH_FILE} into ${STATE_NVS_FILE}."
    extract_existing_nvs_partition "${QEMU_FLASH_FILE}" "${STATE_NVS_FILE}"
  fi
}

sync_state_from_existing_flash

progress_phase "Preparing virtual NVS state."
if [[ -f "${STATE_NVS_FILE}" && "${RESEED_NVS}" == "0" ]]; then
  echo "Preserving persisted virtualAxe settings from ${STATE_NVS_FILE}."
  cp "${STATE_NVS_FILE}" "${NVS_FILE}"
  NVS_SEED_MODE="preserved"
elif [[ "${RESEED_NVS}" == "0" ]] && can_migrate_existing_flash; then
  echo "Migrating persisted virtualAxe settings from ${QEMU_FLASH_FILE} to ${STATE_NVS_FILE}."
  extract_existing_nvs_partition "${QEMU_FLASH_FILE}" "${NVS_FILE}"
  cp "${NVS_FILE}" "${STATE_NVS_FILE}"
  NVS_SEED_MODE="migrated"
else
  if [[ "${RESEED_NVS}" == "1" ]]; then
    echo "Reseeding virtualAxe settings from ${ACTIVE_CSV} because: ${RESEED_REASONS[*]}"
  else
    echo "Seeding virtualAxe settings from ${ACTIVE_CSV}."
  fi
fi

run_build_in_env() {
  local root_dir="$1"
  local upstream_dir="$2"
  local out_dir="$3"
  local nvs_seed_mode="$4"
  local sdkconfig_defaults="${upstream_dir}/sdkconfig.defaults;${root_dir}/configs/sdkconfig.virtual.defaults;${out_dir}/sdkconfig.virtual.generated"

  if [[ "${nvs_seed_mode}" == "seeded" ]]; then
    echo "[virtualAxe] Generating NVS partition."
    python3 "$IDF_PATH/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py" generate "${out_dir}/config-active.csv" "${out_dir}/nvs.bin" 0x6000
  else
    echo "Using preserved NVS partition at ${out_dir}/nvs.bin."
  fi
  cd "${upstream_dir}"
  export SDKCONFIG_DEFAULTS="${sdkconfig_defaults}"
  echo "[virtualAxe] Configuring ESP-IDF target esp32s3."
  idf.py set-target esp32s3
  echo "[virtualAxe] Building ESP-IDF firmware and AxeOS assets. This is the longest step."
  idf.py -D SDKCONFIG_DEFAULTS="${sdkconfig_defaults}" build
  echo "[virtualAxe] Copying firmware artifacts."
  cp build/esp-miner.bin "${out_dir}/esp-miner.bin"
  cp build/www.bin "${out_dir}/www.bin"
  echo "[virtualAxe] Merging QEMU flash image."
  esptool.py --chip esp32s3 merge_bin --flash_mode dio --flash_freq 80m --fill-flash-size 16MB \
    -o "${out_dir}/qemu_flash.bin" \
    0x0 build/bootloader/bootloader.bin \
    0x8000 build/partition_table/partition-table.bin \
    0x9000 "${out_dir}/nvs.bin" \
    0x10000 build/esp-miner.bin \
    0x410000 build/www.bin \
    0xf10000 build/ota_data_initial.bin
  python3 - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["IDF_PATH"]) / "tools"))
from idf_py_actions.qemu_ext import QEMU_TARGETS

Path(os.environ["VIRTUALAXE_QEMU_EFUSE_PATH"]).write_bytes(QEMU_TARGETS["esp32s3"].default_efuse)
PY
  python3 - "${out_dir}/tool-versions.json" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

def version(command):
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or result.stderr).strip().splitlines()[0]

Path(sys.argv[1]).write_text(json.dumps({
    "espIdf": version(["idf.py", "--version"]),
    "qemu": version(["qemu-system-xtensa", "--version"]),
}, indent=2) + "\n", encoding="utf-8")
PY
}

progress_phase "Building firmware and reusable QEMU flash image."
if [[ "${EXECUTION_MODE}" == "container" ]]; then
  CONTAINER_UPSTREAM_DIR="$(workspace_path "${UPSTREAM_DIR}")"
  CONTAINER_OUT_DIR="$(workspace_path "${OUT_DIR}")"
  CONTAINER_BUILD_ENV_ARGS=(
    -e VIRTUAL_ASIC_MODE="${VIRTUAL_ASIC_MODE}"
    -e VIRTUALAXE_QEMU_EFUSE_PATH="${CONTAINER_OUT_DIR}/qemu_efuse.bin"
  )
  if [[ -n "${BOARD:-}" ]]; then
    CONTAINER_BUILD_ENV_ARGS+=(-e BOARD="${BOARD}")
  fi

  "${CONTAINER_RUNTIME}" run --rm \
    -v "${ROOT_DIR}:/workspace" \
    -w /workspace \
    "${CONTAINER_BUILD_ENV_ARGS[@]}" \
    "${IMAGE_NAME}" \
    bash -lc '
      set -euo pipefail
      '"$(declare -f run_build_in_env)"'
      run_build_in_env /workspace "'"${CONTAINER_UPSTREAM_DIR}"'" "'"${CONTAINER_OUT_DIR}"'" "'"${NVS_SEED_MODE}"'"
    '
else
  export VIRTUALAXE_QEMU_EFUSE_PATH="${OUT_DIR}/qemu_efuse.bin"
  run_build_in_env "${ROOT_DIR}" "${UPSTREAM_DIR}" "${OUT_DIR}" "${NVS_SEED_MODE}"
fi

cp "${NVS_FILE}" "${STATE_NVS_FILE}"

progress_phase "Writing build manifest and artifact checksums."
python3 > "${OUT_DIR}/manifest.json" <<PY
import json
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path("${OUT_DIR}")
repo_root = Path("${ROOT_DIR}")
upstream_dir = Path("${UPSTREAM_DIR}")
state_root = Path("${STATE_DIR}")
source_name = "${SOURCE_NAME}"
profile_file = Path("${VIRTUAL_PROFILE_FILE}")
sdkconfig_override = root / "sdkconfig.virtual.generated"
active_config = root / "config-active.csv"
sys.path.insert(0, str(repo_root / "scripts"))
from source_registry import load_source_registry

registry = load_source_registry(repo_root / "configs" / "sources.json")
source = registry.get(source_name)
canonical_source_name = registry.canonical_name(source_name)
source_entry = source.as_legacy_entry()
series_file = source.patch_series_path
patch_dir = series_file.parent
configured_ref = source.ref

def sha256(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def git_output(args: list[str]) -> str:
    result = subprocess.run(["git", "-C", str(upstream_dir), *args], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()

series_names = [
    line.strip()
    for line in series_file.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]
patches = [
    {
        "file": name,
        "sha256": sha256(patch_dir / name),
    }
    for name in series_names
]
series_digest = hashlib.sha256()
series_digest.update(series_file.read_bytes())
for patch in patches:
    patch_path = patch_dir / patch["file"]
    series_digest.update(patch["file"].encode("utf-8"))
    series_digest.update(b"\0")
    series_digest.update(patch_path.read_bytes())

resolved_upstream_commit = git_output(["rev-parse", f"{configured_ref}^{{commit}}"])
if not resolved_upstream_commit and series_names:
    resolved_upstream_commit = git_output(["rev-parse", f"HEAD~{len(series_names)}"])
if not resolved_upstream_commit:
    resolved_upstream_commit = git_output(["rev-parse", "HEAD"])

tool_versions_path = root / "tool-versions.json"
try:
    tool_versions = json.loads(tool_versions_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    tool_versions = {}

artifacts = {}
for name in ["esp-miner.bin", "www.bin", "qemu_flash.bin", "nvs.bin", "qemu_efuse.bin", "tool-versions.json", "build.log"]:
    path = root / name
    artifacts[name] = {
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() else 0,
        "sha256": sha256(path),
    }
print(json.dumps({
    "sourceName": "${SOURCE_NAME}",
    "canonicalSourceName": canonical_source_name,
    "sourceDisplayName": source.display_name,
    "sourceRepoUrl": source.repo_url,
    "sourceReleaseTag": source.release_tag,
    "sourceSupportState": source.support_state,
    "sourceQemuMemoryMb": source.qemu_memory_mb,
    "configuredUpstreamRef": configured_ref,
    "resolvedUpstreamCommit": resolved_upstream_commit,
    "configuredResolvedCommit": source.resolved_commit,
    "patchSeriesPath": str(series_file.relative_to(repo_root)),
    "patchSeriesSha256": series_digest.hexdigest(),
    "patches": patches,
    "sourceBuildVars": source.build_vars,
    "virtualProfile": "${VIRTUAL_PROFILE}",
    "profileFileSha256": sha256(profile_file),
    "sdkconfigOverrideSha256": sha256(sdkconfig_override),
    "activeConfigCsvSha256": sha256(active_config),
    "executionMode": "${EXECUTION_MODE}",
    "containerImage": "${IMAGE_NAME}" if "${EXECUTION_MODE}" == "container" else "",
    "toolVersions": tool_versions,
    "buildTimestampUtc": datetime.now(timezone.utc).isoformat(),
    "virtualAsicMode": "${VIRTUAL_ASIC_MODE}",
    "nvsSeedMode": "${NVS_SEED_MODE}",
    "stateDir": str(state_root),
    "stateNvsSha256": sha256(state_root / "nvs.bin"),
    "upstreamDir": "${UPSTREAM_DIR}",
    "profileFile": "${VIRTUAL_PROFILE_FILE}",
    "poolHost": "${POOL_HOST}",
    "poolPort": int("${POOL_PORT}"),
    "poolUser": "${POOL_USER}",
    "poolDifficulty": float("${POOL_DIFF}"),
    "poolTLS": int("${POOL_TLS}"),
    "poolSubscribeAgent": "${POOL_SUBSCRIBE_AGENT}",
    "fallbackPoolHost": "${FALLBACK_POOL_HOST}",
    "fallbackPoolPort": int("${FALLBACK_POOL_PORT}"),
    "fallbackPoolUser": "${FALLBACK_POOL_USER}",
    "fallbackPoolDifficulty": float("${FALLBACK_POOL_DIFF}"),
    "fallbackPoolTLS": int("${FALLBACK_POOL_TLS}"),
    "fallbackPoolSubscribeAgent": "${FALLBACK_POOL_SUBSCRIBE_AGENT}",
    "artifacts": artifacts,
}, indent=2))
PY

progress_note "Build complete. QEMU image is ready."
