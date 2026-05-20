import importlib.util
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT_DIR / "scripts" / "verify-release.py"
TEST_POOL_USER = "bc1qvirtualaxetestfixture0000000000000000000000000"


def load_module():
    spec = importlib.util.spec_from_file_location("verify_release_test_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_args_uses_default_public_pool_user(monkeypatch):
    module = load_module()
    monkeypatch.delenv("VERIFY_POOL_USER", raising=False)
    monkeypatch.setattr(sys, "argv", ["verify-release.py"])

    args = module.parse_args()

    assert args.pool_user == module.DEFAULT_VERIFY_POOL_USER
    assert args.source == "bitaxe"


def test_parse_args_accepts_verify_pool_user_env(monkeypatch):
    module = load_module()
    monkeypatch.setenv("VERIFY_POOL_USER", TEST_POOL_USER)
    monkeypatch.setattr(sys, "argv", ["verify-release.py"])

    args = module.parse_args()

    assert args.pool_user == TEST_POOL_USER


def test_runtime_active_accepts_native_pid_file(tmp_path: Path):
    module = load_module()
    (tmp_path / "qemu.pid").write_text("123\n", encoding="utf-8")

    assert module.runtime_active(tmp_path) is True


def test_runtime_active_accepts_container_cid_file(tmp_path: Path):
    module = load_module()
    (tmp_path / "qemu.cid").write_text("container-id\n", encoding="utf-8")

    assert module.runtime_active(tmp_path) is True


def test_runtime_active_is_false_without_runtime_markers(tmp_path: Path):
    module = load_module()

    assert module.runtime_active(tmp_path) is False


def test_release_phases_match_gamma_pool_gate_policy():
    module = load_module()

    phases = module.phases_for_mode("smoke")

    assert [phase["slug"] for phase in phases] == ["public", "bitronics", "nerdminers"]
    assert [(phase["host"], phase["port"]) for phase in module.PHASES] == [
        ("public-pool.io", 3333),
        ("pool.bitronics.store", 3334),
        ("pool.nerdminers.org", 3333),
    ]
    assert [phase["subscribe_agent"] for phase in phases] == [
        "",
        "NerdMinerV2/virtualAxe-gamma",
        "NerdMinerV2/virtualAxe-gamma",
    ]
    assert [phase["difficulty"] for phase in phases] == [0.0001, 0.0001, 0.0005]
    assert [phase["min_accepted_share_delta"] for phase in phases] == [1, 1, 1]
    assert [phase["min_duration_seconds"] for phase in phases] == [0, 0, 0]
    assert [phase["phase_timeout_seconds"] for phase in phases] == [120, 600, 1200]
    assert [phase["smoke_phase_timeout_seconds"] for phase in phases] == [120, 600, 1200]
    assert [phase["required_for_pass"] for phase in phases] == [True, True, True]
    assert [phase["require_accepted_share"] for phase in phases] == [True, True, True]
    assert [phase["require_accepted_log"] for phase in phases] == [False, False, False]
    assert phases[0]["pool_stats_kind"] == "public_pool_bestdiff"
    assert phases[0]["pool_stats_url_template"] == "https://public-pool.io:40557/api/client/{pool_user}"
    assert phases[0]["pool_stats_page_url_template"] == "https://public-pool.io:40557/api/client/{pool_user}/{worker}"
    assert phases[1]["pool_stats_kind"] == "bitronics_status_evidence"
    assert phases[1]["pool_stats_url_template"] == "https://pool.bitronics.store/api/stats/{pool_user}"
    assert phases[2]["pool_stats_url_template"] == "https://pool.nerdminers.org/users/{pool_user}"


def test_source_smoke_timeouts_are_probability_based_on_effective_hash_attempts():
    module = load_module()

    phases = {phase["label"]: phase for phase in module.phases_for_mode("smoke")}
    bitronics_feasibility = phases["Bitronics"]["smoke_feasibility"]
    nerdminers_feasibility = phases["Nerdminers"]["smoke_feasibility"]

    assert module.expected_hashes_per_share(0.001) == 0.001 * (2**32)
    assert module.share_probability(12500.0, 0.0005, 120) < 0.55
    assert module.share_probability(12500.0, 0.0005, 600) >= 0.95
    assert module.share_probability(12500.0, 0.001, 600) < 0.85
    assert module.share_probability(12500.0, 0.001, 1200) >= 0.95
    assert 510 < module.duration_for_share_probability(12500.0, 0.0005, 0.95) < 520
    assert 1020 < module.duration_for_share_probability(12500.0, 0.001, 0.95) < 1040
    assert bitronics_feasibility["estimatedHashrateHps"] == 12500.0
    assert bitronics_feasibility["assignedDifficulty"] == 0.0005
    assert bitronics_feasibility["targetProbability"] == 0.95
    assert bitronics_feasibility["configuredTimeoutSeconds"] == 600
    assert bitronics_feasibility["configuredTimeoutProbability"] >= 0.95
    assert "effective guest hash attempts" in bitronics_feasibility["basis"]
    assert nerdminers_feasibility["assignedDifficulty"] == 0.001
    assert nerdminers_feasibility["configuredTimeoutSeconds"] == 1200
    assert nerdminers_feasibility["configuredTimeoutProbability"] >= 0.95


def test_qualification_timeout_is_probability_based_on_nerdminers_share_count():
    module = load_module()

    policy = module.qualification_count_policy()

    assert policy["requiredAcceptedShareDelta"] == 5
    assert policy["targetProbability"] == 0.95
    assert policy["observedNerdminersAcceptedShares"] == 4
    assert policy["observedNerdminersDurationSeconds"] == 1800
    assert 4100 < policy["targetProbabilitySecondsAtObservedRate"] < 4200
    assert 3100 < policy["targetProbabilitySecondsAtHashModel"] < 3200
    assert policy["configuredTimeoutSeconds"] == 4200
    assert policy["configuredTimeoutProbabilityAtObservedRate"] >= 0.95
    assert policy["configuredTimeoutProbabilityAtHashModel"] >= 0.99
    assert module.poisson_probability_at_least(5, 4.0) < 0.38
    assert module.poisson_probability_at_least(5, (4 / 1800) * 2400) > 0.60


def test_qualification_mode_requires_five_accepted_shares_without_fixed_duration():
    module = load_module()

    phases = module.phases_for_mode("qualification")

    assert [phase["min_accepted_share_delta"] for phase in phases] == [5, 5, 5]
    assert [phase["require_pool_side_accepted_share"] for phase in phases] == [True, True, True]
    assert [phase["require_pool_stats_accepted_share"] for phase in phases] == [False, False, False]
    assert [phase["pool_stats_qualification_capable"] for phase in phases] == [False, False, True]
    assert [phase["min_duration_seconds"] for phase in phases] == [0, 0, 0]
    assert [phase["phase_timeout_seconds"] for phase in phases] == [4200, 4200, 4200]
    assert [phase["max_rejected_share_delta"] for phase in phases] == [0, 0, 0]


def test_pool_stats_capability_metadata_documents_current_adapters():
    module = load_module()
    phases = {phase["label"]: phase for phase in module.phases_for_mode("qualification")}

    public = phases["PublicPool"]
    assert public["pool_stats_worker_bound"] is True
    assert public["pool_stats_accepted_share_counter"] is False
    assert public["pool_stats_supports_delta"] is False
    assert public["pool_stats_rejected_share_counter"] is False
    assert "best-difficulty" in public["pool_stats_qualification_capability"]
    assert "not an accepted-share counter" in public["pool_stats_qualification_capability"]

    bitronics = phases["Bitronics"]
    assert bitronics["pool_stats_worker_bound"] is False
    assert bitronics["pool_stats_accepted_share_counter"] is False
    assert bitronics["pool_stats_supports_delta"] is False
    assert bitronics["pool_stats_rejected_share_counter"] is False
    assert "status" in bitronics["pool_stats_qualification_capability"]
    assert "not a worker-bound accepted-share counter" in bitronics["pool_stats_qualification_capability"]

    nerdminers = phases["Nerdminers"]
    assert nerdminers["pool_stats_worker_bound"] is True
    assert nerdminers["pool_stats_accepted_share_counter"] is True
    assert nerdminers["pool_stats_supports_delta"] is True
    assert nerdminers["pool_stats_rejected_share_counter"] is False
    assert "CKPool-style" in nerdminers["pool_stats_qualification_capability"]


def test_worker_name_uses_short_pool_compatible_run_token():
    module = load_module()

    run_id = "20260428-three-pool-smoke-api-restart"
    bitronics_worker = module.worker_name(
        TEST_POOL_USER,
        "gamma",
        "bitronics",
        run_id,
    )
    nerdminers_worker = module.worker_name(
        TEST_POOL_USER,
        "gamma",
        "nerdminers",
        run_id,
    )
    token = module.worker_run_token(run_id)
    public_worker = module.worker_name(
        TEST_POOL_USER,
        "gamma",
        "public",
        run_id,
    )

    assert token.startswith("r")
    assert token.isalnum()
    assert token[1:].islower()
    assert token[1:].isalpha()
    assert public_worker == f"{TEST_POOL_USER}.vagpub{token}"
    assert bitronics_worker == f"{TEST_POOL_USER}.vagbit{token}"
    assert nerdminers_worker == f"{TEST_POOL_USER}.vagnerd{token}"
    assert "20260428" not in public_worker
    assert "20260428" not in bitronics_worker
    assert "20260428" not in nerdminers_worker
    assert public_worker.count(".") == 1
    assert bitronics_worker.count(".") == 1
    assert nerdminers_worker.count(".") == 1
    assert len(public_worker) < 90
    assert len(bitronics_worker) < 90
    assert len(nerdminers_worker) < 90


def test_worker_token_avoids_public_pool_numeric_suffix_regression():
    module = load_module()

    token = module.worker_run_token("20260505-221001")

    assert token != "r16d9"
    assert token.startswith("r")
    assert token[1:].isalpha()
    assert token[1:].islower()


def test_required_pool_phase_status_requires_configured_share_delta():
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[0])
    phase["min_accepted_share_delta"] = 2
    phase["min_duration_seconds"] = 5

    assert module.phase_status(phase, True, 1, 5, 0) == "FAILED"
    assert module.phase_status(phase, True, 2, 4, 0) == "FAILED"
    assert module.phase_status(phase, True, 2, 5, 1) == "FAILED"
    assert module.phase_status(phase, True, 2, 5, 0) == "PASSED"


