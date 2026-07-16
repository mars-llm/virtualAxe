import json
import re
import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
PATCH_DIR = ROOT_DIR / "patches" / "esp-miner"
BITAXE_PATCH_DIR = PATCH_DIR / "bitaxe"


EXPECTED_PATCH_SUBJECTS = {
    "0001-virtual-gamma-add-qemu-firmware-foundation.patch": "virtual gamma: add qemu firmware foundation",
    "0002-virtual-mining-keep-stratum-and-worker-responsive.patch": "virtual mining: keep stratum and worker responsive",
    "0003-virtual-axeos-support-qemu-partitions-and-openeth-ui.patch": "virtual axeos: support qemu partitions and openeth ui",
    "0004-virtual-mining-preserve-work-and-reduce-nonce-overhead.patch": "virtual mining: preserve work and reduce nonce overhead",
    "0005-virtual-gamma-apply-profile-metadata-and-deterministic-sensors.patch": "virtual gamma: apply profile metadata and sensors",
    "0006-virtual-mining-precompute-nonce-search-material.patch": "virtual mining: precompute nonce search material",
    "0007-virtual-api-handle-qemu-patch-and-static-responses.patch": "virtual api: handle qemu patch and static responses",
    "0008-virtual-mining-keep-guest-worker-responsive-under-qemu.patch": "virtual mining: keep guest worker responsive under qemu",
    "0044-virtual-share-canonical-header-material.patch": "virtual: share canonical header material",
    "0045-virtual-align-guest-digest-path-with-software-validator.patch": "virtual: align digest path with validator",
    "0046-virtual-guard-submit-boundary-with-work-generations.patch": "virtual: guard submit boundary with work generations",
    "0047-virtual-api-keep-settings-updates-responsive.patch": "virtual api: keep settings updates responsive",
    "0048-virtual-pool-support-low-difficulty-interoperability.patch": "virtual pool: support low-difficulty interoperability",
}

EXPECTED_PATCH_KEEP_REASONS = {
    "0001-virtual-gamma-add-qemu-firmware-foundation.patch": "Required to compile and boot a virtual Gamma board under ESP32-S3 QEMU.",
    "0002-virtual-mining-keep-stratum-and-worker-responsive.patch": "Prevents stale Stratum work from blocking fresh jobs while the guest scans nonces.",
    "0003-virtual-axeos-support-qemu-partitions-and-openeth-ui.patch": "Makes AxeOS, HTTP, NVS, and the network form work against QEMU loopback/OpenETH.",
    "0004-virtual-mining-preserve-work-and-reduce-nonce-overhead.patch": "Cuts guest per-nonce overhead without discarding valid in-flight pool work.",
    "0005-virtual-gamma-apply-profile-metadata-and-deterministic-sensors.patch": "Makes device identity, lanes, and thermal telemetry deterministic for repeatable QEMU/API/browser gates.",
    "0006-virtual-mining-precompute-nonce-search-material.patch": "Moves invariant SHA header setup out of the nonce loop so live low-difficulty shares are feasible inside the guest.",
    "0007-virtual-api-handle-qemu-patch-and-static-responses.patch": "Prevents API settings updates from corrupting responses or leaving runtime config stale.",
    "0008-virtual-mining-keep-guest-worker-responsive-under-qemu.patch": "Bounds mining batches so HTTP, NVS, and Stratum tasks keep running under QEMU load.",
    "0044-virtual-share-canonical-header-material.patch": "Establishes one block-header byte contract for guest search, validation, and submit.",
    "0045-virtual-align-guest-digest-path-with-software-validator.patch": "Keeps the fast digest filter aligned with the validator while preserving rolled-version submit behavior.",
    "0046-virtual-guard-submit-boundary-with-work-generations.patch": "Stops clean-jobs-invalidated work from reaching submit after a candidate is found.",
    "0047-virtual-api-keep-settings-updates-responsive.patch": "Prevents repeated AxeOS settings writes from stalling the API while preserving persisted values.",
    "0048-virtual-pool-support-low-difficulty-interoperability.patch": "Makes the virtual miner interoperate with the low-difficulty public pools used for release evidence.",
}
EXPECTED_NERDNOS_PATCH_KEEP_REASONS = {
    "0001-nerdnos-add-virtual-gamma-api-boot-path.patch": "Required to boot the NerdNos fork as virtual Gamma in ESP32-S3 QEMU.",
    "0002-nerdnos-add-virtual-asic-submit-path.patch": "Adds the NerdNos-native virtual ASIC path and guards stale work at submit.",
    "0003-nerdnos-keep-virtual-mining-api-responsive.patch": "Keeps NerdNos pool work fresh without starving the source-native HTTP/API tasks.",
    "0004-nerdnos-low-difficulty-pool-interoperability.patch": "Preserves fractional pool difficulty and Stratum setup ordering required by public low-difficulty pools.",
    "0005-nerdnos-precompute-virtual-nonce-search-material.patch": "Keeps NerdNos live-share throughput inside the guest by precomputing invariant header material.",
    "0006-nerdnos-brand-virtualaxe-header.patch": "Fixes source-specific UI branding for the shipped virtual runtime screenshots.",
}


