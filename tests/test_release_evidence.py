import importlib.util
import json
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT_DIR / "scripts" / "release-evidence.py"


def load_module():
    spec = importlib.util.spec_from_file_location("release_evidence_test_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_summary(
    tmp_path: Path,
    *,
    source: str = "bitaxe",
    mode: str = "smoke",
    status: str = "passed",
    gate_status: str = "PASSED",
    evidence_root: Path | None = None,
    phases: list[dict] | None = None,
) -> Path:
    evidence_root = evidence_root or tmp_path
    phases = phases or [
        {
            "phase": "01-primary",
            "label": "PublicPool",
            "phaseStatus": "PASSED",
            "poolHost": "public-pool.io",
            "poolPort": 3333,
            "assignedPoolDifficulty": 0.0001,
            "acceptedShareDelta": 1,
            "acceptedShareProofSource": "firmware_api",
            "rejectedShareDelta": 0,
            "jobsAssigned": [2],
            "phaseDurationSeconds": 4.0,
            "phaseTimeoutSeconds": 120.0,
            "apiBeforePath": str(evidence_root / "api-before-public.json"),
            "apiAfterPath": str(evidence_root / "api-after-public.json"),
            "waitResultPath": str(evidence_root / "wait-result-public.json"),
            "qemuLogPath": str(evidence_root / "qemu-public.log"),
        },
        {
            "phase": "02-secondary",
            "label": "Bitronics",
            "phaseStatus": "PASSED",
            "poolHost": "pool.bitronics.store",
            "poolPort": 3334,
            "assignedPoolDifficulty": 0.0005,
            "acceptedShareDelta": 1,
            "acceptedShareProofSource": "pool_stats",
            "rejectedShareDelta": 0,
            "jobsAssigned": [2],
            "phaseDurationSeconds": 8.0,
            "phaseTimeoutSeconds": 120.0,
            "poolStatsBeforePath": str(evidence_root / "pool-stats-before-bitronics.json"),
            "poolStatsAfterPath": str(evidence_root / "pool-stats-after-bitronics.json"),
            "waitResultPath": str(evidence_root / "wait-result-bitronics.json"),
            "qemuLogPath": str(evidence_root / "qemu-bitronics.log"),
        },
        {
            "phase": "03-tertiary",
            "label": "Nerdminers",
            "phaseStatus": "PASSED",
            "poolHost": "pool.nerdminers.org",
            "poolPort": 3333,
            "assignedPoolDifficulty": 0.0005,
            "acceptedShareDelta": 1,
            "acceptedShareProofSource": "firmware_api",
            "rejectedShareDelta": 0,
            "jobsAssigned": [2],
            "phaseDurationSeconds": 12.0,
            "phaseTimeoutSeconds": 600.0,
            "apiBeforePath": str(evidence_root / "api-before-nerdminers.json"),
            "apiAfterPath": str(evidence_root / "api-after-nerdminers.json"),
            "waitResultPath": str(evidence_root / "wait-result-nerdminers.json"),
            "qemuLogPath": str(evidence_root / "qemu-nerdminers.log"),
        },
    ]
    summary = {
        "runId": "20260507-test",
        "source": source,
        "mode": mode,
        "status": status,
        "poolUser": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
        "outputDir": str(evidence_root / "release-matrix" / "20260507-test"),
        "releaseGate": {"requiredPools": ["PublicPool", "Bitronics", "Nerdminers"]},
        "profiles": [
            {
                "profile": "gamma",
                "status": status,
                "releaseGateStatus": gate_status,
                "blockingFailures": [],
                "durationSeconds": 42.0,
                "phases": phases,
            }
        ],
    }
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    return summary_path