def test_qualification_phase_status_requires_five_pool_side_accepts_and_zero_rejects():
    module = load_module()
    phase = dict(module.phases_for_mode("qualification")[2])

    assert module.phase_status(phase, True, 5, 0, 0) == "FAILED"
    assert module.phase_status(phase, True, 5, 0, 0, qualification_accepted_share_delta=4) == "FAILED"
    assert module.phase_status(phase, True, 0, 0, 1, qualification_accepted_share_delta=5) == "FAILED"
    assert module.phase_status(phase, True, 0, 0, 0, qualification_accepted_share_delta=5) == "PASSED"


def test_non_capable_stats_metadata_does_not_block_direct_protocol_qualification_status():
    module = load_module()
    phase = dict(module.phases_for_mode("qualification")[0])

    assert phase["pool_stats_qualification_capable"] is False
    assert module.phase_status(phase, True, 100, 0, 0, qualification_accepted_share_delta=5) == "PASSED"


def test_bitronics_primary_settings_include_release_agent_and_difficulty():
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[1])
    payload = module.phase_primary_settings(phase, "bc1ptest.gamma.bitronics.run")

    assert payload == {
        "stratumURL": "pool.bitronics.store",
        "stratumPort": 3334,
        "stratumUser": "bc1ptest.gamma.bitronics.run",
        "stratumSubscribeAgent": "NerdMinerV2/virtualAxe-gamma",
        "stratumSuggestedDifficulty": 0.0001,
    }