FORBIDDEN_ACTIVE_DOC_PATTERNS = [
    r"\bAtlas\b",
    r"future profiles?",
    r"future profile work",
    r"verify-live",
    r"run-tap",
    r"network-modes\.md",
    r"co" r"dex-session-handover",
    r"major-release-mining-handoff",
    r"virtual-asic-mining-follow-up",
    r"SparkMiner",
    r"virtualAxe-clickdummy",
    r"HostilePool",
    r"Progress Log",
    r"Outstanding Work",
    r"Post-Release Work",
    r"under active construction",
    r"phase-one",
    r"As of April",
    r"\bfake(?:d|ing)?\b",
    r"mock accepted shares",
    r"0006-network-modes",
    r"0007-build-fixes",
    r"0008-virtual-nvs-fallback",
    r"0009-virtual-api-avoid-partition-lookups",
    r"0014-virtual-partitions-fix",
    r"0015-virtual-running-partition",
    r"0016-virtual-partition-iterators",
    r"0017-virtual-stratum-tolerate-partial-coinbase-metadata",
    r"0018-axe-os-brand-virtualaxe-ui",
    r"0021-virtual-network-form-detect-openeth-via-networkType",
    r"0022-axe-os-add-bitronics-quicklink",
    r"0037-virtual-batch-settings-updates",
    r"0041-virtual-use-per-request-settings-buffer",
    r"0050-virtual-reconnect-stalled-submit-responses",
    r'"repo"\s*:\s*"https://github\.com/bitaxeorg/ESP-Miner"',
    r'"name"\s*:\s*"gamma"',
    r"RELEASE_" r"CHECKLIST\.md",
    r"docs/" r"ag" r"ent-guide\.md",
    r"docs/" r"ag" r"ent-native-readiness-" r"tick" r"ets\.md",
    r"docs/patch-" r"minimization-" r"back" r"log\.md",
    r"docs/release-" r"review-" r"brief\.md",
]


REMOVED_PUBLIC_SURFACES = {
    ".gitmodules",
    "configs/nvs/config-realpool-template.csv",
    "scripts/wait-for-share.sh",
    "tests/api/test_virtual_mining.py",
    "upstream/ESP-Miner",
}


def removed_private_process_docs() -> set[str]:
    return {
        "RELEASE_" + "CHECKLIST.md",
        "docs/" + "ag" + "ent-guide.md",
        "docs/" + "ag" + "ent-native-readiness-" + "tick" + "ets.md",
        "docs/patch-" + "minimization-" + "back" + "log.md",
        "docs/release-" + "review-" + "brief.md",
    }