def test_release_evidence_report_records_live_gate_and_patch_provenance(tmp_path: Path):
    module = load_module()
    summary_path = write_summary(tmp_path)

    report = module.build_report(ROOT_DIR, summary_path)

    assert report["releaseBlockers"] == []
    assert report["source"]["name"] == "bitaxe"
    assert report["source"]["configuredResolvedCommit"] == "ce44b2bbfef60ef8830ab17b321cc295e0c0edc8"
    assert report["source"]["resolvedCommit"] == "ce44b2bbfef60ef8830ab17b321cc295e0c0edc8"
    assert report["source"]["patchSeries"] == "patches/esp-miner/bitaxe/series.txt"
    assert report["patchStack"]["patchSeries"] == "patches/esp-miner/bitaxe/series.txt"
    assert report["patchStack"]["patchCount"] == 13
    assert len(report["patchStack"]["patches"]) == 13
    assert report["liveVerification"]["status"] == "passed"
    assert report["liveVerification"]["releaseGateStatus"] == "PASSED"
    assert report["liveVerification"]["phases"][0]["acceptedShareProofSource"] == "firmware_api"
    assert report["liveVerification"]["phases"][0]["acceptedShareProofMeaning"] == "diagnostic firmware/API accepted-share evidence"
    assert report["liveVerification"]["phases"][1]["acceptedShareProofSource"] == "pool_stats"
    assert report["liveVerification"]["phases"][1]["acceptedShareProofMeaning"] == "delayed worker-bound pool stats accepted-share proof"
    assert "qemuLogPath" in report["liveVerification"]["phases"][0]["evidence"]


def test_release_evidence_report_uses_verified_source_patch_stack(tmp_path: Path):
    module = load_module()
    summary_path = write_summary(tmp_path, source="nerdnos")

    report = module.build_report(ROOT_DIR, summary_path)

    assert report["releaseBlockers"] == []
    assert report["liveVerification"]["source"] == "nerdnos"
    assert report["source"]["name"] == "nerdnos"
    assert report["source"]["configuredResolvedCommit"] == "c18abafebde66c39f4bd8ae6d839088b84b4e79c"
    assert report["source"]["resolvedCommit"] == "c18abafebde66c39f4bd8ae6d839088b84b4e79c"
    assert report["source"]["releaseTag"] == "v1.0.37"
    assert report["source"]["patchSeries"] == "patches/esp-miner/nerdnos/series.txt"
    assert report["patchStack"]["patchSeries"] == "patches/esp-miner/nerdnos/series.txt"
    assert report["patchStack"]["patchCount"] == 6
    assert all(patch["filename"].startswith("000") for patch in report["patchStack"]["patches"])


def test_release_evidence_report_uses_repo_relative_paths(tmp_path: Path):
    module = load_module()
    evidence_root = ROOT_DIR / "out"
    summary_path = write_summary(tmp_path, evidence_root=evidence_root)

    report = module.build_report(ROOT_DIR, summary_path)
    phase = report["liveVerification"]["phases"][0]

    assert report["liveVerification"]["outputDir"] == "out/release-matrix/20260507-test"
    assert phase["evidence"]["qemuLogPath"] == "out/qemu-public.log"
    assert str(ROOT_DIR) not in json.dumps(report)


def test_release_evidence_report_sanitizes_copied_summary_paths(tmp_path: Path):
    module = load_module()
    evidence_root = ROOT_DIR / "out"
    summary_path = write_summary(tmp_path, evidence_root=evidence_root)

    report = module.build_report(ROOT_DIR, summary_path)

    assert report["liveVerification"]["summaryPath"] == "summary.json"
    assert report["liveVerification"]["summaryMarkdownPath"] == "summary.md"
    assert str(tmp_path) not in json.dumps(report)


def test_repo_relative_leaves_urls_and_external_paths_unchanged(tmp_path: Path):
    module = load_module()

    assert module.repo_relative(ROOT_DIR, "https://example.invalid/stats") == "https://example.invalid/stats"
    assert module.repo_relative(ROOT_DIR, str(tmp_path / "outside.txt")) == str(tmp_path / "outside.txt")


def test_release_evidence_report_flags_failed_live_gate(tmp_path: Path):
    module = load_module()
    summary_path = write_summary(tmp_path, status="failed", gate_status="FAILED")

    report = module.build_report(ROOT_DIR, summary_path)

    assert report["releaseBlockers"] == ["latest live release verifier summary is not passed"]
    assert "latest live release verifier summary is not passed" in module.markdown(report)


def test_release_evidence_report_requires_all_blocking_pools(tmp_path: Path):
    module = load_module()
    summary_path = write_summary(
        tmp_path,
        phases=[
            {
                "phase": "01-primary",
                "label": "PublicPool",
                "phaseStatus": "PASSED",
                "poolHost": "public-pool.io",
                "poolPort": 3333,
                "assignedPoolDifficulty": 0.0001,
                "acceptedShareDelta": 1,
                "acceptedShareProofSource": "firmware_api",
                "rejectedShareDelta": 0,
            }
        ],
    )

    report = module.build_report(ROOT_DIR, summary_path)

    assert report["releaseBlockers"] == [
        "latest live release verifier summary is missing required pool evidence: Bitronics, Nerdminers"
    ]