def test_nerdnos_primary_settings_use_source_native_api_fields():
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[1])
    payload = module.phase_primary_settings(phase, "bc1ptest.gamma.bitronics.run", "nerdnos")

    assert payload == {
        "stratumURL": "pool.bitronics.store",
        "stratumPort": 3334,
        "stratumUser": "bc1ptest.gamma.bitronics.run",
        "stratumDifficulty": 0.0001,
    }


def test_phase_runtime_paths_are_per_phase_and_inside_release_bundle(tmp_path: Path):
    module = load_module()
    public_phase, bitronics_phase, _ = module.phases_for_mode("smoke")

    public_out, public_state = module.phase_runtime_paths(tmp_path, public_phase)
    bitronics_out, bitronics_state = module.phase_runtime_paths(tmp_path, bitronics_phase)

    assert public_out == tmp_path / "runtime" / "01-primary" / "out"
    assert public_state == tmp_path / "runtime" / "01-primary" / "state"
    assert bitronics_out == tmp_path / "runtime" / "02-secondary" / "out"
    assert bitronics_state == tmp_path / "runtime" / "02-secondary" / "state"
    assert public_out != bitronics_out
    assert public_state != bitronics_state


def test_settings_match_allows_float_roundtrip_noise():
    module = load_module()
    payload = {
        "stratumSuggestedDifficulty": 0.0010000000474974513,
        "stratumSubscribeAgent": "NerdMinerV2/virtualAxe-gamma",
    }
    expected = {
        "stratumSuggestedDifficulty": 0.001,
        "stratumSubscribeAgent": "NerdMinerV2/virtualAxe-gamma",
    }

    assert module.settings_match(payload, expected) is True