def read_series() -> list[str]:
    return [
        line.strip()
        for line in (BITAXE_PATCH_DIR / "series.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def patch_subject(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"Subject: \[PATCH[^\]]*\] (.*)", line)
        if match:
            return match.group(1)
    raise AssertionError(f"{path.name} does not contain a Subject header")


def patch_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    header, _, _diff = text.partition("\n---\n")
    _prelude, _, body = header.partition("\n\n")
    return body.strip()


def test_patch_series_is_exact_active_stack():
    series = read_series()
    patch_files = sorted(path.name for path in BITAXE_PATCH_DIR.glob("*.patch"))

    assert series == list(EXPECTED_PATCH_SUBJECTS)
    assert patch_files == sorted(EXPECTED_PATCH_SUBJECTS)
    assert sorted(path.name for path in PATCH_DIR.glob("*.patch")) == []


def test_patch_subjects_match_public_patch_names():
    for filename, expected_subject in EXPECTED_PATCH_SUBJECTS.items():
        assert patch_subject(BITAXE_PATCH_DIR / filename) == expected_subject


def test_patch_bodies_explain_release_boundary_and_verification():
    required_labels = ["Why:", "Virtual-only:", "Upstream surface:", "Verify:", "Removal impact:"]

    for filename in EXPECTED_PATCH_SUBJECTS:
        body = patch_body(BITAXE_PATCH_DIR / filename)
        for label in required_labels:
            assert label in body, f"{filename} missing {label}"


def test_patch_stack_documents_one_keep_reason_per_patch():
    text = (ROOT_DIR / "docs" / "patch-stack.md").read_text(encoding="utf-8")

    for filename, expected_reason in EXPECTED_PATCH_KEEP_REASONS.items():
        assert f"| `{filename}` | {expected_reason} |" in text
    for filename, expected_reason in EXPECTED_NERDNOS_PATCH_KEEP_REASONS.items():
        assert f"| `{filename}` | {expected_reason} |" in text

    assert "guest-side mining correctness" not in text
    assert "AxeOS/API/NVS correctness" not in text
    assert "verifier pool interoperability" not in text
    assert "shipping operator UX" not in text


def test_nerdnos_responsiveness_patch_preempts_queued_pool_work():
    text = (PATCH_DIR / "nerdnos" / "0003-nerdnos-keep-virtual-mining-api-responsive.patch").read_text(
        encoding="utf-8"
    )

    assert "bool force_switch_work;" in text
    assert "queued_job_should_preempt" in text
    assert "(void) current;" in text
    assert "return uxQueueMessagesWaiting(s_jobQueue) > 0;" in text
    assert "bool difficulty_changed = false;" in text
    assert "next_job->force_switch_work = force_switch_work || difficulty_changed;" in text
    assert "return difficulty_changed;" in text
    assert "taskYIELD();" in text
    assert "(nonce & SEARCH_YIELD_INTERVAL) == 0" in text
    assert "static const UBaseType_t WORKER_TASK_PRIORITY = 1;" not in text
    assert "main/tasks/asic_result_task.cpp" in text
    assert "if (!asics->processWork(&asic_result))" in text
    assert "vTaskDelay(pdMS_TO_TICKS(1));" in text


def test_nerdnos_submit_patch_keeps_virtual_runtime_below_http_priority():
    text = (PATCH_DIR / "nerdnos" / "0002-nerdnos-add-virtual-asic-submit-path.patch").read_text(
        encoding="utf-8"
    )

    assert 'xTaskCreate(create_jobs_task, "stratum miner", 8192, NULL, 4, NULL);' in text
    assert 'xTaskCreate(ASIC_result_task, "asic result", 8192, NULL, 4, NULL);' in text
    assert 'xTaskCreate(StratumManager::taskWrapper, "stratum manager", 8192, (void *) STRATUM_MANAGER, 4, NULL);' in text
    assert "bool isCurrent(uint8_t asic_job_id, uint32_t generation_id)" in text
    assert "Stale virtual job invalidated before submit" in text
    assert "free_bm_job(job);" in text


def test_nerdnos_api_boot_patch_sends_normal_json_responses_once():
    text = (PATCH_DIR / "nerdnos" / "0001-nerdnos-add-virtual-gamma-api-boot-path.patch").read_text(
        encoding="utf-8"
    )

    assert "main/http_server/http_utils.cpp" in text
    assert "const size_t response_len = measureJson(doc);" in text
    assert "response_len < SCRATCH_BUFSIZE" in text
    assert "serializeJson(doc, scratch, response_len + 1)" in text
    assert "httpd_resp_send(req, scratch, written)" in text
    assert "HttpdChunkHeapWriter w(req, 2048);" in text
    assert "main/http_server/http_utils.h" not in text


def test_nerdnos_virtual_board_keeps_frequency_changes_virtual():
    text = (PATCH_DIR / "nerdnos" / "0001-nerdnos-add-virtual-gamma-api-boot-path.patch").read_text(
        encoding="utf-8"
    )

    assert "bool VirtualAxeGamma::setAsicFrequency(float frequency)" in text
    assert "bool setAsicFrequency(float frequency) override;" in text
    assert "m_asicFrequency = (int) frequency;" in text
    assert "return m_asics->setAsicFrequency(frequency);" not in text


def test_nerdnos_low_difficulty_patch_preserves_fractional_assigned_pool_difficulty():
    text = (PATCH_DIR / "nerdnos" / "0004-nerdnos-low-difficulty-pool-interoperability.patch").read_text(
        encoding="utf-8"
    )

    assert "setPoolDifficulty(pool, m_stratum_api_v1_message.new_difficulty)" in text
    assert "virtual void setPoolDifficulty(int pool, double diff)" in text
    assert "virtual double getPoolDifficulty()" in text
    assert "double m_poolDifficulty" in text
    assert "#define NVS_CONFIG_STRATUM_FALLBACK_DIFFICULTY \"fbstratumdiff\"" in text
    assert "bool parsed = end && *end == '\\0' && value > 0;" in text
    assert "free(difficulty);\n+            if (parsed) {" in text
    assert "double m_difficulty = 1.0;" in text
    assert "m_difficulty = Config::getStratumDifficulty();" in text
    assert "m_difficulty = Config::getStratumFallbackDifficulty();" in text
    assert "m_stratumAPI.suggestDifficulty(m_transport, m_config->getDifficulty())" in text
    assert "reserveOptionalSetupIds" in text
    assert "STRATUM_LAST_SETUP_ID + 1" in text
    assert "#ifdef VIRTUALAXE_GAMMA" in text
    assert "!m_config->isEnonceSubscribeEnabled()" in text
    assert "+        setPoolDifficulty(pool, (uint32_t) m_stratum_api_v1_message.new_difficulty)" not in text


def test_nerdnos_precompute_patch_keeps_guest_side_nonce_search_fast():
    text = (PATCH_DIR / "nerdnos" / "0005-nerdnos-precompute-virtual-nonce-search-material.patch").read_text(
        encoding="utf-8"
    )
    series = (PATCH_DIR / "nerdnos" / "series.txt").read_text(encoding="utf-8")
    patch_docs = (ROOT_DIR / "docs" / "patch-stack.md").read_text(encoding="utf-8")

    assert "0005-nerdnos-precompute-virtual-nonce-search-material.patch" in series
    assert (
        "| `0005-nerdnos-precompute-virtual-nonce-search-material.patch` | "
        "Keeps NerdNos live-share throughput inside the guest by precomputing invariant header material. |"
    ) in patch_docs
    assert "Precomputing invariant SHA-256 header material" in text
    assert "without moving work to the host" in text
    assert "components/bm1397/virtual_asic.cpp" in text
    assert "prepare_virtual_work_cache" in text
    assert "double_sha256_header_hash_exact" in text
    assert "virtual_hash_candidate_filter_accepts" in text
    assert "virtual_job_candidate_diff_for_nonce" in text
    assert "le256todouble" in text
    assert "+        double diff = test_nonce_value(&snapshot->job" not in text


def test_nerdnos_header_branding_patch_keeps_source_logo_with_virtual_label():
    text = (PATCH_DIR / "nerdnos" / "0006-nerdnos-brand-virtualaxe-header.patch").read_text(
        encoding="utf-8"
    )
    series = (PATCH_DIR / "nerdnos" / "series.txt").read_text(encoding="utf-8")
    patch_docs = (ROOT_DIR / "docs" / "patch-stack.md").read_text(encoding="utf-8")

    assert "0006-nerdnos-brand-virtualaxe-header.patch" in series
    assert (
        "| `0006-nerdnos-brand-virtualaxe-header.patch` | "
        "Fixes source-specific UI branding for the shipped virtual runtime screenshots. |"
    ) in patch_docs
    assert "normalizedDeviceModel === 'virtualAxe Gamma'" in text
    assert "this.deviceModel = 'NerdQAxe+'" in text
    assert "this.logoSubLabel = 'virtualaxe'" in text
    assert "logo-sub-label" in text
    assert "broken logo asset path" in text


def tracked_files() -> set[str]:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT_DIR, text=True, capture_output=True, check=True)
    return set(result.stdout.splitlines())


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert data[12:16] == b"IHDR"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def gif_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:6] in (b"GIF87a", b"GIF89a")
    return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")


