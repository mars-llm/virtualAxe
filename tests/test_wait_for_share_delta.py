import importlib.util
import itertools
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT_DIR / "scripts" / "wait-for-share-delta.py"


def load_module():
    spec = importlib.util.spec_from_file_location("wait_for_share_delta_test_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_proven_local_diff_uses_observed_value_when_above_threshold():
    module = load_module()
    log_text = "I (42) asic_result: ID: abc, ASIC nr: 0, Core: 0/0, ver: 20000000 Nonce 00000001 diff 512.5 of 512.\n"

    proven, inferred = module.proven_local_diff_lower_bound(
        log_text,
        pool_difficulty=512.0,
        accepted_seen=False,
    )

    assert proven == 512.5
    assert inferred is False


def test_proven_local_diff_falls_back_to_pool_difficulty_when_acceptance_proves_rounded_share():
    module = load_module()
    log_text = "\n".join(
        [
            "I (42) asic_result: ID: abc, ASIC nr: 0, Core: 0/0, ver: 20000000 Nonce 00000001 diff 0.0 of 0.001.",
            "I (43) stratum_task: message result accepted",
        ]
    )

    proven, inferred = module.proven_local_diff_lower_bound(
        log_text,
        pool_difficulty=0.001,
        accepted_seen=True,
    )

    assert proven == 0.001
    assert inferred is True


def test_qemu_accepted_share_proof_requires_pool_worker_submit_and_acceptance():
    module = load_module()
    log_text = "\n".join(
        [
            "I (10) stratum task (Pri): Connecting to: stratum+tcp://pool.nerdminers.org:3333 (144.91.83.152)",
            'I (11) stratum_api: tx: {"id": 3, "method": "mining.authorize", "params": ["bc1ptest.vagnerd", "x"]}',
            'I (12) stratum_api: tx: {"id": 7, "method": "mining.submit", "params": ["bc1ptest.vagnerd", "job", "00000001", "6a01de9b", "000011c7", "00002000"]}',
            "I (13) stratum task (Pri): message result accepted",
        ]
    )

    proof = module.qemu_accepted_share_proof(
        log_text,
        expected_host="pool.nerdminers.org",
        expected_port=3333,
        expected_worker="bc1ptest.vagnerd",
    )

    assert proof == {
        "qemuPoolIdentity": True,
        "qemuWorkerIdentity": True,
        "qemuSubmitSeen": True,
        "qemuAcceptedShare": True,
        "qemuAcceptedShareDelta": 1,
        "poolStratumAcceptedShare": True,
        "poolStratumAcceptedShareDelta": 1,
        "poolStratumProofSource": "pool_stratum_response",
        "poolStratumEvidenceTransport": "qemu_log",
    }


def test_qemu_accepted_share_proof_rejects_wrong_worker():
    module = load_module()
    log_text = "\n".join(
        [
            "I (10) stratum task (Pri): Connecting to: stratum+tcp://pool.nerdminers.org:3333 (144.91.83.152)",
            'I (12) stratum_api: tx: {"id": 7, "method": "mining.submit", "params": ["bc1ptest.other", "job", "00000001", "6a01de9b", "000011c7", "00002000"]}',
            "I (13) stratum task (Pri): message result accepted",
        ]
    )

    proof = module.qemu_accepted_share_proof(
        log_text,
        expected_host="pool.nerdminers.org",
        expected_port=3333,
        expected_worker="bc1ptest.vagnerd",
    )

    assert proof["qemuPoolIdentity"] is True
    assert proof["qemuWorkerIdentity"] is False
    assert proof["qemuSubmitSeen"] is False
    assert proof["qemuAcceptedShare"] is False
    assert proof["poolStratumAcceptedShareDelta"] == 0


def test_qemu_accepted_share_proof_rejects_wrong_pool():
    module = load_module()
    log_text = "\n".join(
        [
            "I (10) stratum task (Pri): Connecting to: stratum+tcp://public-pool.io:3333 (1.2.3.4)",
            'I (11) stratum_api: tx: {"id": 3, "method": "mining.authorize", "params": ["bc1ptest.vagnerd", "x"]}',
            'I (12) stratum_api: tx: {"id": 7, "method": "mining.submit", "params": ["bc1ptest.vagnerd", "job", "00000001", "6a01de9b", "000011c7", "00002000"]}',
            "I (13) stratum task (Pri): message result accepted",
        ]
    )

    proof = module.qemu_accepted_share_proof(
        log_text,
        expected_host="pool.nerdminers.org",
        expected_port=3333,
        expected_worker="bc1ptest.vagnerd",
    )

    assert proof["qemuPoolIdentity"] is False
    assert proof["qemuWorkerIdentity"] is True
    assert proof["qemuSubmitSeen"] is True
    assert proof["qemuAcceptedShare"] is False
    assert proof["poolStratumAcceptedShareDelta"] == 0


def test_qemu_accepted_share_proof_rejects_accept_without_submit():
    module = load_module()
    log_text = "\n".join(
        [
            "I (10) stratum task (Pri): Connecting to: stratum+tcp://pool.nerdminers.org:3333 (144.91.83.152)",
            'I (11) stratum_api: tx: {"id": 3, "method": "mining.authorize", "params": ["bc1ptest.vagnerd", "x"]}',
            "I (13) stratum task (Pri): message result accepted",
        ]
    )

    proof = module.qemu_accepted_share_proof(
        log_text,
        expected_host="pool.nerdminers.org",
        expected_port=3333,
        expected_worker="bc1ptest.vagnerd",
    )

    assert proof["qemuPoolIdentity"] is True
    assert proof["qemuWorkerIdentity"] is True
    assert proof["qemuSubmitSeen"] is False
    assert proof["qemuAcceptedShare"] is False
    assert proof["poolStratumAcceptedShareDelta"] == 0


def test_qemu_accepted_share_proof_counts_verified_pool_stratum_acceptances():
    module = load_module()
    log_text = "\n".join(
        [
            "I (10) stratum task (Pri): Connecting to: stratum+tcp://pool.nerdminers.org:3333 (144.91.83.152)",
            'I (11) stratum_api: tx: {"id": 3, "method": "mining.authorize", "params": ["bc1ptest.vagnerd", "x"]}',
            'I (12) stratum_api: tx: {"id": 7, "method": "mining.submit", "params": ["bc1ptest.vagnerd", "job", "00000001", "6a01de9b", "000011c7", "00002000"]}',
            "I (13) stratum task (Pri): message result accepted",
            'I (14) stratum_api: tx: {"id": 8, "method": "mining.submit", "params": ["bc1ptest.vagnerd", "job", "00000002", "6a01de9b", "000011c8", "00002000"]}',
            "I (15) stratum task (Pri): message result accepted",
        ]
    )

    proof = module.qemu_accepted_share_proof(
        log_text,
        expected_host="pool.nerdminers.org",
        expected_port=3333,
        expected_worker="bc1ptest.vagnerd",
    )

    assert proof["qemuAcceptedShareDelta"] == 2
    assert proof["poolStratumAcceptedShareDelta"] == 2
    assert proof["poolStratumProofSource"] == "pool_stratum_response"


def test_wait_progress_payload_reports_pool_side_qualification_state():
    module = load_module()
    payload = module.wait_progress_payload(
        {
            "sharesAccepted": 8,
            "sharesRejectedDelta": 0,
            "poolDifficulty": 0.001,
            "requiredDurationSeconds": 0,
        },
        elapsed_seconds=123.45,
        deadline=200.0,
        now=150.0,
        min_delta=5.0,
        qemu_proof={
            "qemuPoolIdentity": True,
            "qemuWorkerIdentity": True,
            "qemuSubmitSeen": True,
            "poolStratumAcceptedShareDelta": 4,
        },
        pool_stats_delta=2.0,
        pool_stats_accepted=False,
    )

    assert payload["elapsedSeconds"] == 123.45
    assert payload["remainingSeconds"] == 50.0
    assert payload["firmwareApiAcceptedShareDelta"] == 8
    assert payload["poolStratumAcceptedShareDelta"] == 4.0
    assert payload["poolStatsAcceptedShareDelta"] == 2.0
    assert payload["qualificationAcceptedShareDelta"] == 4.0
    assert payload["requiredAcceptedShareDelta"] == 5.0
    assert payload["qemuPoolIdentity"] is True
    assert payload["qemuWorkerIdentity"] is True
    assert payload["qemuSubmitSeen"] is True


def test_qemu_accepted_share_proof_ignores_stale_pre_phase_log(tmp_path: Path):
    module = load_module()
    qemu_log = tmp_path / "qemu.log"
    qemu_log.write_text(
        "\n".join(
            [
                "I (10) stratum task (Pri): Connecting to: stratum+tcp://pool.nerdminers.org:3333 (144.91.83.152)",
                'I (11) stratum_api: tx: {"id": 3, "method": "mining.authorize", "params": ["bc1ptest.vagnerd", "x"]}',
                'I (12) stratum_api: tx: {"id": 7, "method": "mining.submit", "params": ["bc1ptest.vagnerd", "job", "00000001", "6a01de9b", "000011c7", "00002000"]}',
                "I (13) stratum task (Pri): message result accepted",
                "",
            ]
        ),
        encoding="utf-8",
    )
    phase_start = qemu_log.stat().st_size
    qemu_log.write_text(qemu_log.read_text(encoding="utf-8") + "I (20) next phase booted\n", encoding="utf-8")

    proof = module.qemu_accepted_share_proof(
        module.read_log_since(str(qemu_log), phase_start),
        expected_host="pool.nerdminers.org",
        expected_port=3333,
        expected_worker="bc1ptest.vagnerd",
    )

    assert proof == {
        "qemuPoolIdentity": False,
        "qemuWorkerIdentity": False,
        "qemuSubmitSeen": False,
        "qemuAcceptedShare": False,
        "qemuAcceptedShareDelta": 0,
        "poolStratumAcceptedShare": False,
        "poolStratumAcceptedShareDelta": 0,
        "poolStratumProofSource": "",
        "poolStratumEvidenceTransport": "",
    }


def test_qemu_accepted_share_cannot_replace_api_health(monkeypatch, tmp_path: Path):
    module = load_module()
    qemu_log = tmp_path / "qemu.log"
    qemu_log.write_text(
        "\n".join(
            [
                "I (10) stratum task (Pri): Connecting to: stratum+tcp://pool.nerdminers.org:3333 (144.91.83.152)",
                'I (11) stratum_api: tx: {"id": 3, "method": "mining.authorize", "params": ["bc1ptest.vagnerd", "x"]}',
                'I (12) stratum_api: tx: {"id": 7, "method": "mining.submit", "params": ["bc1ptest.vagnerd", "job", "00000001", "6a01de9b", "000011c7", "00002000"]}',
                "I (13) stratum task (Pri): message result accepted",
                "",
            ]
        ),
        encoding="utf-8",
    )
    args = module.argparse.Namespace(
        base_url="http://127.0.0.1:18080",
        timeout=0.5,
        min_duration_seconds=0.0,
        min_shares=1,
        baseline_rejected=None,
        max_rejected_delta=None,
        expected_pool_host="pool.nerdminers.org",
        expected_pool_port=3333,
        expected_pool_worker="bc1ptest.vagnerd",
        min_worker_count=0,
        require_worker_jobs=False,
        require_accepted_share=True,
        qemu_log=str(qemu_log),
        qemu_log_start=0,
        require_accepted_log=False,
        min_local_diff=None,
        require_local_diff_at_least_pool_difficulty=False,
        pool_stats_url=None,
        pool_stats_page_url=None,
        pool_stats_kind="ckpool_shares",
        pool_stats_auth="",
        pool_stats_worker=None,
        pool_stats_baseline_shares=None,
        pool_stats_baseline_best_difficulty=None,
        pool_stats_baseline_last_share=None,
        pool_stats_min_delta=None,
    )
    times = itertools.chain([0.0, 0.0, 0.0, 0.0, 1.0], itertools.repeat(2.0))

    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(module, "get_json", lambda _url: (_ for _ in ()).throw(RuntimeError("api down")))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.time, "time", lambda: next(times))

    try:
        module.main()
    except SystemExit as exc:
        assert "Last payload: none" in str(exc)
    else:
        raise AssertionError("QEMU accepted proof passed without API health")