def test_run_command_can_stream_wait_stderr(capsys):
    module = load_module()

    result = module.run_command(
        [
            sys.executable,
            "-c",
            "import sys; print('final-json'); print('live-progress', file=sys.stderr)",
        ],
        capture=True,
        stream_stderr=True,
    )

    assert result.returncode == 0
    assert result.stdout == "final-json\n"
    assert result.stderr == "live-progress\n"
    assert "live-progress" in capsys.readouterr().err


def test_summarize_phase_keeps_wait_workers_when_after_payload_lacks_workers(tmp_path: Path):
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[1])
    wait_payload = {
        "durationSeconds": 12.0,
        "poolDifficulty": 0.0005,
        "virtualAsicWorkers": [
            {
                "asicNr": 0,
                "jobsAssigned": 3,
            }
        ],
    }
    before_payload = {"sharesAccepted": 5, "stratumSubscribeAgent": "NerdMinerV2/virtualAxe-gamma"}
    after_payload = {"sharesAccepted": 7, "sharesRejected": 0, "poolDifficulty": 0.0005}

    summary = module.summarize_phase(
        phase,
        tmp_path,
        tmp_path / "runtime" / "out",
        tmp_path / "runtime" / "state",
        wait_payload,
        before_payload,
        after_payload,
        phase_error="",
    )

    assert summary["phaseStatus"] == "PASSED"
    assert summary["workerCount"] == 1
    assert summary["jobsAssigned"] == [3]
    assert summary["acceptedShareDelta"] == 2
    assert summary["diagnosticAcceptedShareDelta"] == 2
    assert summary["qualificationAcceptedShareDelta"] == 2
    assert summary["qualificationProofSource"] == ""
    assert summary["localAcceptedShareDelta"] == 2
    assert summary["requiredAcceptedShareDelta"] == 1
    assert summary["phaseDurationSeconds"] == 12.0
    assert summary["rejectedShareDelta"] == 0
    assert summary["subscribeAgent"] == "NerdMinerV2/virtualAxe-gamma"
    assert summary["smokeFeasibility"]["configuredTimeoutSeconds"] == 600


def test_nerdminers_phase_can_use_pool_side_worker_stats_for_acceptance(tmp_path: Path):
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[2])
    wait_payload = {
        "durationSeconds": 12.0,
        "poolDifficulty": 0.0005,
        "acceptedShareProofSource": "pool_stats",
        "poolStatsURL": "https://pool.nerdminers.org/users/bc1ptest",
        "poolStatsWorker": "bc1ptest.gamma.nerdminers.rtest",
        "poolStatsSharesBefore": 3.0,
        "poolStatsSharesAfter": 4.0,
        "poolStatsAcceptedShareDelta": 1.0,
        "poolStatsAccepted": True,
        "localDiffSatisfiedByAcceptance": True,
        "provenLocalDiffLowerBound": 0.0005,
        "virtualAsicWorkers": [{"asicNr": 0, "jobsAssigned": 2}],
    }
    before_payload = {"sharesAccepted": 0, "sharesRejected": 0}
    after_payload = {"sharesAccepted": 0, "sharesRejected": 0, "poolDifficulty": 0.0005}

    summary = module.summarize_phase(
        phase,
        tmp_path,
        tmp_path / "runtime" / "out",
        tmp_path / "runtime" / "state",
        wait_payload,
        before_payload,
        after_payload,
        phase_error="",
    )

    assert summary["phaseStatus"] == "PASSED"
    assert summary["acceptedShareProofSource"] == "pool_stats"
    assert summary["localAcceptedShareDelta"] == 0
    assert summary["acceptedShareDelta"] == 1
    assert summary["qualificationAcceptedShareDelta"] == 1
    assert summary["qualificationProofSource"] == ""
    assert summary["poolStatsAccepted"] is True
    assert summary["smokeFeasibility"]["configuredTimeoutSeconds"] == 1200
    assert summary["poolStatsWorkerBound"] is True
    assert summary["poolStatsAcceptedShareCounter"] is True
    assert summary["poolStatsSupportsDelta"] is True
    assert summary["poolStatsRejectedShareCounter"] is False


