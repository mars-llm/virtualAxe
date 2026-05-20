import sys
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import source_registry


def test_registry_resolves_public_sources_and_compatibility_alias():
    registry = source_registry.load_source_registry()

    assert registry.default_source == "bitaxe"
    assert registry.canonical_name("bitaxe") == "bitaxe"
    assert registry.canonical_name("nerdnos") == "nerdnos"
    assert registry.canonical_name("vanilla") == "bitaxe"
    assert registry.get("vanilla").name == "bitaxe"


def test_registry_rejects_unknown_source():
    registry = source_registry.load_source_registry()

    with pytest.raises(source_registry.SourceRegistryError, match="Unknown source"):
        registry.get("missing")


def test_registry_exposes_legacy_payload_for_existing_callers():
    payload = source_registry.load_source_registry().as_legacy_payload(include_aliases=True)

    assert payload["defaultSource"] == "bitaxe"
    assert payload["sources"]["vanilla"]["canonicalSource"] == "bitaxe"
    assert payload["sources"]["vanilla"]["repoUrl"] == payload["sources"]["bitaxe"]["repoUrl"]


def test_registry_records_nerdnos_release_and_build_metadata():
    nerdnos = source_registry.load_source_registry().get("nerdnos")

    assert nerdnos.release_tag == "v1.0.37"
    assert nerdnos.ref == "c18abafebde66c39f4bd8ae6d839088b84b4e79c"
    assert nerdnos.resolved_commit == "c18abafebde66c39f4bd8ae6d839088b84b4e79c"
    assert nerdnos.profile == "gamma"
    assert nerdnos.build_vars == {"BOARD": "VIRTUALAXE_GAMMA"}
    assert nerdnos.init_submodules is True
    assert nerdnos.qemu_memory_mb == 8
    assert nerdnos.patch_series == "patches/esp-miner/nerdnos/series.txt"
    assert nerdnos.support_state == "live_verified"
    assert nerdnos.supports("metadata_only") is True
    assert nerdnos.supports("patch_applies") is True
    assert nerdnos.supports("build_verified") is True
    assert nerdnos.supports("api_boot_verified") is True
    assert nerdnos.supports("submit_replay_verified") is True
    assert nerdnos.supports("live_verified") is True


def test_registry_records_bitaxe_as_live_verified_default_source():
    bitaxe = source_registry.load_source_registry().get("bitaxe")

    assert bitaxe.support_state == "live_verified"
    assert bitaxe.supports("api_boot_verified") is True
    assert bitaxe.supports("submit_replay_verified") is True
    assert bitaxe.supports("live_verified") is True


def test_registry_records_source_specific_paths():
    registry = source_registry.load_source_registry()

    assert registry.get("bitaxe").patch_series_path == ROOT_DIR / "patches" / "esp-miner" / "bitaxe" / "series.txt"
    assert registry.get("nerdnos").patch_series_path == ROOT_DIR / "patches" / "esp-miner" / "nerdnos" / "series.txt"
    assert registry.get("bitaxe").nvs_template_path == ROOT_DIR / "configs" / "nvs" / "config-virtual.csv"
    assert registry.get("nerdnos").patch_series_path.is_file()