def test_pool_side_min_delta_rejects_firmware_api_only_proof(monkeypatch):
    module = load_module()
    args = module.argparse.Namespace(
        base_url="http://127.0.0.1:18080",
        timeout=0.5,
        min_duration_seconds=0.0,
        min_shares=5,
        baseline_rejected=0,
        max_rejected_delta=0,
        expected_pool_host="pool.nerdminers.org",
        expected_pool_port=3333,
        expected_pool_worker="bc1ptest.vagnerd",
        min_worker_count=0,
        require_worker_jobs=False,
        require_accepted_share=True,
        qemu_log=None,
        qemu_log_start=0,
        require_accepted_log=False,
        min_local_diff=None,
        require_local_diff_at_least_pool_difficulty=False,
        pool_stats_url="https://pool.nerdminers.org/users/bc1ptest",
        pool_stats_page_url=None,
        pool_stats_kind="ckpool_shares",
        pool_stats_auth="",
        pool_stats_worker="bc1ptest.vagnerd",
        pool_stats_baseline_shares=0.0,
        pool_stats_baseline_best_difficulty=None,
        pool_stats_baseline_last_share=None,
        pool_stats_min_delta=5.0,
    )
    times = itertools.chain([0.0, 0.0, 0.1], itertools.repeat(1.0))

    def fake_get_json(url, _headers=None):
        if url.endswith("/api/system/info"):
            return {
                "sharesAccepted": 5,
                "sharesRejected": 0,
                "stratumURL": "pool.nerdminers.org",
                "stratumPort": 3333,
                "poolDifficulty": 0.001,
            }
        return {"worker": [{"workername": "bc1ptest.vagnerd", "shares": 4.0}]}

    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(module, "get_json", fake_get_json)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.time, "time", lambda: next(times))

    try:
        module.main()
    except SystemExit as exc:
        assert "Timed out waiting" in str(exc)
    else:
        raise AssertionError("firmware/API evidence satisfied a pool-side share threshold")