def test_bitronics_phase_cannot_use_pool_wide_last_share_for_smoke_acceptance(tmp_path: Path):
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[1])
    wait_payload = {
        "durationSeconds": 42.0,
        "poolDifficulty": 0.0005,
        "acceptedShareProofSource": "pool_stats",
        "poolStatsURL": "https://pool.bitronics.store/api/stats/bc1ptest",
        "poolStatsWorker": "bc1ptest.vagbitrtest",
        "poolStatsProofKind": "bitronics_status_evidence",
        "poolStatsSharesBefore": 0.0,
        "poolStatsSharesAfter": 1.0,
        "poolStatsAcceptedShareDelta": 1.0,
        "poolStatsAccepted": True,
        "poolStatsWorkerActive": True,
        "poolStatsLastShareBefore": "2026-04-30T20:00:00.000Z",
        "poolStatsLastShareAfter": "2026-04-30T20:01:00.000Z",
        "localDiffSatisfiedByAcceptance": True,
        "provenLocalDiffLowerBound": 0.0005,
        "virtualAsicWorkers": [{"asicNr": 0, "jobsAssigned": 2}],
    }
    before_payload = {"sharesAccepted": 0, "sharesRejected": 0}
    after_payload = {"sharesAccepted": 0, "sharesRejected": 0, "poolDifficulty": 0.0005}

    summary = module.summarize_phase(
        phase,
        tmp_path,
        tmp_path / "runtime" / "out",
        tmp_path / "runtime" / "state",
        wait_payload,
        before_payload,
        after_payload,
        phase_error="",
    )

    assert summary["phaseStatus"] == "FAILED"
    assert summary["acceptedShareProofSource"] == "pool_stats"
    assert summary["acceptedShareDelta"] == 0
    assert summary["poolStatsAccepted"] is False
    assert summary["poolStatsAcceptedShareDelta"] == 0
    assert summary["poolStatsProofKind"] == "bitronics_status_evidence"
    assert summary["poolStatsWorkerActive"] is True
    assert summary["poolStatsWorkerBound"] is False
    assert summary["poolStatsAcceptedShareCounter"] is False
    assert summary["poolStatsSupportsDelta"] is False


def test_pool_stats_before_failure_records_error_without_aborting_phase(tmp_path: Path, monkeypatch):
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[2])

    def fail_snapshot(*args, **kwargs):
        raise RuntimeError("stats unavailable")

    monkeypatch.setattr(module, "pool_stats_worker_snapshot", fail_snapshot)

    shares, best_difficulty, last_share = module.capture_pool_stats_before(
        tmp_path,
        "https://pool.nerdminers.org/users/bc1ptest",
        "bc1ptest.vagnerdrtest",
        phase,
    )

    payload = json.loads((tmp_path / "pool-stats-before.json").read_text(encoding="utf-8"))
    assert shares is None
    assert best_difficulty is None
    assert last_share is None
    assert payload["error"] == "stats unavailable"
    assert payload["worker"] == "bc1ptest.vagnerdrtest"


def test_public_pool_phase_can_use_worker_bestdiff_for_smoke_acceptance(tmp_path: Path):
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[0])
    wait_payload = {
        "durationSeconds": 18.0,
        "poolDifficulty": 0.0001,
        "acceptedShareProofSource": "pool_stats",
        "poolStatsURL": "https://public-pool.io:40557/api/client/bc1ptest",
        "poolStatsWorker": "bc1ptest.vagpubrtest",
        "poolStatsProofKind": "public_pool_bestdiff",
        "poolStatsSharesBefore": 0.0,
        "poolStatsSharesAfter": 1.0,
        "poolStatsAcceptedShareDelta": 1.0,
        "poolStatsAccepted": True,
        "poolStatsWorkerActive": True,
        "poolStatsBestDifficultyBefore": 0.0,
        "poolStatsBestDifficultyAfter": 0.05,
        "localDiffSatisfiedByAcceptance": True,
        "provenLocalDiffLowerBound": 0.0001,
        "virtualAsicWorkers": [{"asicNr": 0, "jobsAssigned": 2}],
    }
    before_payload = {"sharesAccepted": 0, "sharesRejected": 0}
    after_payload = {"sharesAccepted": 0, "sharesRejected": 0, "poolDifficulty": 0.0001}

    summary = module.summarize_phase(
        phase,
        tmp_path,
        tmp_path / "runtime" / "out",
        tmp_path / "runtime" / "state",
        wait_payload,
        before_payload,
        after_payload,
        phase_error="",
    )

    assert summary["phaseStatus"] == "PASSED"
    assert summary["acceptedShareProofSource"] == "pool_stats"
    assert summary["acceptedShareDelta"] == 1
    assert summary["poolStatsProofKind"] == "public_pool_bestdiff"
    assert summary["poolStatsBestDifficultyAfter"] == 0.05
    assert summary["poolStatsWorkerBound"] is True
    assert summary["poolStatsAcceptedShareCounter"] is False
    assert summary["poolStatsSupportsDelta"] is False


