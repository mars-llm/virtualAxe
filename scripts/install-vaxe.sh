#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_BIN_DIR="${VAXE_BIN_DIR:-${HOME}/.local/bin}"
TARGET_PATH="${TARGET_BIN_DIR}/vaxe"

mkdir -p "${TARGET_BIN_DIR}"
ln -sfn "${ROOT_DIR}/vaxe" "${TARGET_PATH}"

echo "Installed vaxe at ${TARGET_PATH}"

case ":${PATH}:" in
  *":${TARGET_BIN_DIR}:"*)
    echo "vaxe is ready to use on your PATH."
    ;;
  *)
    echo "Add ${TARGET_BIN_DIR} to your PATH to use 'vaxe' directly."
    echo "Example:"
    echo "  export PATH=\"${TARGET_BIN_DIR}:\$PATH\""
    ;;
esac