def test_active_docs_do_not_reference_removed_release_surfaces():
    active_docs = [ROOT_DIR / "README.md", *sorted((ROOT_DIR / "docs").glob("*.md"))]
    failures: list[str] = []
    for path in active_docs:
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_ACTIVE_DOC_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                failures.append(f"{path.relative_to(ROOT_DIR)}: {pattern}")

    assert failures == []


def test_public_repo_does_not_track_local_process_docs_or_upstream_source():
    files = tracked_files()

    assert files.isdisjoint(REMOVED_PUBLIC_SURFACES)
    assert files.isdisjoint(removed_private_process_docs())
    assert "AGENTS.md" in files
    assert not any(path.startswith("upstream/") for path in files)
    assert not (ROOT_DIR / "reference").exists()


def test_public_source_docs_match_current_schema():
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    upstream_doc = (ROOT_DIR / "docs" / "upstream-integration.md").read_text(encoding="utf-8")
    command_contract = (ROOT_DIR / "docs" / "command-contract.md").read_text(encoding="utf-8")
    sources = json.loads((ROOT_DIR / "configs" / "sources.json").read_text(encoding="utf-8"))
    profile = json.loads((ROOT_DIR / "configs" / "profiles" / "gamma.json").read_text(encoding="utf-8"))
    default_source = sources["defaultSource"]
    bitaxe = sources["sources"]["bitaxe"]
    nerdnos = sources["sources"]["nerdnos"]

    assert default_source == "bitaxe"
    assert (
        "| `bitaxe` | [bitaxeorg/ESP-Miner](https://github.com/bitaxeorg/ESP-Miner) | "
        f"`{bitaxe['ref']}` | `{profile['id']}` |"
    ) in readme
    assert (
        "| `nerdnos` | "
        "[shufps/ESP-Miner-NerdQAxePlus](https://github.com/shufps/ESP-Miner-NerdQAxePlus) | "
        f"`v1.0.37` / `{nerdnos['resolvedCommit']}` | `{profile['id']}` |"
    ) in readme
    assert "SOURCE_NAME=vanilla" not in readme
    assert "SOURCE_NAME=vanilla" not in upstream_doc
    assert '"repo":' not in readme
    assert '"name": "gamma"' not in readme
    assert "make build SOURCE=bitaxe" in readme
    assert "make build SOURCE=nerdnos" in readme
    assert "`out/nerdnos/gamma/`" in command_contract


def test_env_example_only_contains_supported_public_defaults():
    text = (ROOT_DIR / ".env.example").read_text(encoding="utf-8")
    lines = text.splitlines()

    assert lines == [
        "SOURCE=bitaxe",
        "VIRTUAL_PROFILE=gamma",
        "HTTP_PORT=18080",
        "POOL_USER=<pool-user>",
    ]
    assert "SOURCE_NAME" not in text
    assert "NETWORK_MODE" not in text
    assert "TAP_" not in text
    assert "simulated" not in text


def test_default_pool_user_is_valid_bitcoin_address_template():
    defaults = (ROOT_DIR / "configs" / "sdkconfig.virtual.defaults").read_text(encoding="utf-8")
    nvs_template = (ROOT_DIR / "configs" / "nvs" / "config-virtual.csv").read_text(encoding="utf-8")
    build_script = (ROOT_DIR / "scripts" / "build-virtual.sh").read_text(encoding="utf-8")
    cli = (ROOT_DIR / "scripts" / "virtualaxe.py").read_text(encoding="utf-8")
    address = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"

    for text in (defaults, nvs_template, build_script, cli):
        assert address in text
        assert "virtualaxe.local" not in text


def test_readme_describes_public_test_address_without_identity_claims():
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    address = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"

    assert address in readme
    assert "mandatory public test address" in readme
    assert re.search(r"automated\s+smoke/release validation", readme)
    assert re.search(r"replace it in AxeOS or `\.env` with your own pool user or\s+wallet", readme)
    for forbidden in (
        "project wallet",
        "operator identity",
        "maint" + "ainer " + "wallet",
        "default payout identity",
        "private wallet",
        "secret",
        "configured release identity",
        "local placeholder",
    ):
        assert forbidden not in readme.lower()


def test_verifier_does_not_encode_source_specific_readiness_relaxations():
    verify_release = (ROOT_DIR / "scripts" / "verify-release.py").read_text(encoding="utf-8")
    wait_for_share_delta = (ROOT_DIR / "scripts" / "wait-for-share-delta.py").read_text(encoding="utf-8")

    forbidden_verify_release = [
        "STABLE_SUCCESS_COUNT",
        "configure_release_runtime_readiness",
        "api-health-established",
        "fallback-pool-difficulty",
    ]
    for token in forbidden_verify_release:
        assert token not in verify_release

    forbidden_wait_flags = [
        "api-health-established",
        "fallback-pool-difficulty",
    ]
    for token in forbidden_wait_flags:
        assert token not in wait_for_share_delta

    combined = f"{verify_release}\n{wait_for_share_delta}".lower()
    for token in ("tolerate intermittent", "relax", "lenient", "bypass"):
        assert token not in combined