def test_phase_can_use_source_native_qemu_accepted_response_for_acceptance(tmp_path: Path):
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[0])
    wait_payload = {
        "durationSeconds": 18.0,
        "poolDifficulty": 0.0001,
        "acceptedShareProofSource": "pool_stratum_response",
        "acceptedShareProofSources": ["pool_stratum_response"],
        "qemuAcceptedShareDelta": 1,
        "poolStratumAcceptedShareDelta": 1,
        "poolStratumEvidenceTransport": "qemu_log",
        "qemuPoolIdentity": True,
        "qemuWorkerIdentity": True,
        "qemuSubmitSeen": True,
        "qemuAcceptedShare": True,
        "localDiffSatisfiedByAcceptance": True,
        "provenLocalDiffLowerBound": 0.0001,
    }
    before_payload = {"sharesAccepted": 0, "sharesRejected": 0}
    after_payload = {"sharesAccepted": 0, "sharesRejected": 0, "poolDifficulty": 0.0001}

    summary = module.summarize_phase(
        phase,
        tmp_path,
        tmp_path / "runtime" / "out",
        tmp_path / "runtime" / "state",
        wait_payload,
        before_payload,
        after_payload,
        phase_error="",
    )

    assert summary["phaseStatus"] == "PASSED"
    assert summary["acceptedShareProofSource"] == "pool_stratum_response"
    assert summary["acceptedShareDelta"] == 1
    assert summary["diagnosticAcceptedShareDelta"] == 1
    assert summary["qualificationAcceptedShareDelta"] == 1
    assert summary["qualificationProofSource"] == ""
    assert summary["localAcceptedShareDelta"] == 0
    assert summary["qemuAcceptedShareDelta"] == 1
    assert summary["poolStratumAcceptedShareDelta"] == 1
    assert summary["evidenceTransport"] == "qemu_log"
    assert summary["qemuPoolIdentity"] is True
    assert summary["qemuWorkerIdentity"] is True
    assert summary["qemuSubmitSeen"] is True
    assert summary["qemuAcceptedShare"] is True


def test_qualification_summary_rejects_firmware_and_generic_qemu_without_pool_side_proof(tmp_path: Path):
    module = load_module()
    phase = dict(module.phases_for_mode("qualification")[2])
    wait_payload = {
        "durationSeconds": 0.0,
        "poolDifficulty": 0.001,
        "acceptedShareProofSource": "qemu_log",
        "qemuAcceptedShareDelta": 5,
        "qemuPoolIdentity": True,
        "qemuWorkerIdentity": True,
        "qemuSubmitSeen": True,
        "qemuAcceptedShare": True,
        "poolStatsProofKind": "ckpool_shares",
        "poolStatsAcceptedShareDelta": 0.0,
        "poolStatsAccepted": False,
    }
    before_payload = {"sharesAccepted": 0, "sharesRejected": 0}
    after_payload = {"sharesAccepted": 5, "sharesRejected": 0, "poolDifficulty": 0.001}

    summary = module.summarize_phase(
        phase,
        tmp_path,
        tmp_path / "runtime" / "out",
        tmp_path / "runtime" / "state",
        wait_payload,
        before_payload,
        after_payload,
        phase_error="",
    )

    assert summary["phaseStatus"] == "FAILED"
    assert summary["diagnosticAcceptedShareDelta"] == 5
    assert summary["localAcceptedShareDelta"] == 5
    assert summary["qemuAcceptedShareDelta"] == 5
    assert summary["poolStratumAcceptedShareDelta"] == 0
    assert summary["poolStatsAcceptedShareDelta"] == 0
    assert summary["qualificationAcceptedShareDelta"] == 0
    assert summary["qualificationProofSource"] == ""