def test_smoke_release_evidence_records_qemu_log_share_diagnostic(tmp_path: Path):
    module = load_module()
    summary_path = write_summary(
        tmp_path,
        phases=[
            {
                "phase": "01-primary",
                "label": "PublicPool",
                "phaseStatus": "PASSED",
                "poolHost": "public-pool.io",
                "poolPort": 3333,
                "assignedPoolDifficulty": 0.0001,
                "acceptedShareDelta": 1,
                "acceptedShareProofSource": "qemu_log",
                "qemuAcceptedShareDelta": 1,
                "rejectedShareDelta": 0,
            },
            {
                "phase": "02-secondary",
                "label": "Bitronics",
                "phaseStatus": "PASSED",
                "poolHost": "pool.bitronics.store",
                "poolPort": 3334,
                "assignedPoolDifficulty": 0.0005,
                "acceptedShareDelta": 1,
                "acceptedShareProofSource": "qemu_log",
                "qemuAcceptedShareDelta": 1,
                "rejectedShareDelta": 0,
            },
            {
                "phase": "03-tertiary",
                "label": "Nerdminers",
                "phaseStatus": "PASSED",
                "poolHost": "pool.nerdminers.org",
                "poolPort": 3333,
                "assignedPoolDifficulty": 0.0005,
                "acceptedShareDelta": 1,
                "acceptedShareProofSource": "qemu_log",
                "qemuAcceptedShareDelta": 1,
                "rejectedShareDelta": 0,
            },
        ],
    )

    report = module.build_report(ROOT_DIR, summary_path)

    assert report["releaseBlockers"] == []
    assert (
        report["liveVerification"]["phases"][0]["acceptedShareProofMeaning"]
        == "QEMU log transport only; not a qualification proof source"
    )


def test_qualification_release_evidence_rejects_firmware_api_proof(tmp_path: Path):
    module = load_module()
    phases = [
        {
            "phase": "01-primary",
            "label": "PublicPool",
            "phaseStatus": "PASSED",
            "poolHost": "public-pool.io",
            "poolPort": 3333,
            "assignedPoolDifficulty": 0.0001,
            "acceptedShareDelta": 10,
            "diagnosticAcceptedShareDelta": 10,
            "acceptedShareProofSource": "firmware_api",
            "qualificationAcceptedShareDelta": 0,
            "qualificationProofSource": "",
            "poolStatsAcceptedShareDelta": 0,
            "poolStatsWorkerBound": True,
            "poolStatsAcceptedShareCounter": True,
            "poolStatsSupportsDelta": True,
            "poolStatsQualificationCapable": True,
            "requiredAcceptedShareDelta": 10,
            "rejectedShareDelta": 0,
        },
        {
            "phase": "02-secondary",
            "label": "Bitronics",
            "phaseStatus": "PASSED",
            "poolHost": "pool.bitronics.store",
            "poolPort": 3334,
            "assignedPoolDifficulty": 0.0005,
            "acceptedShareDelta": 10,
            "diagnosticAcceptedShareDelta": 10,
            "acceptedShareProofSource": "pool_stats",
            "qualificationAcceptedShareDelta": 10,
            "qualificationProofSource": "pool_stats",
            "poolStatsAcceptedShareDelta": 10,
            "poolStatsWorkerBound": True,
            "poolStatsAcceptedShareCounter": True,
            "poolStatsSupportsDelta": True,
            "poolStatsQualificationCapable": True,
            "requiredAcceptedShareDelta": 10,
            "rejectedShareDelta": 0,
        },
        {
            "phase": "03-tertiary",
            "label": "Nerdminers",
            "phaseStatus": "PASSED",
            "poolHost": "pool.nerdminers.org",
            "poolPort": 3333,
            "assignedPoolDifficulty": 0.001,
            "acceptedShareDelta": 10,
            "diagnosticAcceptedShareDelta": 10,
            "acceptedShareProofSource": "pool_stats",
            "qualificationAcceptedShareDelta": 10,
            "qualificationProofSource": "pool_stats",
            "poolStatsAcceptedShareDelta": 10,
            "poolStatsWorkerBound": True,
            "poolStatsAcceptedShareCounter": True,
            "poolStatsSupportsDelta": True,
            "poolStatsQualificationCapable": True,
            "requiredAcceptedShareDelta": 10,
            "rejectedShareDelta": 0,
        },
    ]
    summary_path = write_summary(tmp_path, mode="qualification", phases=phases)

    report = module.build_report(ROOT_DIR, summary_path)

    assert "qualification pool phase lacks pool-side accepted-share proof: PublicPool" in report["releaseBlockers"]
    assert "qualification pool phase has insufficient pool-side accepted-share delta: PublicPool" in report["releaseBlockers"]


