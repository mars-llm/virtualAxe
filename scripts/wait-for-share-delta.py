#!/usr/bin/env python3
import argparse
import html
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ACCEPTED_PATTERNS = (
    re.compile(r"message result accepted"),
    re.compile(r"accepted=True"),
)
CONNECT_PATTERN = re.compile(r"Connecting to: stratum\+tcp://([^:\s]+):([0-9]+)")
AUTHORIZE_PATTERN = re.compile(r'"method":\s*"mining\.authorize".*"params":\s*\["([^"]+)"')
SUBMIT_PATTERN = re.compile(r'"method":\s*"mining\.submit".*"params":\s*\["([^"]+)"')
LOCAL_DIFF_PATTERN = re.compile(r"asic_result: .* diff ([0-9]+(?:\.[0-9]+)?) of ([0-9]+(?:\.[0-9]+)?)\.")
BITRONICS_POOL_HOME_URL = "https://pool.bitronics.store/"
BITRONICS_API_TOKEN_PATTERN = re.compile(r"window\.POOL_API_TOKEN\s*=\s*['\"]([^'\"]+)['\"]")
PUBLIC_POOL_DIFF1_HASHES = 4294967296.0
POOL_STATS_QUALIFICATION_CAPABLE_KINDS = {"ckpool_shares"}
PROGRESS_INTERVAL_SECONDS = 30.0
API_UNAVAILABLE_FAIL_SECONDS = 120.0
POOL_STRATUM_IDENTITY_FAIL_SECONDS = 180.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wait for a live virtualAxe session to reach either an accepted-share milestone or a connected worker-session milestone."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--min-duration-seconds", type=float, default=0.0)
    parser.add_argument("--min-shares", type=int)
    parser.add_argument("--baseline-rejected", type=int)
    parser.add_argument("--max-rejected-delta", type=int)
    parser.add_argument("--expected-pool-host")
    parser.add_argument("--expected-pool-port", type=int)
    parser.add_argument("--expected-pool-worker")
    parser.add_argument("--min-worker-count", type=int, default=0)
    parser.add_argument("--require-worker-jobs", action="store_true")
    parser.add_argument("--require-accepted-share", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--qemu-log")
    parser.add_argument("--qemu-log-start", type=int, default=0)
    parser.add_argument("--require-accepted-log", action="store_true")
    parser.add_argument("--min-local-diff", type=float)
    parser.add_argument("--require-local-diff-at-least-pool-difficulty", action="store_true")
    parser.add_argument("--pool-stats-url")
    parser.add_argument("--pool-stats-page-url")
    parser.add_argument("--pool-stats-kind", choices=("ckpool_shares", "bitronics_status_evidence", "public_pool_bestdiff"), default="ckpool_shares")
    parser.add_argument("--pool-stats-auth", default="")
    parser.add_argument("--pool-stats-worker")
    parser.add_argument("--pool-stats-baseline-shares", type=float)
    parser.add_argument("--pool-stats-baseline-best-difficulty", type=float)
    parser.add_argument("--pool-stats-baseline-last-share")
    parser.add_argument("--pool-stats-min-delta", type=float)
    return parser.parse_args()


def get_text(url: str, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


def get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def bitronics_pool_headers() -> dict[str, str]:
    home = get_text(BITRONICS_POOL_HOME_URL)
    match = BITRONICS_API_TOKEN_PATTERN.search(home)
    if not match:
        raise RuntimeError("Unable to discover Bitronics public stats token")
    return {
        "X-Pool-Request": "internal",
        "X-Pool-Token": match.group(1),
    }


def read_log_since(path: str, start_offset: int) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        return ""

    with file_path.open("rb") as handle:
        handle.seek(max(0, start_offset))
        return handle.read().decode("utf-8", errors="replace")


def accepted_log_seen(log_text: str) -> bool:
    return any(pattern.search(log_text) for pattern in ACCEPTED_PATTERNS)


def qemu_accepted_share_proof(
    log_text: str,
    *,
    expected_host: str | None = None,
    expected_port: int | None = None,
    expected_worker: str | None = None,
) -> dict:
    connected = expected_host is None and expected_port is None
    worker_seen = expected_worker is None
    submit_seen = False
    submit_pending = 0
    accepted_count = 0

    for line in log_text.splitlines():
        connect_match = CONNECT_PATTERN.search(line)
        if connect_match:
            host = connect_match.group(1)
            port = int(connect_match.group(2))
            host_ok = expected_host is None or host == expected_host
            port_ok = expected_port is None or port == expected_port
            if host_ok and port_ok:
                connected = True

        authorize_match = AUTHORIZE_PATTERN.search(line)
        if authorize_match and (expected_worker is None or authorize_match.group(1) == expected_worker):
            worker_seen = True

        submit_match = SUBMIT_PATTERN.search(line)
        if submit_match and (expected_worker is None or submit_match.group(1) == expected_worker):
            submit_seen = True
            submit_pending += 1

        if submit_pending and "message result rejected" in line:
            submit_pending = max(0, submit_pending - 1)

        if submit_pending and accepted_log_seen(line):
            accepted_count += 1
            submit_pending = max(0, submit_pending - 1)

    pool_stratum_delta = accepted_count if connected and worker_seen else 0

    return {
        "qemuPoolIdentity": connected,
        "qemuWorkerIdentity": worker_seen,
        "qemuSubmitSeen": submit_seen,
        "qemuAcceptedShare": pool_stratum_delta > 0,
        "qemuAcceptedShareDelta": pool_stratum_delta,
        "poolStratumAcceptedShare": pool_stratum_delta > 0,
        "poolStratumAcceptedShareDelta": pool_stratum_delta,
        "poolStratumProofSource": "pool_stratum_response" if pool_stratum_delta > 0 else "",
        "poolStratumEvidenceTransport": "qemu_log" if pool_stratum_delta > 0 else "",
    }


def write_progress(status: str, payload: dict) -> None:
    progress = {"status": status, **payload}
    print(f"wait-for-share-delta progress: {json.dumps(progress, sort_keys=True)}", file=sys.stderr, flush=True)


def wait_progress_payload(
    last_payload: dict,
    *,
    elapsed_seconds: float,
    deadline: float,
    now: float,
    min_delta: float,
    qemu_proof: dict,
    pool_stats_delta: float,
    pool_stats_accepted: bool,
) -> dict:
    pool_stratum_delta = float(qemu_proof.get("poolStratumAcceptedShareDelta", 0) or 0)
    qualification_delta = max(pool_stratum_delta, pool_stats_delta if pool_stats_accepted else 0.0)
    return {
        "elapsedSeconds": round(elapsed_seconds, 2),
        "remainingSeconds": max(0.0, round(deadline - now, 2)),
        "requiredDurationSeconds": last_payload.get("requiredDurationSeconds", 0.0),
        "firmwareApiAcceptedShareDelta": last_payload.get("sharesAccepted"),
        "sharesRejectedDelta": last_payload.get("sharesRejectedDelta", 0),
        "poolDifficulty": last_payload.get("poolDifficulty"),
        "qemuPoolIdentity": bool(qemu_proof.get("qemuPoolIdentity", False)),
        "qemuWorkerIdentity": bool(qemu_proof.get("qemuWorkerIdentity", False)),
        "qemuSubmitSeen": bool(qemu_proof.get("qemuSubmitSeen", False)),
        "poolStratumAcceptedShareDelta": pool_stratum_delta,
        "poolStatsAcceptedShareDelta": pool_stats_delta,
        "poolStatsAccepted": pool_stats_accepted,
        "qualificationAcceptedShareDelta": qualification_delta,
        "requiredAcceptedShareDelta": min_delta,
    }


def pool_stats_worker(payload: dict, worker_name: str) -> dict | None:
    for worker in payload.get("worker", []) or []:
        if worker.get("workername") == worker_name:
            return worker
    return None


def pool_stats_worker_shares(payload: dict, worker_name: str) -> float:
    worker = pool_stats_worker(payload, worker_name)
    if not worker:
        return 0.0
    try:
        return float(worker.get("shares", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_bitronics_last_share(value: object) -> float:
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def worker_display_names(worker_name: str) -> set[str]:
    candidate_names = {worker_name}
    if "." in worker_name:
        candidate_names.add(worker_name.rsplit(".", 1)[1])
    return candidate_names


def bitronics_worker_active(page_text: str, worker_name: str) -> bool:
    candidate_names = worker_display_names(worker_name)
    for row in re.findall(r"<tr\b.*?</tr>", page_text, flags=re.IGNORECASE | re.DOTALL):
        row_text = html.unescape(re.sub(r"<[^>]+>", " ", row))
        compact_row = " ".join(row_text.split())
        if any(candidate in compact_row for candidate in candidate_names) and "status-indicator active" in row:
            return True
    return False


def public_pool_best_difficulty(worker: dict | None) -> float:
    if not worker:
        return 0.0
    try:
        return float(worker.get("bestDifficulty", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def public_pool_worker(payload: dict, worker_name: str) -> dict | None:
    candidate_names = worker_display_names(worker_name)
    workers = [
        worker
        for worker in payload.get("workers", []) or []
        if str(worker.get("name", "")) in candidate_names
    ]
    if not workers:
        return None
    return max(workers, key=public_pool_best_difficulty)


def public_pool_chart_accepted_count(payload: dict, pool_difficulty: float) -> float:
    if pool_difficulty <= 0:
        return 0.0
    total_difficulty = 0.0
    for point in payload.get("chartData", []) or []:
        try:
            hashrate = float(point.get("data", 0) or 0)
        except (TypeError, ValueError):
            continue
        total_difficulty += (hashrate * 600.0) / PUBLIC_POOL_DIFF1_HASHES
    return total_difficulty / pool_difficulty


def public_pool_bestdiff_share_delta(
    *,
    worker_active: bool,
    best_difficulty_before: float,
    best_difficulty_after: float,
    pool_difficulty: float,
) -> float:
    if not worker_active or pool_difficulty <= 0:
        return 0.0
    if best_difficulty_after <= best_difficulty_before:
        return 0.0
    if best_difficulty_after < pool_difficulty:
        return 0.0
    return 1.0


def bitronics_pool_stats(payload: dict) -> dict:
    data = payload.get("data", {}) or {}
    pools = data.get("pools", []) or []
    return next((pool for pool in pools if pool.get("pool") == "nerd"), {})


def max_local_diff(log_text: str) -> float | None:
    best: float | None = None
    for match in LOCAL_DIFF_PATTERN.finditer(log_text):
        diff = float(match.group(1))
        if best is None or diff > best:
            best = diff
    return best


def proven_local_diff_lower_bound(
    log_text: str,
    *,
    pool_difficulty: float,
    accepted_seen: bool,
) -> tuple[float | None, bool]:
    observed = max_local_diff(log_text)
    if observed is not None and observed >= pool_difficulty:
        return observed, False

    # The asic_result log rounds tiny low-difficulty shares to "diff 0.0",
    # so a real accepted submit is the honest lower-bound proof.
    if accepted_seen:
        return pool_difficulty, True

    return observed, False


def main() -> int:
    args = parse_args()
    if args.require_accepted_share and args.min_shares is None:
        raise SystemExit("--min-shares is required when --require-accepted-share is enabled")
    if bool(args.pool_stats_url) != bool(args.pool_stats_worker):
        raise SystemExit("--pool-stats-url and --pool-stats-worker must be provided together")
    info_url = f"{args.base_url.rstrip('/')}/api/system/info"
    started_at = time.time()
    deadline = time.time() + args.timeout
    last_payload: dict | None = None
    last_progress_at = 0.0
    first_api_failure_at: float | None = None
    first_missing_pool_identity_at: float | None = None

    while time.time() < deadline:
        now = time.time()
        try:
            payload = get_json(info_url)
        except Exception:
            if first_api_failure_at is None:
                first_api_failure_at = now
            if now - first_api_failure_at >= API_UNAVAILABLE_FAIL_SECONDS:
                raise SystemExit(
                    "API stayed unreadable while waiting for live pool proof. "
                    f"Elapsed without API: {round(now - first_api_failure_at, 2)} seconds"
                )
            time.sleep(1.0)
            continue
        first_api_failure_at = None

        elapsed_seconds = now - started_at
        last_payload = dict(payload)
        last_payload["durationSeconds"] = round(elapsed_seconds, 2)
        last_payload["requiredDurationSeconds"] = args.min_duration_seconds

        if args.baseline_rejected is not None:
            rejected_delta = max(0, int(last_payload.get("sharesRejected", 0) or 0) - args.baseline_rejected)
            last_payload["sharesRejectedDelta"] = rejected_delta
            if args.max_rejected_delta is not None and rejected_delta > args.max_rejected_delta:
                raise SystemExit(
                    "Rejected share delta exceeded the requested limit. "
                    f"Last payload: {json.dumps(last_payload, sort_keys=True)}"
                )

        if args.expected_pool_host and last_payload.get("stratumURL") != args.expected_pool_host:
            time.sleep(1.0)
            continue
        if args.expected_pool_port is not None and int(last_payload.get("stratumPort", 0)) != args.expected_pool_port:
            time.sleep(1.0)
            continue

        workers = last_payload.get("virtualAsicWorkers", [])
        if len(workers) < args.min_worker_count:
            time.sleep(1.0)
            continue
        if args.require_worker_jobs and any(int(worker.get("jobsAssigned", 0)) <= 0 for worker in workers[: args.min_worker_count]):
            time.sleep(1.0)
            continue

        log_text = ""
        if args.qemu_log:
            log_text = read_log_since(args.qemu_log, args.qemu_log_start)

        qemu_proof = qemu_accepted_share_proof(
            log_text,
            expected_host=args.expected_pool_host,
            expected_port=args.expected_pool_port,
            expected_worker=args.expected_pool_worker,
        ) if args.qemu_log else {}
        if (
            args.pool_stats_min_delta is not None
            and args.qemu_log
            and args.expected_pool_host
            and not qemu_proof.get("qemuPoolIdentity")
        ):
            if first_missing_pool_identity_at is None:
                first_missing_pool_identity_at = now
            if now - first_missing_pool_identity_at >= POOL_STRATUM_IDENTITY_FAIL_SECONDS:
                raise SystemExit(
                    "No verified live Stratum connection to the expected pool appeared in the QEMU log. "
                    f"Expected {args.expected_pool_host}:{args.expected_pool_port}; "
                    f"elapsed without identity: {round(now - first_missing_pool_identity_at, 2)} seconds"
                )
        else:
            first_missing_pool_identity_at = None
        accepted_seen = bool(qemu_proof.get("qemuAcceptedShare")) if args.qemu_log else False
        pool_stratum_accepted_delta = int(qemu_proof.get("poolStratumAcceptedShareDelta", 0) or 0)

        pool_stats_accepted = False
        pool_stats_delta = 0.0
        if args.pool_stats_url and args.pool_stats_worker:
            try:
                pool_headers = bitronics_pool_headers() if args.pool_stats_auth == "bitronics" else {}
                pool_stats_payload = get_json(args.pool_stats_url, pool_headers)
                pool_stats_baseline = float(args.pool_stats_baseline_shares or 0.0)
                worker_stats = pool_stats_worker(pool_stats_payload, args.pool_stats_worker) or {}
                pool_stats_shares = pool_stats_worker_shares(pool_stats_payload, args.pool_stats_worker)
                pool_stats_delta = max(0.0, pool_stats_shares - pool_stats_baseline)

                last_share_before = args.pool_stats_baseline_last_share or ""
                last_share_after = worker_stats.get("lastshare")
                worker_active = bool(worker_stats)
                if args.pool_stats_kind == "bitronics_status_evidence":
                    worker_stats = bitronics_pool_stats(pool_stats_payload)
                    last_share_after = worker_stats.get("lastShare")
                    last_share_delta = parse_bitronics_last_share(last_share_after) - parse_bitronics_last_share(last_share_before)
                    worker_active = False
                    if args.pool_stats_page_url:
                        worker_active = bitronics_worker_active(get_text(args.pool_stats_page_url, pool_headers), args.pool_stats_worker)
                    pool_stats_delta = 0.0
                    pool_stats_shares = pool_stats_baseline
                    last_payload["poolStatsEvidenceOnly"] = True
                    last_payload["poolStatsLastShareDeltaSeconds"] = last_share_delta
                elif args.pool_stats_kind == "public_pool_bestdiff":
                    worker_stats = public_pool_worker(pool_stats_payload, args.pool_stats_worker) or {}
                    best_difficulty_before = float(args.pool_stats_baseline_best_difficulty or 0.0)
                    best_difficulty_after = public_pool_best_difficulty(worker_stats)
                    worker_active = bool(worker_stats)
                    chart_accepted_count = 0.0
                    if args.pool_stats_page_url:
                        chart_accepted_count = public_pool_chart_accepted_count(
                            get_json(args.pool_stats_page_url, pool_headers),
                            float(last_payload.get("poolDifficulty") or 0),
                        )
                    bestdiff_delta = public_pool_bestdiff_share_delta(
                        worker_active=worker_active,
                        best_difficulty_before=best_difficulty_before,
                        best_difficulty_after=best_difficulty_after,
                        pool_difficulty=float(last_payload.get("poolDifficulty") or 0),
                    )
                    pool_stats_delta = max(chart_accepted_count if bestdiff_delta else 0.0, bestdiff_delta)
                    pool_stats_shares = pool_stats_baseline + pool_stats_delta
                    last_share_after = worker_stats.get("lastSeen")
                    last_payload["poolStatsBestDifficultyBefore"] = best_difficulty_before
                    last_payload["poolStatsBestDifficultyAfter"] = best_difficulty_after
                    last_payload["poolStatsBestDifficultyReachedTarget"] = bool(bestdiff_delta)

                last_payload["poolStatsURL"] = args.pool_stats_url
                last_payload["poolStatsWorker"] = args.pool_stats_worker
                last_payload["poolStatsProofKind"] = args.pool_stats_kind
                last_payload["poolStatsSharesBefore"] = pool_stats_baseline
                last_payload["poolStatsSharesAfter"] = pool_stats_shares
                last_payload["poolStatsAcceptedShareDelta"] = pool_stats_delta
                last_payload["poolStatsWorkerActive"] = worker_active
                last_payload["poolStatsQualificationCapable"] = (
                    args.pool_stats_kind in POOL_STATS_QUALIFICATION_CAPABLE_KINDS
                )
                if last_share_before:
                    last_payload["poolStatsLastShareBefore"] = last_share_before
                if last_share_after:
                    last_payload["poolStatsLastShareAfter"] = last_share_after
                if args.pool_stats_min_delta is not None:
                    pool_stats_accepted = (
                        args.pool_stats_kind in POOL_STATS_QUALIFICATION_CAPABLE_KINDS
                        and pool_stats_delta >= float(args.pool_stats_min_delta)
                    )
                last_payload["poolStatsAccepted"] = pool_stats_accepted
            except Exception as exc:  # noqa: BLE001
                last_payload["poolStatsError"] = str(exc)

        if now - last_progress_at >= PROGRESS_INTERVAL_SECONDS:
            write_progress(
                "waiting",
                wait_progress_payload(
                    last_payload,
                    elapsed_seconds=elapsed_seconds,
                    deadline=deadline,
                    now=now,
                    min_delta=float(args.pool_stats_min_delta or args.min_shares or 1),
                    qemu_proof=qemu_proof,
                    pool_stats_delta=pool_stats_delta,
                    pool_stats_accepted=pool_stats_accepted,
                ),
            )
            last_progress_at = now

        pool_stats_required = args.pool_stats_min_delta is not None
        pool_side_accepted = (
            pool_stratum_accepted_delta >= float(args.pool_stats_min_delta or 1)
            or pool_stats_accepted
        )
        local_accepted = True
        if args.require_accepted_share:
            local_accepted = int(last_payload.get("sharesAccepted", 0)) >= int(args.min_shares)
            if pool_stats_required and not pool_side_accepted:
                time.sleep(1.0)
                continue
            if not pool_stats_required and not local_accepted and not pool_stats_accepted and not accepted_seen:
                time.sleep(1.0)
                continue

        if args.require_accepted_log and not accepted_seen:
            time.sleep(1.0)
            continue

        accepted_proven = pool_side_accepted if pool_stats_required else accepted_seen or pool_stats_accepted or local_accepted
        pool_difficulty = float(last_payload.get("poolDifficulty") or 0)
        observed_local_diff = max_local_diff(log_text) if args.qemu_log else None
        proven_local_diff, inferred_from_acceptance = proven_local_diff_lower_bound(
            log_text,
            pool_difficulty=pool_difficulty,
            accepted_seen=accepted_proven,
        ) if args.qemu_log else (None, False)

        if args.min_local_diff is not None and (proven_local_diff is None or proven_local_diff < args.min_local_diff):
            time.sleep(1.0)
            continue
        if args.require_local_diff_at_least_pool_difficulty:
            if proven_local_diff is None or proven_local_diff < pool_difficulty:
                time.sleep(1.0)
                continue

        if elapsed_seconds < args.min_duration_seconds:
            time.sleep(1.0)
            continue

        enriched_payload = dict(last_payload)
        enriched_payload.update(qemu_proof)
        if observed_local_diff is not None:
            enriched_payload["observedLocalDiff"] = observed_local_diff
        if proven_local_diff is not None:
            enriched_payload["provenLocalDiffLowerBound"] = proven_local_diff
        if inferred_from_acceptance:
            enriched_payload["localDiffSatisfiedByAcceptance"] = True
        if args.require_accepted_share:
            if pool_stats_required:
                proof_sources = []
                if pool_stratum_accepted_delta >= float(args.pool_stats_min_delta or 1):
                    proof_sources.append("pool_stratum_response")
                if pool_stats_accepted:
                    proof_sources.append("pool_stats")
                enriched_payload["acceptedShareProofSources"] = proof_sources
                enriched_payload["acceptedShareProofSource"] = proof_sources[0] if proof_sources else ""
                enriched_payload["qualificationProofSources"] = proof_sources
                enriched_payload["qualificationProofSource"] = proof_sources[0] if proof_sources else ""
                enriched_payload["qualificationAcceptedShareDelta"] = max(
                    pool_stratum_accepted_delta,
                    float(enriched_payload.get("poolStatsAcceptedShareDelta", 0.0) or 0.0) if pool_stats_accepted else 0.0,
                )
            elif accepted_seen:
                enriched_payload["acceptedShareProofSource"] = "pool_stratum_response"
                enriched_payload["acceptedShareProofSources"] = ["pool_stratum_response"]
                enriched_payload["evidenceTransport"] = "qemu_log"
            elif pool_stats_accepted:
                enriched_payload["acceptedShareProofSource"] = "pool_stats"
            else:
                enriched_payload["acceptedShareProofSource"] = "firmware_api"
        if args.require_accepted_log:
            enriched_payload["acceptedLogSeen"] = True
        elif accepted_seen:
            enriched_payload["acceptedLogSeen"] = True
        print(json.dumps(enriched_payload, indent=2, sort_keys=True))
        return 0

    raise SystemExit(
        "Timed out waiting for the requested live pool session state. "
        f"Last payload: {json.dumps(last_payload, sort_keys=True) if last_payload is not None else 'none'}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