def test_pool_side_min_delta_rejects_rejected_share_even_with_five_pool_accepts(monkeypatch, tmp_path: Path):
    module = load_module()
    qemu_log = tmp_path / "qemu.log"
    lines = [
        "I (10) stratum task (Pri): Connecting to: stratum+tcp://pool.nerdminers.org:3333 (144.91.83.152)",
        'I (11) stratum_api: tx: {"id": 3, "method": "mining.authorize", "params": ["bc1ptest.vagnerd", "x"]}',
    ]
    for index in range(5):
        lines.extend(
            [
                f'I (12) stratum_api: tx: {{"id": {index + 7}, "method": "mining.submit", "params": ["bc1ptest.vagnerd", "job", "00000001", "6a01de9b", "{index:08x}", "00002000"]}}',
                "I (13) stratum task (Pri): message result accepted",
            ]
        )
    qemu_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    args = module.argparse.Namespace(
        base_url="http://127.0.0.1:18080",
        timeout=1.0,
        min_duration_seconds=0.0,
        min_shares=5,
        baseline_rejected=0,
        max_rejected_delta=0,
        expected_pool_host="pool.nerdminers.org",
        expected_pool_port=3333,
        expected_pool_worker="bc1ptest.vagnerd",
        min_worker_count=0,
        require_worker_jobs=False,
        require_accepted_share=True,
        qemu_log=str(qemu_log),
        qemu_log_start=0,
        require_accepted_log=False,
        min_local_diff=None,
        require_local_diff_at_least_pool_difficulty=False,
        pool_stats_url=None,
        pool_stats_page_url=None,
        pool_stats_kind="ckpool_shares",
        pool_stats_auth="",
        pool_stats_worker=None,
        pool_stats_baseline_shares=None,
        pool_stats_baseline_best_difficulty=None,
        pool_stats_baseline_last_share=None,
        pool_stats_min_delta=5.0,
    )
    times = itertools.chain([0.0, 0.0, 0.1], itertools.repeat(2.0))

    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(
        module,
        "get_json",
        lambda _url: {
            "sharesAccepted": 5,
            "sharesRejected": 1,
            "stratumURL": "pool.nerdminers.org",
            "stratumPort": 3333,
            "poolDifficulty": 0.001,
        },
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.time, "time", lambda: next(times))

    try:
        module.main()
    except SystemExit as exc:
        assert "Rejected share delta exceeded" in str(exc)
    else:
        raise AssertionError("rejected share delta did not fail qualification")