def test_qualification_release_evidence_rejects_qemu_log_proof(tmp_path: Path):
    module = load_module()
    phase = {
        "phase": "01-primary",
        "label": "PublicPool",
        "phaseStatus": "PASSED",
        "poolHost": "public-pool.io",
        "poolPort": 3333,
        "assignedPoolDifficulty": 0.0001,
        "acceptedShareDelta": 10,
        "diagnosticAcceptedShareDelta": 10,
        "acceptedShareProofSource": "qemu_log",
        "qemuAcceptedShareDelta": 10,
        "qualificationAcceptedShareDelta": 0,
        "qualificationProofSource": "",
        "poolStatsAcceptedShareDelta": 0,
        "poolStatsWorkerBound": True,
        "poolStatsAcceptedShareCounter": True,
        "poolStatsSupportsDelta": True,
        "poolStatsQualificationCapable": True,
        "requiredAcceptedShareDelta": 10,
        "rejectedShareDelta": 0,
    }
    summary_path = write_summary(tmp_path, mode="qualification", phases=[phase, {**phase, "label": "Bitronics"}, {**phase, "label": "Nerdminers"}])

    report = module.build_report(ROOT_DIR, summary_path)

    assert "qualification pool phase lacks pool-side accepted-share proof: PublicPool" in report["releaseBlockers"]
    assert "qualification pool phase lacks pool-side accepted-share proof: Bitronics" in report["releaseBlockers"]
    assert "qualification pool phase lacks pool-side accepted-share proof: Nerdminers" in report["releaseBlockers"]


def test_qualification_release_evidence_accepts_pool_stratum_response_proof(tmp_path: Path):
    module = load_module()
    phases = []
    for label in ("PublicPool", "Bitronics", "Nerdminers"):
        phases.append(
            {
                "phase": label,
                "label": label,
                "phaseStatus": "PASSED",
                "poolHost": "pool.example.invalid",
                "poolPort": 3333,
                "assignedPoolDifficulty": 0.001,
                "acceptedShareDelta": 10,
                "diagnosticAcceptedShareDelta": 10,
                "acceptedShareProofSource": "pool_stratum_response",
                "qualificationAcceptedShareDelta": 10,
                "qualificationProofSource": "pool_stratum_response",
                "qualificationProofSources": ["pool_stratum_response"],
                "poolStratumAcceptedShareDelta": 10,
                "poolStratumEvidenceTransport": "qemu_log",
                "qemuPoolIdentity": True,
                "qemuWorkerIdentity": True,
                "qemuSubmitSeen": True,
                "qemuAcceptedShare": True,
                "poolStatsAcceptedShareDelta": 0,
                "poolStatsWorkerBound": False,
                "poolStatsAcceptedShareCounter": False,
                "poolStatsSupportsDelta": False,
                "poolStatsQualificationCapable": False,
                "requiredAcceptedShareDelta": 10,
                "rejectedShareDelta": 0,
            }
        )
    summary_path = write_summary(tmp_path, mode="qualification", phases=phases)

    report = module.build_report(ROOT_DIR, summary_path)

    assert report["releaseBlockers"] == []
    assert report["liveVerification"]["phases"][0]["qualificationProofMeaning"] == (
        "direct remote pool Stratum accepted-response proof"
    )