def test_public_repo_excludes_local_process_documents():
    files = tracked_files()
    docs_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [ROOT_DIR / "README.md", *sorted((ROOT_DIR / "docs").glob("*.md"))]
    )

    for path in removed_private_process_docs():
        assert path not in files
        assert path not in docs_text


def test_repository_agent_policy_is_tracked_and_enforces_runtime_boundaries():
    files = tracked_files()
    policy = (ROOT_DIR / "AGENTS.md").read_text(encoding="utf-8")

    assert "AGENTS.md" in files
    for required in (
        "make validate SOURCE=bitaxe",
        "make validate SOURCE=nerdnos",
        "make local-state-report",
        "make drift-check",
        "make verify-release",
        ".state/todo_local.md",
        "ASIC_send_work()",
        "ASIC_process_work()",
        "test_nonce_value()",
        "0044`/`0045",
    ):
        assert required in policy
    assert "Do not add host-side or relay-assisted proof-of-work." in policy
    assert "Simulation Actions must not affect hashrate" in policy
    assert "Do not run `make verify-release`" in policy
    assert "Do not commit generated state" in policy


def test_python_dependencies_are_locked():
    files = tracked_files()
    pyproject = (ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8")

    assert "pyproject.toml" in files
    assert "uv.lock" in files
    assert "pytest==9.0.3" in pyproject
    assert "requests==2.33.1" in pyproject


def test_patch_drift_commands_are_public_and_documented():
    makefile = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")
    command_contract = (ROOT_DIR / "docs" / "command-contract.md").read_text(encoding="utf-8")
    upstream_doc = (ROOT_DIR / "docs" / "upstream-integration.md").read_text(encoding="utf-8")
    cli = (ROOT_DIR / "scripts" / "virtualaxe.py").read_text(encoding="utf-8")

    assert "patch-check:" in makefile
    assert "patch-check-upstream:" in makefile
    assert "PATCH_TARGET_DIR ?= /tmp/virtualaxe-patchcheck-$(SOURCE)" in makefile
    assert "/tmp/virtualaxe-patchcheck-upstream-$(SOURCE)" in makefile
    assert 'add_parser("patch-check"' in cli
    assert "make patch-check" in command_contract
    assert "make patch-check-upstream" in upstream_doc


def test_patch_apply_does_not_copy_frontend_dependency_cache():
    script = (ROOT_DIR / "scripts" / "apply-patches.sh").read_text(encoding="utf-8")

    assert "SOURCE_AXEOS_NODE_MODULES" not in script
    assert "/node_modules" not in script


def test_release_readiness_commands_are_public_and_documented():
    makefile = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    command_contract = (ROOT_DIR / "docs" / "command-contract.md").read_text(encoding="utf-8")

    targets = [
        "validate",
        "validate-lite",
        "validate-config",
        "secret-scan",
        "local-state-report",
        "clean-clone-smoke",
        "drift-check",
        "patch-audit",
        "audit",
        "release-evidence",
    ]
    for target in targets:
        assert f"{target}:" in makefile
        assert f"make {target}" in command_contract
    assert "make validate" in readme
    assert "make clean-clone-smoke" in command_contract
    assert "make drift-check" in command_contract
    assert "bootstrap:" not in makefile
    assert "make bootstrap" not in command_contract
    assert "| `./vaxe` | tracked-source read-only | Prints usage and concrete start examples." in command_contract
    assert not (ROOT_DIR / "scripts" / "bootstrap.sh").exists()


def test_clean_clone_smoke_runs_actual_first_user_path():
    makefile = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")
    script = (ROOT_DIR / "scripts" / "clean-clone-smoke.sh").read_text(encoding="utf-8")
    command_contract = (ROOT_DIR / "docs" / "command-contract.md").read_text(encoding="utf-8")

    assert "clean-clone-smoke:" in makefile
    assert "./scripts/clean-clone-smoke.sh" in makefile
    assert "git clone --quiet" in script
    assert "git -C \"${ROOT_DIR}\" diff --quiet" in script
    assert "run_step ./vaxe" in script
    assert "run_step make help" in script
    assert "run_step make drift-check" in script
    assert "run_step make validate-lite" in script
    assert "run_step make build SOURCE=bitaxe" in script
    assert "run_step make build SOURCE=nerdnos" in script
    assert "preserved clone" in script
    assert "removed on success and preserved on failure" in command_contract


def test_readme_is_human_focused_github_landing_page():
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    files = tracked_files()

    assert '<img src="img/banner.png"' in readme
    assert 'alt="virtualAxe - Virtual Bitaxe powered by QEMU, ESP-Miner &amp; AxeOS"' in readme
    assert 'width="100%"' in readme
    assert "img/banner.png" in files
    assert "img/logo.png" in files
    assert png_dimensions(ROOT_DIR / "img" / "banner.png") == (2172, 724)
    assert png_dimensions(ROOT_DIR / "img" / "logo.png") == (1254, 1254)
    assert png_dimensions(ROOT_DIR / "img" / "virtualaxe-nerdnos-axeos.png") == (1280, 640)
    assert png_dimensions(ROOT_DIR / "img" / "virtualaxe-nerdnos-pool.png") == (1280, 640)
    assert "img/virtualaxe-build-to-axeos.gif" in files
    assert "img/virtualaxe-build-to-axeos.gif" in readme
    assert gif_dimensions(ROOT_DIR / "img" / "virtualaxe-build-to-axeos.gif") == (1600, 900)
    assert "img/virtualaxe-banner.svg" not in readme
    assert "# virtualAxe" in readme
    assert "Virtual Bitaxe firmware testing for ESP-Miner, AxeOS, and ESP32-S3 QEMU." in readme
    assert 'href="LICENSE">MIT License</a>' in readme
    for use_case in ("CI/CD", "security-test", "AxeOS UI", "settings", "persistence", "Stratum"):
        assert use_case in readme
    for section in (
        "## Quick Start",
        "## What It Does",
        "## Common Commands",
        "## Supported Sources",
        "## Evidence Model",
        "## Simulation Actions",
        "## Limits",
        "## Docs",
    ):
        assert section in readme

    forbidden_public_headings = (
        "## Release Scope",
        "## What It Proves",
        "## What It Does Not Prove",
        "## What It Is Not",
        "## Development Workflow",
        "## Release Notes",
        "### Deterministic Local Gates",
        "### Automated External/Live Gate",
        "### Human Release Actions",
    )
    for heading in forbidden_public_headings:
        assert heading not in readme
    assert "hidden startup control" not in readme
    assert "VIRTUAL_BITAXE_SIM_ACTIONS" not in readme
    quick_start = readme.split("## Quick Start", 1)[1].split("## What It Does", 1)[0]
    assert "git clone https://github.com/mars-llm/virtualAxe.git" in quick_start
    assert "cd virtualAxe" in quick_start
    assert "./vaxe --source bitaxe" in quick_start
    assert "make build SOURCE=bitaxe" in quick_start
    assert "Successful builds print the image path, manifest path" in quick_start
    assert "real Bitaxe AxeOS UI" in quick_start
    assert "make verify-submit-replay SOURCE=bitaxe" in quick_start
    assert "make bootstrap" not in quick_start
    assert "./scripts/install-vaxe.sh" not in quick_start
    assert re.search(r"first run fetches the\s+pinned upstream source", quick_start, re.IGNORECASE)
    assert "For a coding agent" not in quick_start
    assert "AGENTS.md" in readme
    assert "make build SOURCE=bitaxe" in readme
    assert "make build SOURCE=nerdnos" in readme
    assert "out/qemu_flash.bin" in readme
    assert "out/nerdnos/gamma/qemu_flash.bin" in readme
    assert "reuse the matching\nimage when the manifest still matches" in readme
    assert "| `./vaxe` | Print usage and examples without starting QEMU. |" in readme
    assert "Build or reuse the default Bitaxe image, start QEMU" not in readme
    assert "./vaxe --source bitaxe --sim-actions" in readme
    assert "./vaxe --source nerdnos --sim-actions" in readme
    assert "./scripts/install-vaxe.sh" not in readme
    assert "docs/command-contract.md" in readme


def test_readme_documents_firmware_source_selection_without_overclaiming():
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    command_contract = (ROOT_DIR / "docs" / "command-contract.md").read_text(encoding="utf-8")
    upstream = (ROOT_DIR / "docs" / "upstream-integration.md").read_text(encoding="utf-8")
    patch_stack = (ROOT_DIR / "docs" / "patch-stack.md").read_text(encoding="utf-8")

    assert "`bitaxe`" in readme
    assert "`nerdnos`" in readme
    assert "vaxe --source bitaxe" in readme
    assert "vaxe --source nerdnos" in readme
    assert "make patch-check SOURCE=nerdnos" in command_contract
    assert "`gamma` remains the only" in readme
    assert re.search(r"hardware-equivalent\s+validation", readme)
    assert "`live_verified`" in readme
    assert "screenshots,\nand generic QEMU log activity are diagnostics only" in readme
    assert "does not claim full NerdNos feature parity" in readme
    assert "deterministic submit replay" in readme
    assert "five pool-side accepted shares per pool" in readme
    assert "direct remote\npool Stratum accepted response" in readme
    assert "Firmware/API counters, best-difficulty charts, worker-active status, screenshots,\nand generic QEMU log activity are diagnostics only" in readme
    assert re.search(r"QEMU logs are accepted only\s+as the transport for validated live pool Stratum responses", readme)
    assert "live mining\nis not verified for that source" not in upstream
    assert re.search(r"live pool\s+qualification evidence", upstream)
    assert "status evidence remain diagnostic because they are not accepted-share counters" in upstream
    assert "requires PublicPool, Bitronics, and Nerdminers to\npass in the same run" in patch_stack
    assert "five validated remote-pool Stratum accepted responses\nper pool" in patch_stack


def test_tracked_text_excludes_private_history_and_agent_identity_leaks():
    forbidden = [
        "/" + "Users/" + "mars",
        "/private/" + "tmp",
        "mars" + "mensch",
        "users." + "nore" + "ply." + "github.com",
        "co" + "dex",
        "Co" + "dex",
        "cl" + "aude",
        "Cl" + "aude",
    ]
    failures = []
    for relative in tracked_files():
        path = ROOT_DIR / relative
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in forbidden:
            if token in text:
                failures.append(f"{relative}: {token}")

    assert failures == []


def test_public_docs_distinguish_local_live_and_release_gates():
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    command_contract = (ROOT_DIR / "docs" / "command-contract.md").read_text(encoding="utf-8")

    assert "docs/command-contract.md" in readme
    assert "automated live PublicPool, Bitronics, and Nerdminers smoke gate" in command_contract
    assert "Each pool phase exits when its accepted-share proof requirement is met" in command_contract
    assert "requires five pool-side accepted shares and zero rejected-share delta violation" in command_contract
    assert "Pool-side proof means direct remote-pool Stratum accepted responses" in command_contract
    assert "direct remote-pool Stratum accepted responses" in command_contract
    assert "manual pool" not in readme.lower()


def test_readme_preserves_release_proof_semantics():
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT_DIR / "docs" / "architecture.md").read_text(encoding="utf-8")
    command_contract = (ROOT_DIR / "docs" / "command-contract.md").read_text(encoding="utf-8")
    upstream = (ROOT_DIR / "docs" / "upstream-integration.md").read_text(encoding="utf-8")

    assert "Release evidence distinguishes firmware/API accepted-share evidence" not in readme
    assert "Firmware/API counters, best-difficulty charts, worker-active status, screenshots,\nand generic QEMU log activity are diagnostics only" in readme
    assert "Pool-side proof can come from a direct remote\npool Stratum accepted response" in readme
    assert "Firmware/API counters, best-difficulty/chart data, worker-active status, and generic QEMU logs are diagnostic only" in command_contract
    assert "PublicPool best-difficulty/chart evidence and Bitronics\nstatus evidence remain diagnostic" in upstream
    assert "five pool-side accepted shares" in architecture
    assert "do\nnot satisfy qualification thresholds" in architecture