def test_qualification_summary_accepts_direct_pool_stratum_response(tmp_path: Path):
    module = load_module()
    phase = dict(module.phases_for_mode("qualification")[0])
    wait_payload = {
        "durationSeconds": 0.0,
        "poolDifficulty": 0.0001,
        "acceptedShareProofSource": "pool_stratum_response",
        "acceptedShareProofSources": ["pool_stratum_response"],
        "qemuAcceptedShareDelta": 5,
        "poolStratumAcceptedShareDelta": 5,
        "poolStratumEvidenceTransport": "qemu_log",
        "qemuPoolIdentity": True,
        "qemuWorkerIdentity": True,
        "qemuSubmitSeen": True,
        "qemuAcceptedShare": True,
        "poolStatsProofKind": "public_pool_bestdiff",
        "poolStatsAcceptedShareDelta": 0.0,
        "poolStatsAccepted": False,
    }
    before_payload = {"sharesAccepted": 0, "sharesRejected": 0}
    after_payload = {"sharesAccepted": 0, "sharesRejected": 0, "poolDifficulty": 0.0001}

    summary = module.summarize_phase(
        phase,
        tmp_path,
        tmp_path / "runtime" / "out",
        tmp_path / "runtime" / "state",
        wait_payload,
        before_payload,
        after_payload,
        phase_error="",
    )

    assert summary["phaseStatus"] == "PASSED"
    assert summary["diagnosticAcceptedShareDelta"] == 5
    assert summary["qualificationAcceptedShareDelta"] == 5
    assert summary["qualificationProofSource"] == "pool_stratum_response"
    assert summary["qualificationProofSources"] == ["pool_stratum_response"]
    assert summary["poolStatsQualificationCapable"] is False


def test_qualification_summary_accepts_pool_stats_even_without_local_or_qemu(tmp_path: Path):
    module = load_module()
    phase = dict(module.phases_for_mode("qualification")[2])
    wait_payload = {
        "durationSeconds": 0.0,
        "poolDifficulty": 0.001,
        "acceptedShareProofSource": "pool_stats",
        "poolStatsProofKind": "ckpool_shares",
        "poolStatsAcceptedShareDelta": 5.0,
        "poolStatsAccepted": True,
    }
    before_payload = {"sharesAccepted": 0, "sharesRejected": 0}
    after_payload = {"sharesAccepted": 0, "sharesRejected": 0, "poolDifficulty": 0.001}

    summary = module.summarize_phase(
        phase,
        tmp_path,
        tmp_path / "runtime" / "out",
        tmp_path / "runtime" / "state",
        wait_payload,
        before_payload,
        after_payload,
        phase_error="",
    )

    assert summary["phaseStatus"] == "PASSED"
    assert summary["diagnosticAcceptedShareDelta"] == 5
    assert summary["qualificationAcceptedShareDelta"] == 5
    assert summary["qualificationProofSource"] == "pool_stats"


def test_nerdminers_qualification_wait_command_requires_pool_stats_delta(monkeypatch):
    module = load_module()
    phase = dict(module.phases_for_mode("qualification")[2])
    captured = {}

    def stub_run_command(command, **kwargs):
        captured["command"] = command
        return module.subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(module, "run_command", stub_run_command)

    module.run_wait_for_phase(
        "http://127.0.0.1:18080",
        phase,
        0,
        0,
        Path("/tmp/qemu.log"),
        0,
        "bitaxe",
        "https://pool.nerdminers.org/users/bc1ptest",
        "",
        "bc1ptest.gamma.nerdminers.rtest",
        3.0,
    )

    command = captured["command"]
    assert "--pool-stats-url" in command
    assert "https://pool.nerdminers.org/users/bc1ptest" in command
    assert "--pool-stats-worker" in command
    assert "bc1ptest.gamma.nerdminers.rtest" in command
    expected_worker_index = command.index("--expected-pool-worker")
    assert command[expected_worker_index + 1] == "bc1ptest.gamma.nerdminers.rtest"
    assert "--pool-stats-baseline-shares" in command
    assert "3.0" in command
    assert "--pool-stats-min-delta" in command
    assert command[command.index("--pool-stats-min-delta") + 1] == "5"
    assert "--require-accepted-log" not in command


def test_smoke_wait_command_does_not_require_pool_stats_delta(monkeypatch):
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[2])
    captured = {}

    def stub_run_command(command, **kwargs):
        captured["command"] = command
        return module.subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(module, "run_command", stub_run_command)

    module.run_wait_for_phase(
        "http://127.0.0.1:18080",
        phase,
        0,
        0,
        Path("/tmp/qemu.log"),
        0,
        "bitaxe",
        "https://pool.nerdminers.org/users/bc1ptest",
        "",
        "bc1ptest.gamma.nerdminers.rtest",
        3.0,
    )

    assert "--pool-stats-min-delta" not in captured["command"]