def test_qualification_release_evidence_rejects_unverified_pool_stratum_response(tmp_path: Path):
    module = load_module()
    phase = {
        "phase": "01-primary",
        "label": "PublicPool",
        "phaseStatus": "PASSED",
        "poolHost": "public-pool.io",
        "poolPort": 3333,
        "assignedPoolDifficulty": 0.0001,
        "acceptedShareDelta": 10,
        "diagnosticAcceptedShareDelta": 10,
        "acceptedShareProofSource": "pool_stratum_response",
        "qualificationAcceptedShareDelta": 10,
        "qualificationProofSource": "pool_stratum_response",
        "qualificationProofSources": ["pool_stratum_response"],
        "poolStratumAcceptedShareDelta": 10,
        "poolStratumEvidenceTransport": "qemu_log",
        "qemuPoolIdentity": True,
        "qemuWorkerIdentity": False,
        "qemuSubmitSeen": True,
        "qemuAcceptedShare": True,
        "requiredAcceptedShareDelta": 10,
        "rejectedShareDelta": 0,
    }
    summary_path = write_summary(tmp_path, mode="qualification", phases=[phase, {**phase, "label": "Bitronics"}, {**phase, "label": "Nerdminers"}])

    report = module.build_report(ROOT_DIR, summary_path)

    assert "qualification pool phase lacks verified worker identity for Stratum proof: PublicPool" in report["releaseBlockers"]


def test_qualification_release_evidence_accepts_pool_stats_proof(tmp_path: Path):
    module = load_module()
    phases = []
    for label in ("PublicPool", "Bitronics", "Nerdminers"):
        phases.append(
            {
                "phase": label,
                "label": label,
                "phaseStatus": "PASSED",
                "poolHost": "pool.example.invalid",
                "poolPort": 3333,
                "assignedPoolDifficulty": 0.001,
                "acceptedShareDelta": 10,
                "diagnosticAcceptedShareDelta": 10,
                "acceptedShareProofSource": "pool_stats",
                "qualificationAcceptedShareDelta": 10,
                "qualificationProofSource": "pool_stats",
                "poolStatsAcceptedShareDelta": 10,
                "poolStatsWorkerBound": True,
                "poolStatsAcceptedShareCounter": True,
                "poolStatsSupportsDelta": True,
                "poolStatsQualificationCapable": True,
                "requiredAcceptedShareDelta": 10,
                "rejectedShareDelta": 0,
            }
        )
    summary_path = write_summary(tmp_path, mode="qualification", phases=phases)

    report = module.build_report(ROOT_DIR, summary_path)

    assert report["releaseBlockers"] == []


def test_qualification_release_evidence_rejects_non_capable_pool_stats_kind(tmp_path: Path):
    module = load_module()
    public_phase = {
        "phase": "01-primary",
        "label": "PublicPool",
        "phaseStatus": "PASSED",
        "poolHost": "public-pool.io",
        "poolPort": 3333,
        "assignedPoolDifficulty": 0.0001,
        "acceptedShareDelta": 10,
        "diagnosticAcceptedShareDelta": 10,
        "acceptedShareProofSource": "pool_stats",
        "qualificationAcceptedShareDelta": 10,
        "qualificationProofSource": "pool_stats",
        "poolStatsAcceptedShareDelta": 10,
        "poolStatsProofKind": "public_pool_bestdiff",
        "poolStatsWorkerBound": True,
        "poolStatsAcceptedShareCounter": False,
        "poolStatsSupportsDelta": False,
        "poolStatsQualificationCapable": False,
        "requiredAcceptedShareDelta": 10,
        "rejectedShareDelta": 0,
    }
    bitronics_phase = {
        **public_phase,
        "phase": "02-secondary",
        "label": "Bitronics",
        "poolHost": "pool.bitronics.store",
        "poolPort": 3334,
        "poolStatsProofKind": "bitronics_status_evidence",
        "poolStatsWorkerBound": False,
        "poolStatsAcceptedShareCounter": False,
        "poolStatsSupportsDelta": False,
        "poolStatsQualificationCapable": False,
    }
    nerdminers_phase = {
        **public_phase,
        "phase": "03-tertiary",
        "label": "Nerdminers",
        "poolHost": "pool.nerdminers.org",
        "poolPort": 3333,
        "poolStatsProofKind": "ckpool_shares",
        "poolStatsWorkerBound": True,
        "poolStatsAcceptedShareCounter": True,
        "poolStatsSupportsDelta": True,
        "poolStatsQualificationCapable": True,
    }
    summary_path = write_summary(tmp_path, mode="qualification", phases=[public_phase, bitronics_phase, nerdminers_phase])

    report = module.build_report(ROOT_DIR, summary_path)

    assert "qualification pool phase stats are not accepted-share counters: PublicPool" in report["releaseBlockers"]
    assert "qualification pool phase stats do not support accepted-share deltas: PublicPool" in report["releaseBlockers"]
    assert "qualification pool phase stats are not accepted-share-count capable: PublicPool" in report["releaseBlockers"]
    assert "qualification pool phase stats are not worker-bound: Bitronics" in report["releaseBlockers"]
    assert "qualification pool phase stats are not accepted-share counters: Bitronics" in report["releaseBlockers"]
    assert "qualification pool phase stats do not support accepted-share deltas: Bitronics" in report["releaseBlockers"]
    assert "qualification pool phase stats are not accepted-share-count capable: Bitronics" in report["releaseBlockers"]