def test_command_safety_docs_use_precise_side_effect_classes():
    makefile = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")
    command_contract = (ROOT_DIR / "docs" / "command-contract.md").read_text(encoding="utf-8")

    for safety_class in (
        "tracked-source read-only",
        "report-generating",
        "ignored-state mutating",
        "runtime mutating",
        "destructive",
        "external/live",
    ):
        assert safety_class in command_contract

    assert "make validate       [ignored-state mutating]" in makefile
    assert "| `make validate` | ignored-state mutating |" in command_contract
    assert "| `make build SOURCE=bitaxe` | ignored-state mutating, external/live when first-run dependencies are missing | Builds the default Bitaxe reusable QEMU image under `out/`" in command_contract
    assert "| `make build SOURCE=nerdnos` | ignored-state mutating, external/live when first-run dependencies are missing | Builds the NerdNos reusable QEMU image under `out/nerdnos/gamma/`" in command_contract
    assert "| `make validate-config` | tracked-source read-only |" in command_contract
    assert "| `make audit` | report-generating |" in command_contract
    assert "| `make verify-release` | external/live |" in command_contract
    assert "| `make validate` | read-only |" not in command_contract


def test_build_auto_provisions_first_run_frontend_and_container_state():
    script = (ROOT_DIR / "scripts" / "build-virtual.sh").read_text(encoding="utf-8")
    run_script = (ROOT_DIR / "scripts" / "run-qemu-nat.sh").read_text(encoding="utf-8")
    test_ci_script = (ROOT_DIR / "scripts" / "verify-test-ci.sh").read_text(encoding="utf-8")
    runtime_helper = (ROOT_DIR / "scripts" / "container-runtime.sh").read_text(encoding="utf-8")
    dashboard = (ROOT_DIR / "scripts" / "virtualaxe_dashboard.py").read_text(encoding="utf-8")

    assert 'source "${ROOT_DIR}/scripts/container-runtime.sh"' in script
    assert 'source "${ROOT_DIR}/scripts/container-runtime.sh"' in run_script
    assert 'source "${ROOT_DIR}/scripts/container-runtime.sh"' in test_ci_script
    assert "virtualaxe_select_execution_environment" in runtime_helper
    assert "podman machine start" in runtime_helper
    assert "will not create or recreate" in runtime_helper
    assert "ensure_container_image" in script
    assert "install_axeos_frontend_dependencies" in script
    assert '"${CONTAINER_RUNTIME}" image inspect "${IMAGE_NAME}"' in script
    assert "npm ci --prefix" in script
    assert "npm install --prefix" in script
    assert "Run SOURCE=${SOURCE_NAME} make bootstrap first" not in script
    assert "make bootstrap first" not in dashboard