def test_pool_side_wait_fails_fast_without_expected_stratum_identity(monkeypatch, tmp_path: Path):
    module = load_module()
    qemu_log = tmp_path / "qemu.log"
    qemu_log.write_text("I (10) booted without pool connection\n", encoding="utf-8")
    args = module.argparse.Namespace(
        base_url="http://127.0.0.1:18080",
        timeout=1000.0,
        min_duration_seconds=0.0,
        min_shares=5,
        baseline_rejected=0,
        max_rejected_delta=0,
        expected_pool_host="pool.nerdminers.org",
        expected_pool_port=3333,
        expected_pool_worker="bc1ptest.vagnerd",
        min_worker_count=0,
        require_worker_jobs=False,
        require_accepted_share=True,
        qemu_log=str(qemu_log),
        qemu_log_start=0,
        require_accepted_log=False,
        min_local_diff=None,
        require_local_diff_at_least_pool_difficulty=False,
        pool_stats_url=None,
        pool_stats_page_url=None,
        pool_stats_kind="ckpool_shares",
        pool_stats_auth="",
        pool_stats_worker=None,
        pool_stats_baseline_shares=None,
        pool_stats_baseline_best_difficulty=None,
        pool_stats_baseline_last_share=None,
        pool_stats_min_delta=5.0,
    )
    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 61.0
        return clock["now"]

    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(
        module,
        "get_json",
        lambda _url: {
            "sharesAccepted": 0,
            "sharesRejected": 0,
            "stratumURL": "pool.nerdminers.org",
            "stratumPort": 3333,
            "poolDifficulty": 0.001,
        },
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.time, "time", fake_time)

    try:
        module.main()
    except SystemExit as exc:
        assert "No verified live Stratum connection" in str(exc)
        assert "pool.nerdminers.org:3333" in str(exc)
    else:
        raise AssertionError("wait helper did not fail fast without expected Stratum identity")


