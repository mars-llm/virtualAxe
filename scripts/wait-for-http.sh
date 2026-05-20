#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:${HTTP_PORT:-18080}}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-120}"
STABLE_SUCCESS_COUNT="${STABLE_SUCCESS_COUNT:-3}"
CURL_MAX_TIME_SECONDS="${CURL_MAX_TIME_SECONDS:-5}"

consecutive_successes=0
last_error=""

for ((i = 0; i < TIMEOUT_SECONDS; i++)); do
  if last_error="$(curl -fsS --max-time "${CURL_MAX_TIME_SECONDS}" "${BASE_URL}/api/system/info" 2>&1 >/dev/null)"; then
    consecutive_successes=$((consecutive_successes + 1))
    if (( consecutive_successes >= STABLE_SUCCESS_COUNT )); then
      exit 0
    fi
  else
    consecutive_successes=0
  fi
  sleep 1
done

echo "Timed out waiting for ${BASE_URL}/api/system/info to respond ${STABLE_SUCCESS_COUNT} time(s) in a row" >&2
if [[ -n "${last_error}" ]]; then
  echo "Last readiness error: ${last_error}" >&2
fi
exit 1