def test_build_commands_stream_human_progress_without_required_bootstrap():
    build_script = (ROOT_DIR / "scripts" / "build-virtual.sh").read_text(encoding="utf-8")
    cli = (ROOT_DIR / "scripts" / "virtualaxe.py").read_text(encoding="utf-8")
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    command_contract = (ROOT_DIR / "docs" / "command-contract.md").read_text(encoding="utf-8")

    assert "progress_phase" in build_script
    assert 'progress_phase "Preparing ${SOURCE_NAME}/${VIRTUAL_PROFILE} build output."' in build_script
    assert "build output in ${OUT_DIR}" not in build_script
    assert "worktree at ${UPSTREAM_DIR}" not in build_script
    assert "Checking AxeOS frontend dependencies" in build_script
    assert "Building ESP-IDF firmware and AxeOS assets. This is the longest step." in build_script
    assert "Build complete. QEMU image is ready." in build_script
    assert "run_build_with_progress" in cli
    assert "BUILD_REFERENCE_SECONDS" in cli
    assert '"bitaxe": 7 * 60 + 52' in cli
    assert '"nerdnos": 4 * 60 + 4' in cli
    assert "BuildCockpitState" in cli
    assert "should_render_build_cockpit" in cli
    assert "virtualAxe build cockpit" in cli
    assert "flight recorder:" in cli
    assert "The QEMU flash image is reusable." in cli
    assert "last_activity_at" in cli
    assert "current_stage_started_at" in cli
    assert "log_lines" in cli
    assert "log_bytes" in cli
    assert "set_build_cockpit_cursor_visible" in cli
    assert "active    " in cli
    assert "updated {activity_age} ago" in cli
    assert "QEMU firmware image ready" in cli
    assert "boot it:" in cli
    assert "make verify-submit-replay SOURCE=" in cli
    assert "rebuild:" in cli
    assert "updated" in cli
    assert "The first build can take several minutes" in readme
    assert "terminal build cockpit" in readme
    assert "local reference timings" in readme
    assert "`bitaxe`: `7m52s`, `nerdnos`: `4m04s`" in readme
    assert "full log path" in readme
    assert "terminal build cockpit with reference clean-build timing" in command_contract
    assert "Container runtime selection is health-checked" in command_contract
    assert "Podman is used only after `podman info` succeeds" in command_contract
    assert "Non-interactive runs keep stable line-oriented progress" in command_contract
    assert "reusable-image paths" in command_contract
    assert "Successful builds print the image path, manifest path, boot command, deterministic replay command, and rebuild command" in command_contract
    assert "virtualAxe can start it, but will not create or\nrecreate it" in readme
    assert "make bootstrap" not in readme.split("## Quick Start", 1)[1].split("## What It Does", 1)[0]