def test_pool_side_min_delta_accepts_verified_pool_stratum_response(monkeypatch, tmp_path: Path, capsys):
    module = load_module()
    qemu_log = tmp_path / "qemu.log"
    lines = [
        "I (10) stratum task (Pri): Connecting to: stratum+tcp://pool.nerdminers.org:3333 (144.91.83.152)",
        'I (11) stratum_api: tx: {"id": 3, "method": "mining.authorize", "params": ["bc1ptest.vagnerd", "x"]}',
    ]
    for index in range(5):
        lines.extend(
            [
                f'I (12) stratum_api: tx: {{"id": {index + 7}, "method": "mining.submit", "params": ["bc1ptest.vagnerd", "job", "00000001", "6a01de9b", "{index:08x}", "00002000"]}}',
                "I (13) stratum task (Pri): message result accepted",
            ]
        )
    qemu_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    args = module.argparse.Namespace(
        base_url="http://127.0.0.1:18080",
        timeout=1.0,
        min_duration_seconds=0.0,
        min_shares=5,
        baseline_rejected=0,
        max_rejected_delta=0,
        expected_pool_host="pool.nerdminers.org",
        expected_pool_port=3333,
        expected_pool_worker="bc1ptest.vagnerd",
        min_worker_count=0,
        require_worker_jobs=False,
        require_accepted_share=True,
        qemu_log=str(qemu_log),
        qemu_log_start=0,
        require_accepted_log=False,
        min_local_diff=None,
        require_local_diff_at_least_pool_difficulty=False,
        pool_stats_url="https://pool.nerdminers.org/users/bc1ptest",
        pool_stats_page_url=None,
        pool_stats_kind="ckpool_shares",
        pool_stats_auth="",
        pool_stats_worker="bc1ptest.vagnerd",
        pool_stats_baseline_shares=0.0,
        pool_stats_baseline_best_difficulty=None,
        pool_stats_baseline_last_share=None,
        pool_stats_min_delta=5.0,
    )
    times = itertools.chain([0.0, 0.0, 0.1, 0.1], itertools.repeat(2.0))

    def fake_get_json(url, _headers=None):
        if url.endswith("/api/system/info"):
            return {
                "sharesAccepted": 0,
                "sharesRejected": 0,
                "stratumURL": "pool.nerdminers.org",
                "stratumPort": 3333,
                "poolDifficulty": 0.001,
            }
        return {"worker": [{"workername": "bc1ptest.vagnerd", "shares": 4.0}]}

    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(module, "get_json", fake_get_json)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.time, "time", lambda: next(times))

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["acceptedShareProofSource"] == "pool_stratum_response"
    assert payload["qualificationProofSource"] == "pool_stratum_response"
    assert payload["poolStratumAcceptedShareDelta"] == 5
    assert payload["poolStatsAcceptedShareDelta"] == 4.0
    assert payload["poolStatsAccepted"] is False