def test_non_capable_stats_pool_still_runs_wait_helper_for_protocol_proof(monkeypatch):
    module = load_module()
    phase = dict(module.phases_for_mode("qualification")[0])
    captured = {}

    def stub_run_command(command, **kwargs):
        captured["command"] = command
        return module.subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(module, "run_command", stub_run_command)

    module.run_wait_for_phase(
        "http://127.0.0.1:18080",
        phase,
        0,
        0,
        Path("/tmp/qemu.log"),
        0,
        "bitaxe",
        "https://public-pool.io:40557/api/client/bc1ptest",
        "https://public-pool.io:40557/api/client/bc1ptest/vagpubrtest",
        "bc1ptest.vagpubrtest",
        0.0,
        0.01,
    )

    command = captured["command"]
    assert "--pool-stats-kind" in command
    assert "public_pool_bestdiff" in command
    assert "--pool-stats-min-delta" in command
    assert command[command.index("--pool-stats-min-delta") + 1] == "5"
    assert "--expected-pool-worker" in command
    assert "bc1ptest.vagpubrtest" in command


def test_bitronics_wait_command_uses_pool_stats_page_and_auth(monkeypatch):
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[1])
    captured = {}

    def stub_run_command(command, **kwargs):
        captured["command"] = command
        return module.subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(module, "run_command", stub_run_command)

    module.run_wait_for_phase(
        "http://127.0.0.1:18080",
        phase,
        0,
        0,
        Path("/tmp/qemu.log"),
        0,
        "bitaxe",
        "https://pool.bitronics.store/api/stats/bc1ptest",
        "https://pool.bitronics.store/stats/bc1ptest",
        "bc1ptest.vagbitrtest",
        0.0,
        None,
        "2026-04-30T20:00:00.000Z",
    )

    command = captured["command"]
    assert "--pool-stats-kind" in command
    assert "bitronics_status_evidence" in command
    assert "--pool-stats-auth" in command
    assert "bitronics" in command
    assert "--pool-stats-page-url" in command
    assert "https://pool.bitronics.store/stats/bc1ptest" in command
    assert "--pool-stats-baseline-last-share" in command


def test_public_pool_wait_command_uses_worker_stats_endpoint(monkeypatch):
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[0])
    captured = {}

    def stub_run_command(command, **kwargs):
        captured["command"] = command
        return module.subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(module, "run_command", stub_run_command)

    module.run_wait_for_phase(
        "http://127.0.0.1:18080",
        phase,
        0,
        0,
        Path("/tmp/qemu.log"),
        0,
        "bitaxe",
        "https://public-pool.io:40557/api/client/bc1ptest",
        "https://public-pool.io:40557/api/client/bc1ptest/vagpubrtest",
        "bc1ptest.vagpubrtest",
        0.0,
        0.01,
    )

    command = captured["command"]
    assert "--pool-stats-kind" in command
    assert "public_pool_bestdiff" in command
    assert "--pool-stats-page-url" in command
    assert "https://public-pool.io:40557/api/client/bc1ptest/vagpubrtest" in command
    expected_worker_index = command.index("--expected-pool-worker")
    assert command[expected_worker_index + 1] == "bc1ptest.vagpubrtest"
    assert "--pool-stats-baseline-best-difficulty" in command
    assert "0.01" in command


def test_nerdnos_wait_command_does_not_require_bitaxe_virtual_worker_fields(monkeypatch):
    module = load_module()
    phase = dict(module.phases_for_mode("smoke")[0])
    captured = {}

    def stub_run_command(command, **kwargs):
        captured["command"] = command
        return module.subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(module, "run_command", stub_run_command)

    module.run_wait_for_phase(
        "http://127.0.0.1:18080",
        phase,
        0,
        0,
        Path("/tmp/qemu.log"),
        0,
        "nerdnos",
    )

    command = captured["command"]
    assert "--min-worker-count" not in command
    assert "--require-worker-jobs" not in command


def test_release_smoke_helpers_pass_source_name_to_api_browser_and_pool_tests(tmp_path: Path, monkeypatch):
    module = load_module()
    captured_envs = []

    def stub_run_command(command, *, env=None, capture=False, **kwargs):
        captured_envs.append(dict(env or {}))
        return module.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module, "run_command", stub_run_command)

    phase = dict(module.phases_for_mode("smoke")[0])
    module.run_api_smoke("http://127.0.0.1:18080", tmp_path, "nerdnos")
    module.run_browser_smoke("http://127.0.0.1:18080", tmp_path, "nerdnos")
    module.run_pool_connectivity_smoke("http://127.0.0.1:18080", phase, tmp_path, "nerdnos")

    assert [env["SOURCE_NAME"] for env in captured_envs] == ["nerdnos", "nerdnos", "nerdnos"]