def test_validate_target_uses_locked_python_environment():
    makefile = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "validate:" in makefile
    assert "./scripts/ensure-test-python.sh" in makefile
    assert "VIRTUALAXE_TEST_PYTHON:-./.venv/bin/python" in makefile


def test_readme_documents_platform_support_matrix():
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    quick_start = readme.split("## Quick Start", 1)[1].split("## What It Does", 1)[0]

    assert "Linux or macOS" in quick_start
    assert "WSL2 may work, but is best-effort and not release-qualified" in quick_start
    assert "On macOS, Docker Desktop must be running" in quick_start
    assert "WSL2 with Docker or Podman integration is best-effort only" in quick_start
    assert "Native\nWindows shells are not part of the tested release matrix" in readme
    assert "native windows support is claimed" not in readme.lower()


def test_known_limitations_document_toolchain_reproducibility_boundary():
    text = (ROOT_DIR / "docs" / "known-limitations.md").read_text(encoding="utf-8")

    assert "## Toolchain Reproducibility" in text
    assert "uv.lock" in text
    assert "package-lock.json" in text
    assert "configs/sources.json" in text
    assert "package feeds" in text
    assert "can still drift" in text


def test_public_docs_exclude_local_process_ledgers():
    files = tracked_files()
    public_docs = {
        str(path.relative_to(ROOT_DIR)): path.read_text(encoding="utf-8")
        for path in [ROOT_DIR / "README.md", *sorted((ROOT_DIR / "docs").glob("*.md"))]
    }
    forbidden_tokens = [
        "AN" + "R-",
        "PM" + "-",
        "not proven hunk-minimized",
    ]

    assert files.isdisjoint(removed_private_process_docs())
    for removed_path in removed_private_process_docs():
        for text in public_docs.values():
            assert removed_path not in text
    for token in forbidden_tokens:
        for path, text in public_docs.items():
            assert token not in text, path


def test_legacy_verifier_identity_surface_is_removed():
    forbidden = [
        "RELEASE_" + "WALLET",
        "DEFAULT_" + "RELEASE",
        "--" + "wallet",
    ]
    failures = []
    for relative in tracked_files():
        path = ROOT_DIR / relative
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in forbidden:
            if token in text:
                failures.append(f"{relative}: {token}")

    assert failures == []


def test_build_manifest_records_release_provenance_fields():
    text = (ROOT_DIR / "scripts" / "build-virtual.sh").read_text(encoding="utf-8")
    required_fields = [
        "sourceRepoUrl",
        "canonicalSourceName",
        "configuredResolvedCommit",
        "sourceDisplayName",
        "sourceReleaseTag",
        "sourceSupportState",
        "configuredUpstreamRef",
        "resolvedUpstreamCommit",
        "patchSeriesPath",
        "patchSeriesSha256",
        "patches",
        "sourceBuildVars",
        "profileFileSha256",
        "sdkconfigOverrideSha256",
        "activeConfigCsvSha256",
        "nvsSeedMode",
        "poolHost",
        "poolPort",
        "poolUser",
        "poolDifficulty",
        "poolSubscribeAgent",
        "fallbackPoolHost",
        "fallbackPoolPort",
        "fallbackPoolUser",
        "fallbackPoolDifficulty",
        "fallbackPoolSubscribeAgent",
        "toolVersions",
        "containerImage",
        "buildTimestampUtc",
        "artifacts",
    ]

    for field in required_fields:
        assert f'"{field}"' in text


def test_minimal_ci_runs_deterministic_release_checks():
    workflow = (ROOT_DIR / ".github" / "workflows" / "ci.yml")
    text = workflow.read_text(encoding="utf-8")

    assert "uv sync --frozen --no-install-project" in text
    assert "uv run make validate-lite" in text
    assert "verify-release" not in text