def test_pool_stats_min_delta_accepts_worker_bound_pool_share_count(monkeypatch, capsys):
    module = load_module()
    args = module.argparse.Namespace(
        base_url="http://127.0.0.1:18080",
        timeout=1.0,
        min_duration_seconds=0.0,
        min_shares=5,
        baseline_rejected=0,
        max_rejected_delta=0,
        expected_pool_host="pool.nerdminers.org",
        expected_pool_port=3333,
        expected_pool_worker="bc1ptest.vagnerd",
        min_worker_count=0,
        require_worker_jobs=False,
        require_accepted_share=True,
        qemu_log=None,
        qemu_log_start=0,
        require_accepted_log=False,
        min_local_diff=None,
        require_local_diff_at_least_pool_difficulty=False,
        pool_stats_url="https://pool.nerdminers.org/users/bc1ptest",
        pool_stats_page_url=None,
        pool_stats_kind="ckpool_shares",
        pool_stats_auth="",
        pool_stats_worker="bc1ptest.vagnerd",
        pool_stats_baseline_shares=0.0,
        pool_stats_baseline_best_difficulty=None,
        pool_stats_baseline_last_share=None,
        pool_stats_min_delta=5.0,
    )
    times = itertools.chain([0.0, 0.0, 0.1, 0.1], itertools.repeat(2.0))

    def fake_get_json(url, _headers=None):
        if url.endswith("/api/system/info"):
            return {
                "sharesAccepted": 0,
                "sharesRejected": 0,
                "stratumURL": "pool.nerdminers.org",
                "stratumPort": 3333,
                "poolDifficulty": 0.001,
            }
        return {"worker": [{"workername": "bc1ptest.vagnerd", "shares": 5.0}]}

    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(module, "get_json", fake_get_json)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.time, "time", lambda: next(times))

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["acceptedShareProofSource"] == "pool_stats"
    assert payload["poolStatsAcceptedShareDelta"] == 5.0
    assert payload["poolStatsAccepted"] is True


