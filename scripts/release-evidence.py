#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_registry import load_source_registry

SOURCES_FILE = ROOT_DIR / "configs" / "sources.json"
DEFAULT_OUT_DIR = ROOT_DIR / "out" / "release-evidence"
DEFAULT_REQUIRED_POOLS = ["Bitronics", "Nerdminers"]
ACCEPTED_PROOF_SOURCES = {
    "firmware_api": "diagnostic firmware/API accepted-share evidence",
    "pool_stratum_response": "direct remote pool Stratum accepted-response proof",
    "pool_stats": "delayed worker-bound pool stats accepted-share proof",
    "qemu_log": "QEMU log transport only; not a qualification proof source",
}
QUALIFICATION_PROOF_SOURCES = {"pool_stratum_response", "pool_stats"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_output(root: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def repo_relative(root: Path, value: str) -> str:
    if not value:
        return ""
    path = Path(value)
    if not path.is_absolute():
        return value
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return value


def report_relative(root: Path, report_root: Path, value: str) -> str:
    """Return repo-relative paths, or report-root-relative paths for copied evidence."""
    path = Path(value)
    repo_path = repo_relative(root, value)
    if repo_path != value or not path.is_absolute():
        return repo_path
    try:
        return str(path.resolve().relative_to(report_root.resolve()))
    except ValueError:
        return path.name


def series(root: Path, patch_series: str) -> list[str]:
    series_file = root / patch_series
    return [
        line.strip()
        for line in series_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def patch_series_hash(root: Path, patch_series: str, patch_names: list[str]) -> str:
    series_file = root / patch_series
    patch_dir = series_file.parent
    digest = hashlib.sha256()
    digest.update(series_file.read_bytes())
    for name in patch_names:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((patch_dir / name).read_bytes())
    return digest.hexdigest()


def patch_inventory(root: Path, patch_series: str) -> dict[str, Any]:
    series_file = root / patch_series
    patch_dir = series_file.parent
    patch_names = series(root, patch_series)
    return {
        "patchCount": len(patch_names),
        "patchSeries": patch_series,
        "seriesSha256": patch_series_hash(root, patch_series, patch_names),
        "patches": [
            {
                "filename": name,
                "sha256": sha256_file(patch_dir / name),
            }
            for name in patch_names
        ],
    }


def configured_source(root: Path, source_name: str | None = None) -> dict[str, Any]:
    registry = load_source_registry(root / "configs" / "sources.json")
    source = registry.get(source_name)
    source_name = source.name
    source_dir = root / ".sources" / source_name
    resolved = source.resolved_commit
    if source_dir.exists():
        resolved = git_output(root, ["-C", str(source_dir), "rev-parse", f"{source.ref}^{{commit}}"]) or source.resolved_commit
    return {
        "name": source_name,
        "repoUrl": source.repo_url,
        "configuredRef": source.ref,
        "configuredResolvedCommit": source.resolved_commit,
        "releaseTag": source.release_tag,
        "patchSeries": source.patch_series,
        "resolvedCommit": resolved,
    }


def latest_summary(root: Path) -> Path:
    summaries = list((root / "out" / "release-matrix").glob("*/summary.json"))
    if not summaries:
        raise SystemExit("No release verifier summaries found under out/release-matrix/. Run make verify-release first.")
    return max(summaries, key=lambda path: path.stat().st_mtime)


def load_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def phase_evidence(root: Path, report_root: Path, phase: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "apiBeforePath",
        "apiAfterPath",
        "waitResultPath",
        "qemuLogPath",
        "poolStatsBeforePath",
        "poolStatsAfterPath",
        "runtimeOutDir",
        "runtimeStateDir",
    ]
    return {key: report_relative(root, report_root, str(phase.get(key, ""))) for key in keys if phase.get(key)}


def live_phase_summary(root: Path, report_root: Path, phase: dict[str, Any]) -> dict[str, Any]:
    proof_source = phase.get("acceptedShareProofSource")
    qualification_proof_source = phase.get("qualificationProofSource", "")
    qualification_proof_sources = phase.get("qualificationProofSources") or (
        [qualification_proof_source] if qualification_proof_source else []
    )
    return {
        "phase": phase.get("phase"),
        "label": phase.get("label"),
        "status": phase.get("phaseStatus"),
        "poolHost": phase.get("poolHost"),
        "poolPort": phase.get("poolPort"),
        "assignedPoolDifficulty": phase.get("assignedPoolDifficulty"),
        "acceptedShareDelta": phase.get("acceptedShareDelta"),
        "diagnosticAcceptedShareDelta": phase.get("diagnosticAcceptedShareDelta", phase.get("acceptedShareDelta")),
        "acceptedShareProofSource": proof_source,
        "acceptedShareProofMeaning": ACCEPTED_PROOF_SOURCES.get(str(proof_source), ""),
        "qualificationAcceptedShareDelta": phase.get("qualificationAcceptedShareDelta"),
        "qualificationProofSource": qualification_proof_source,
        "qualificationProofSources": qualification_proof_sources,
        "qualificationProofMeaning": ACCEPTED_PROOF_SOURCES.get(str(qualification_proof_source), ""),
        "qualificationPoolSideRequired": phase.get("qualificationPoolSideRequired", False),
        "qualificationPoolStatsRequired": phase.get("qualificationPoolStatsRequired", False),
        "poolStratumAcceptedShareDelta": phase.get("poolStratumAcceptedShareDelta", 0),
        "poolStratumAcceptedShare": phase.get("poolStratumAcceptedShare", phase.get("qemuAcceptedShare", False)),
        "poolStratumEvidenceTransport": phase.get("poolStratumEvidenceTransport", phase.get("evidenceTransport", "")),
        "qemuAcceptedShareDelta": phase.get("qemuAcceptedShareDelta", 0),
        "qemuPoolIdentity": phase.get("qemuPoolIdentity"),
        "qemuWorkerIdentity": phase.get("qemuWorkerIdentity"),
        "qemuSubmitSeen": phase.get("qemuSubmitSeen"),
        "qemuAcceptedShare": phase.get("qemuAcceptedShare"),
        "localAcceptedShareDelta": phase.get("localAcceptedShareDelta", 0),
        "poolStatsAcceptedShareDelta": phase.get("poolStatsAcceptedShareDelta", 0),
        "poolStatsProofKind": phase.get("poolStatsProofKind", ""),
        "poolStatsWorkerBound": phase.get("poolStatsWorkerBound"),
        "poolStatsAcceptedShareCounter": phase.get("poolStatsAcceptedShareCounter"),
        "poolStatsRejectedShareCounter": phase.get("poolStatsRejectedShareCounter"),
        "poolStatsSupportsDelta": phase.get("poolStatsSupportsDelta"),
        "poolStatsQualificationCapable": phase.get("poolStatsQualificationCapable"),
        "poolStatsQualificationCapability": phase.get("poolStatsQualificationCapability", ""),
        "requiredAcceptedShareDelta": phase.get("requiredAcceptedShareDelta"),
        "rejectedShareDelta": phase.get("rejectedShareDelta"),
        "jobsAssigned": phase.get("jobsAssigned", []),
        "phaseDurationSeconds": phase.get("phaseDurationSeconds"),
        "phaseTimeoutSeconds": phase.get("phaseTimeoutSeconds"),
        "evidence": phase_evidence(root, report_root, phase),
    }


def live_verification(root: Path, summary_path: Path, summary: dict[str, Any]) -> dict[str, Any]:
    profile = (summary.get("profiles") or [{}])[0]
    phases = profile.get("phases", []) or []
    report_root = summary_path.parent
    return {
        "summaryPath": report_relative(root, report_root, str(summary_path)),
        "summaryMarkdownPath": report_relative(root, report_root, str(summary_path.with_suffix(".md"))),
        "outputDir": report_relative(root, report_root, str(summary.get("outputDir", ""))),
        "runId": summary.get("runId", ""),
        "source": summary.get("source", ""),
        "mode": summary.get("mode", ""),
        "status": summary.get("status", ""),
        "poolUser": summary.get("poolUser", ""),
        "releaseGate": summary.get("releaseGate", {}),
        "profile": profile.get("profile", ""),
        "profileStatus": profile.get("status", ""),
        "releaseGateStatus": profile.get("releaseGateStatus", ""),
        "blockingFailures": profile.get("blockingFailures", []),
        "durationSeconds": profile.get("durationSeconds"),
        "phases": [live_phase_summary(root, report_root, phase) for phase in phases],
    }


def live_release_blockers(live: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if live["status"] != "passed" or live["releaseGateStatus"] != "PASSED":
        blockers.append("latest live release verifier summary is not passed")

    required_pools = live.get("releaseGate", {}).get("requiredPools") or DEFAULT_REQUIRED_POOLS
    phases_by_label = {phase.get("label"): phase for phase in live["phases"]}
    missing = [pool for pool in required_pools if pool not in phases_by_label]
    if missing:
        blockers.append(f"latest live release verifier summary is missing required pool evidence: {', '.join(missing)}")

    for pool in required_pools:
        phase = phases_by_label.get(pool)
        if not phase:
            continue
        if phase.get("status") != "PASSED":
            blockers.append(f"required live pool phase did not pass: {pool}")
        if live.get("mode") == "qualification":
            required_delta = phase.get("requiredAcceptedShareDelta")
            qualification_delta = phase.get("qualificationAcceptedShareDelta")
            proof_sources = set(phase.get("qualificationProofSources") or [])
            if phase.get("qualificationProofSource"):
                proof_sources.add(phase.get("qualificationProofSource"))
            supported_proof_sources = proof_sources & QUALIFICATION_PROOF_SOURCES
            if not supported_proof_sources:
                blockers.append(f"qualification pool phase lacks pool-side accepted-share proof: {pool}")
            if "pool_stratum_response" in proof_sources:
                if not isinstance(phase.get("poolStratumAcceptedShareDelta"), (int, float)):
                    blockers.append(f"qualification pool phase is missing Stratum accepted-response delta: {pool}")
                if phase.get("poolStratumAcceptedShareDelta", 0) < (required_delta or 0):
                    blockers.append(f"qualification pool phase has insufficient Stratum accepted-response delta: {pool}")
                for key, label in (
                    ("qemuPoolIdentity", "pool identity"),
                    ("qemuWorkerIdentity", "worker identity"),
                    ("qemuSubmitSeen", "submit-before-acceptance"),
                    ("qemuAcceptedShare", "accepted response"),
                ):
                    if phase.get(key) is not True:
                        blockers.append(f"qualification pool phase lacks verified {label} for Stratum proof: {pool}")
                if phase.get("poolStratumEvidenceTransport") != "qemu_log":
                    blockers.append(f"qualification pool phase lacks recorded Stratum evidence transport: {pool}")
            if "pool_stats" in proof_sources:
                if phase.get("poolStatsWorkerBound") is not True:
                    blockers.append(f"qualification pool phase stats are not worker-bound: {pool}")
                if phase.get("poolStatsAcceptedShareCounter") is not True:
                    blockers.append(f"qualification pool phase stats are not accepted-share counters: {pool}")
                if phase.get("poolStatsSupportsDelta") is not True:
                    blockers.append(f"qualification pool phase stats do not support accepted-share deltas: {pool}")
                if phase.get("poolStatsQualificationCapable") is not True:
                    blockers.append(f"qualification pool phase stats are not accepted-share-count capable: {pool}")
            if not isinstance(required_delta, (int, float)) or not isinstance(qualification_delta, (int, float)):
                blockers.append(f"qualification pool phase is missing strict pool-side share fields: {pool}")
            elif qualification_delta < required_delta:
                blockers.append(f"qualification pool phase has insufficient pool-side accepted-share delta: {pool}")
        else:
            accepted_delta = phase.get("acceptedShareDelta")
            if not isinstance(accepted_delta, (int, float)) or accepted_delta < 1:
                blockers.append(f"required live pool phase has no accepted-share delta: {pool}")
            proof_source = phase.get("acceptedShareProofSource")
            if proof_source not in ACCEPTED_PROOF_SOURCES:
                blockers.append(f"required live pool phase has unsupported proof source: {pool}")
    return blockers


def build_report(root: Path, summary_path: Path) -> dict[str, Any]:
    summary = load_summary(summary_path)
    live = live_verification(root, summary_path, summary)
    blockers = live_release_blockers(live)
    source = configured_source(root, str(live.get("source") or ""))
    return {
        "generatedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repository": {
            "branch": git_output(root, ["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": git_output(root, ["rev-parse", "HEAD"]),
            "statusShort": git_output(root, ["status", "--short"]),
        },
        "source": source,
        "patchStack": patch_inventory(root, source["patchSeries"]),
        "liveVerification": live,
        "releaseBlockers": blockers,
        "notes": [
            "Evidence reports are generated under ignored out/ paths.",
            "This report references evidence files; it does not copy QEMU logs or API payload contents.",
            "`firmware_api` is firmware/API accepted-share evidence, not independent pool-side proof.",
            "`pool_stratum_response` is direct remote-pool protocol proof: the expected worker submitted a share and the live pool returned an accepted response.",
            "`qemu_log` records the Stratum transport for `pool_stratum_response`; generic QEMU log evidence is not a qualification proof source.",
            "`pool_stats` is delayed worker-bound pool-side stats/interface proof collected from the target pool when available.",
            "Qualification mode requires `pool_stratum_response` or `pool_stats`; firmware/API counters, bestdiff, charts, worker-active status, and generic QEMU evidence are diagnostics only.",
            "Run make validate in the same release session before tagging.",
        ],
    }


def markdown(report: dict[str, Any]) -> str:
    live = report["liveVerification"]
    lines = [
        "# virtualAxe Release Evidence",
        "",
        f"- Generated: `{report['generatedAtUtc']}`",
        f"- Branch: `{report['repository']['branch']}`",
        f"- Commit: `{report['repository']['commit']}`",
        f"- Git status clean: `{not bool(report['repository']['statusShort'].strip())}`",
        f"- Source: `{report['source']['name']}`",
        f"- Configured upstream ref: `{report['source']['configuredRef']}`",
        f"- Resolved upstream commit: `{report['source']['resolvedCommit'] or 'unavailable'}`",
        f"- Patch count: `{report['patchStack']['patchCount']}`",
        f"- Patch series SHA256: `{report['patchStack']['seriesSha256']}`",
        f"- Live run ID: `{live['runId']}`",
        f"- Live status: `{live['status']}`",
        f"- Release gate: `{live['releaseGateStatus']}`",
        f"- Mode: `{live['mode']}`",
        f"- Evidence root: `{live['outputDir']}`",
        "",
        "## Live Pool Phases",
        "",
        "| Pool | Status | Diagnostic Accepted Delta | Stratum Delta | Stats Delta | Qualification Accepted Delta | Qualification Proof | Diagnostic Proof | Rejected Delta | Difficulty | Duration | Timeout |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for phase in live["phases"]:
        lines.append(
            "| {label} | `{status}` | {diagnostic} | {stratum} | {stats} | {qualification} | `{qualification_proof}` | `{proof}` | {rejected} | {difficulty} | {duration} | {timeout} |".format(
                label=phase["label"],
                status=phase["status"],
                diagnostic=phase["diagnosticAcceptedShareDelta"],
                stratum=phase["poolStratumAcceptedShareDelta"],
                stats=phase["poolStatsAcceptedShareDelta"],
                qualification=phase["qualificationAcceptedShareDelta"],
                qualification_proof=phase["qualificationProofSource"],
                proof=phase["acceptedShareProofSource"],
                rejected=phase["rejectedShareDelta"],
                difficulty=phase["assignedPoolDifficulty"],
                duration=phase["phaseDurationSeconds"],
                timeout=phase["phaseTimeoutSeconds"],
            )
        )
    lines.extend(["", "## Release Blockers", ""])
    if report["releaseBlockers"]:
        lines.extend(f"- {blocker}" for blocker in report["releaseBlockers"])
    else:
        lines.append("- None in the latest live verifier summary.")
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in report["notes"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize latest virtualAxe release evidence into ignored JSON and Markdown reports.")
    parser.add_argument("--summary", help="Path to a verify-release summary.json. Defaults to latest out/release-matrix run.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary_path = Path(args.summary).resolve() if args.summary else latest_summary(ROOT_DIR)
    report = build_report(ROOT_DIR, summary_path)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "release-evidence.json"
    markdown_path = out_dir / "release-evidence.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown(report), encoding="utf-8")
    payload = {
        "status": "reported" if not report["releaseBlockers"] else "failed",
        "json": str(json_path),
        "markdown": str(markdown_path),
        "releaseBlockers": report["releaseBlockers"],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"release evidence report: {markdown_path}")
        print(f"release evidence json: {json_path}")
        for blocker in report["releaseBlockers"]:
            print(f"release blocker: {blocker}")
    return 0 if not report["releaseBlockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
