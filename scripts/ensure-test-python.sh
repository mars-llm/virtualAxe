#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
HOST_PYTHON="${HOST_PYTHON:-python3}"

if command -v uv >/dev/null 2>&1 && [[ -f "${ROOT_DIR}/pyproject.toml" && -f "${ROOT_DIR}/uv.lock" ]]; then
  cd "${ROOT_DIR}"
  uv sync --frozen --no-install-project
  exit 0
fi

if ! command -v "${HOST_PYTHON}" >/dev/null 2>&1; then
  echo "Required host Python interpreter not found: ${HOST_PYTHON}" >&2
  exit 1
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  "${HOST_PYTHON}" -m venv "${VENV_DIR}"
fi

if ! "${VENV_DIR}/bin/python" - <<'PY' >/dev/null 2>&1
import importlib.util

required = ("pytest", "requests", "rich", "textual")
missing = [name for name in required if importlib.util.find_spec(name) is None]
raise SystemExit(0 if not missing else 1)
PY
then
  "${VENV_DIR}/bin/python" -m pip install --upgrade pip
  "${VENV_DIR}/bin/python" -m pip install \
    pytest==9.0.3 \
    requests==2.33.1 \
    rich==15.0.0 \
    textual==8.2.4
fi
