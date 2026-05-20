import csv
import importlib.util
import json
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def load_validate_config():
    path = ROOT_DIR / "scripts" / "validate-config.py"
    spec = importlib.util.spec_from_file_location("validate_config", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_current_tracked_config_validates():
    module = load_validate_config()

    assert module.validate_repo(ROOT_DIR)["status"] == "passed"


def test_sources_require_pinned_commit_ref(tmp_path):
    module = load_validate_config()
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                "defaultSource": "bitaxe",
                "aliases": {"vanilla": "bitaxe"},
                "sources": {
                    "bitaxe": {
                        "displayName": "Bitaxe ESP-Miner",
                        "repoUrl": "https://github.com/bitaxeorg/ESP-Miner",
                        "ref": "origin/master",
                        "resolvedCommit": "ce44b2bbfef60ef8830ab17b321cc295e0c0edc8",
                        "patchSeries": "patches/esp-miner/bitaxe/series.txt",
                        "profile": "gamma",
                        "supportState": "submit_replay_verified",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert any("ref must be a pinned 40-character commit SHA" in error for error in module.validate_sources(path))


def test_sources_require_ref_to_match_resolved_commit(tmp_path):
    module = load_validate_config()
    path = tmp_path / "sources.json"
    payload = json.loads((ROOT_DIR / "configs" / "sources.json").read_text(encoding="utf-8"))
    payload["sources"]["bitaxe"]["resolvedCommit"] = "c18abafebde66c39f4bd8ae6d839088b84b4e79c"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert any("source bitaxe ref must match resolvedCommit" in error for error in module.validate_sources(path))


def test_sources_expose_bitaxe_default_and_vanilla_alias():
    module = load_validate_config()
    payload = json.loads((ROOT_DIR / "configs" / "sources.json").read_text(encoding="utf-8"))

    assert payload["defaultSource"] == "bitaxe"
    assert payload["aliases"]["vanilla"] == "bitaxe"
    assert payload["sources"]["bitaxe"]["supportState"] == "live_verified"
    assert module.validate_sources(ROOT_DIR / "configs" / "sources.json") == []


def test_sources_pin_nerdnos_release_target():
    payload = json.loads((ROOT_DIR / "configs" / "sources.json").read_text(encoding="utf-8"))
    nerdnos = payload["sources"]["nerdnos"]

    assert nerdnos["releaseTag"] == "v1.0.37"
    assert nerdnos["resolvedCommit"] == "c18abafebde66c39f4bd8ae6d839088b84b4e79c"
    assert nerdnos["ref"] == nerdnos["resolvedCommit"]
    assert nerdnos["buildVars"]["BOARD"] == "VIRTUALAXE_GAMMA"
    assert nerdnos["qemuMemoryMb"] == 8
    assert nerdnos["supportState"] == "live_verified"


def test_sources_reject_invalid_alias_target(tmp_path):
    module = load_validate_config()
    path = tmp_path / "sources.json"
    payload = json.loads((ROOT_DIR / "configs" / "sources.json").read_text(encoding="utf-8"))
    payload["aliases"]["legacy"] = "missing"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert any("source alias legacy must target a canonical source" in error for error in module.validate_sources(path))


def test_profile_requires_gamma_identity(tmp_path):
    module = load_validate_config()
    payload = json.loads((ROOT_DIR / "configs" / "profiles" / "gamma.json").read_text(encoding="utf-8"))
    payload["id"] = "beta"
    path = tmp_path / "gamma.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert "gamma profile id must be gamma" in module.validate_profile(path)


def test_nvs_rejects_invalid_pool_port(tmp_path):
    module = load_validate_config()
    source = ROOT_DIR / "configs" / "nvs" / "config-virtual.csv"
    rows = list(csv.DictReader(source.read_text(encoding="utf-8").splitlines()))
    for row in rows:
        if row["key"] == "stratumport":
            row["value"] = "70000"
    path = tmp_path / "config-virtual.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["key", "type", "encoding", "value"])
        writer.writeheader()
        writer.writerows(rows)

    assert "NVS stratumport must be between 1 and 65535" in module.validate_nvs_csv(path)


def test_manifest_validator_rejects_missing_provenance(tmp_path):
    module = load_validate_config()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"sourceName": "bitaxe", "virtualProfile": "gamma"}), encoding="utf-8")

    assert any("missing fields" in error for error in module.validate_manifest(path))