def test_pool_side_min_delta_rejects_bestdiff_without_stratum_proof(monkeypatch):
    module = load_module()
    args = module.argparse.Namespace(
        base_url="http://127.0.0.1:18080",
        timeout=1.0,
        min_duration_seconds=0.0,
        min_shares=5,
        baseline_rejected=0,
        max_rejected_delta=0,
        expected_pool_host="public-pool.io",
        expected_pool_port=3333,
        expected_pool_worker="bc1ptest.vagpub",
        min_worker_count=0,
        require_worker_jobs=False,
        require_accepted_share=True,
        qemu_log=None,
        qemu_log_start=0,
        require_accepted_log=False,
        min_local_diff=None,
        require_local_diff_at_least_pool_difficulty=False,
        pool_stats_url="https://public-pool.io:40557/api/client/bc1ptest",
        pool_stats_page_url="https://public-pool.io:40557/api/client/bc1ptest/vagpub",
        pool_stats_kind="public_pool_bestdiff",
        pool_stats_auth="",
        pool_stats_worker="bc1ptest.vagpub",
        pool_stats_baseline_shares=0.0,
        pool_stats_baseline_best_difficulty=0.0,
        pool_stats_baseline_last_share=None,
        pool_stats_min_delta=5.0,
    )
    times = itertools.chain([0.0, 0.0, 0.1], itertools.repeat(2.0))

    def fake_get_json(url, _headers=None):
        if url.endswith("/api/system/info"):
            return {
                "sharesAccepted": 5,
                "sharesRejected": 0,
                "stratumURL": "public-pool.io",
                "stratumPort": 3333,
                "poolDifficulty": 0.0001,
            }
        if "/vagpub" in url:
            return {"chartData": [{"data": module.PUBLIC_POOL_DIFF1_HASHES * 0.0001 / 600.0}]}
        return {"workers": [{"name": "vagpub", "bestDifficulty": "1.0"}]}

    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(module, "get_json", fake_get_json)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.time, "time", lambda: next(times))

    try:
        module.main()
    except SystemExit as exc:
        assert "Timed out waiting" in str(exc)
    else:
        raise AssertionError("bestdiff evidence satisfied strict qualification pool-stats min delta")


def test_pool_side_min_delta_rejects_bitronics_status_without_stratum_proof(monkeypatch):
    module = load_module()
    args = module.argparse.Namespace(
        base_url="http://127.0.0.1:18080",
        timeout=1.0,
        min_duration_seconds=0.0,
        min_shares=5,
        baseline_rejected=0,
        max_rejected_delta=0,
        expected_pool_host="pool.bitronics.store",
        expected_pool_port=3334,
        expected_pool_worker="bc1ptest.vagbit",
        min_worker_count=0,
        require_worker_jobs=False,
        require_accepted_share=True,
        qemu_log=None,
        qemu_log_start=0,
        require_accepted_log=False,
        min_local_diff=None,
        require_local_diff_at_least_pool_difficulty=False,
        pool_stats_url="https://pool.bitronics.store/api/stats/bc1ptest",
        pool_stats_page_url="https://pool.bitronics.store/stats/bc1ptest",
        pool_stats_kind="bitronics_status_evidence",
        pool_stats_auth="bitronics",
        pool_stats_worker="bc1ptest.vagbit",
        pool_stats_baseline_shares=0.0,
        pool_stats_baseline_best_difficulty=0.0,
        pool_stats_baseline_last_share="2026-05-14T20:00:00.000Z",
        pool_stats_min_delta=5.0,
    )
    times = itertools.chain([0.0, 0.0, 0.1], itertools.repeat(2.0))

    def fake_get_json(url, _headers=None):
        if url.endswith("/api/system/info"):
            return {
                "sharesAccepted": 5,
                "sharesRejected": 0,
                "stratumURL": "pool.bitronics.store",
                "stratumPort": 3334,
                "poolDifficulty": 0.0005,
            }
        return {
            "data": {
                "pools": [
                    {
                        "pool": "nerd",
                        "lastShare": "2026-05-14T20:01:00.000Z",
                        "workers": 1,
                    }
                ]
            }
        }

    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(module, "get_json", fake_get_json)
    monkeypatch.setattr(module, "bitronics_pool_headers", lambda: {})
    monkeypatch.setattr(
        module,
        "get_text",
        lambda _url, _headers=None: '<tr><td>vagbit</td><td><span class="status-indicator active"></span></td></tr>',
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.time, "time", lambda: next(times))

    try:
        module.main()
    except SystemExit as exc:
        assert "Timed out waiting" in str(exc)
    else:
        raise AssertionError("Bitronics status evidence satisfied strict qualification pool-stats min delta")