def test_qualification_release_evidence_rejects_legacy_summary_without_strict_fields(tmp_path: Path):
    module = load_module()
    phases = []
    for label in ("PublicPool", "Bitronics", "Nerdminers"):
        phases.append(
            {
                "phase": label,
                "label": label,
                "phaseStatus": "PASSED",
                "poolHost": "pool.example.invalid",
                "poolPort": 3333,
                "assignedPoolDifficulty": 0.001,
                "acceptedShareDelta": 10,
                "acceptedShareProofSource": "pool_stats",
                "poolStatsAcceptedShareDelta": 10,
                "rejectedShareDelta": 0,
            }
        )
    summary_path = write_summary(tmp_path, mode="qualification", phases=phases)

    report = module.build_report(ROOT_DIR, summary_path)

    assert "qualification pool phase is missing strict pool-side share fields: PublicPool" in report["releaseBlockers"]
    assert "qualification pool phase is missing strict pool-side share fields: Bitronics" in report["releaseBlockers"]
    assert "qualification pool phase is missing strict pool-side share fields: Nerdminers" in report["releaseBlockers"]


def test_release_evidence_report_rejects_unknown_or_missing_share_proof(tmp_path: Path):
    module = load_module()
    summary_path = write_summary(
        tmp_path,
        phases=[
            {
                "phase": "01-primary",
                "label": "PublicPool",
                "phaseStatus": "PASSED",
                "poolHost": "public-pool.io",
                "poolPort": 3333,
                "assignedPoolDifficulty": 0.0001,
                "acceptedShareDelta": 0,
                "acceptedShareProofSource": "firmware_api",
                "rejectedShareDelta": 0,
            },
            {
                "phase": "02-secondary",
                "label": "Bitronics",
                "phaseStatus": "PASSED",
                "poolHost": "pool.bitronics.store",
                "poolPort": 3334,
                "assignedPoolDifficulty": 0.0005,
                "acceptedShareDelta": 1,
                "acceptedShareProofSource": "operator_claim",
                "rejectedShareDelta": 0,
            },
            {
                "phase": "03-tertiary",
                "label": "Nerdminers",
                "phaseStatus": "FAILED",
                "poolHost": "pool.nerdminers.org",
                "poolPort": 3333,
                "assignedPoolDifficulty": 0.0005,
                "acceptedShareDelta": 1,
                "acceptedShareProofSource": "pool_stats",
                "rejectedShareDelta": 0,
            },
        ],
    )

    report = module.build_report(ROOT_DIR, summary_path)

    assert "required live pool phase has no accepted-share delta: PublicPool" in report["releaseBlockers"]
    assert "required live pool phase has unsupported proof source: Bitronics" in report["releaseBlockers"]
    assert "required live pool phase did not pass: Nerdminers" in report["releaseBlockers"]


def test_latest_summary_uses_newest_file_not_lexical_order(tmp_path: Path):
    module = load_module()
    older = tmp_path / "out" / "release-matrix" / "zz-old" / "summary.json"
    newer = tmp_path / "out" / "release-matrix" / "20260507-new" / "summary.json"
    older.parent.mkdir(parents=True)
    newer.parent.mkdir(parents=True)
    older.write_text("{}", encoding="utf-8")
    newer.write_text("{}", encoding="utf-8")
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))

    assert module.latest_summary(tmp_path) == newer