def test_pool_stats_worker_shares_reads_exact_worker_only():
    module = load_module()
    payload = {
        "worker": [
            {"workername": "pooluser.gamma.nerdminers.old", "shares": 9.0},
            {"workername": "pooluser.gamma.nerdminers.run", "shares": 4.0},
        ]
    }

    assert module.pool_stats_worker_shares(payload, "pooluser.gamma.nerdminers.run") == 4.0
    assert module.pool_stats_worker_shares(payload, "pooluser.gamma.nerdminers.missing") == 0.0


def test_bitronics_last_share_parser_reads_iso_timestamp():
    module = load_module()

    assert module.parse_bitronics_last_share("2026-04-30T20:07:15.000Z") > 0
    assert module.parse_bitronics_last_share(None) == 0.0
    assert module.parse_bitronics_last_share("not-a-date") == 0.0


def test_bitronics_worker_active_reads_exact_worker_row():
    module = load_module()
    html = """
    <table>
      <tr>
        <td class="worker-cell">vagbitold</td>
        <td class="status-cell"><span class="status-indicator inactive"></span></td>
      </tr>
      <tr>
        <td class="worker-cell">vagbitrtest</td>
        <td class="status-cell"><span class="status-indicator active"></span></td>
      </tr>
    </table>
    """

    assert module.bitronics_worker_active(html, "vagbitrtest") is True
    assert module.bitronics_worker_active(html, "vagbitmissing") is False


def test_bitronics_worker_active_accepts_display_suffix_for_wallet_worker():
    module = load_module()
    html = """
    <table>
      <tr>
        <td class="worker-cell">vagbitrtest</td>
        <td class="status-cell"><span class="status-indicator active"></span></td>
      </tr>
    </table>
    """

    assert module.bitronics_worker_active(html, "bc1ptest.vagbitrtest") is True


def test_public_pool_worker_matches_display_suffix_and_uses_best_difficulty():
    module = load_module()
    payload = {
        "workers": [
            {"name": "vagpubrtest", "bestDifficulty": "0.02"},
            {"name": "vagpubrtest", "bestDifficulty": "0.05"},
            {"name": "other", "bestDifficulty": "9.00"},
        ]
    }

    worker = module.public_pool_worker(payload, "bc1ptest.vagpubrtest")

    assert worker["bestDifficulty"] == "0.05"
    assert module.public_pool_best_difficulty(worker) == 0.05


def test_public_pool_chart_accepted_count_inverts_hashrate_to_share_count():
    module = load_module()
    payload = {
        "chartData": [
            {"data": module.PUBLIC_POOL_DIFF1_HASHES * 0.0001 / 600.0},
            {"data": module.PUBLIC_POOL_DIFF1_HASHES * 0.0002 / 600.0},
        ]
    }

    assert module.public_pool_chart_accepted_count(payload, 0.0001) == 3.0


def test_public_pool_bestdiff_only_counts_when_worker_reaches_assigned_difficulty():
    module = load_module()

    assert module.public_pool_bestdiff_share_delta(
        worker_active=True,
        best_difficulty_before=0.0,
        best_difficulty_after=0.00009,
        pool_difficulty=0.0001,
    ) == 0.0
    assert module.public_pool_bestdiff_share_delta(
        worker_active=True,
        best_difficulty_before=0.0,
        best_difficulty_after=0.0001,
        pool_difficulty=0.0001,
    ) == 1.0
    assert module.public_pool_bestdiff_share_delta(
        worker_active=True,
        best_difficulty_before=0.0002,
        best_difficulty_after=0.0002,
        pool_difficulty=0.0001,
    ) == 0.0
    assert module.public_pool_bestdiff_share_delta(
        worker_active=False,
        best_difficulty_before=0.0,
        best_difficulty_after=1.0,
        pool_difficulty=0.0001,
    ) == 0.0
