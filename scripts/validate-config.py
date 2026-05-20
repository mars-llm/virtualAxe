#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import source_registry

REQUIRED_PROFILE_KEYS = {
    "id",
    "displayName",
    "boardVersion",
    "deviceModel",
    "asicModel",
    "asicCount",
    "nominalVoltage",
    "voltageDomains",
    "maxPower",
    "powerOffset",
    "swarmColor",
    "powerConsumptionTarget",
    "temperatureTarget",
    "sensors",
}
REQUIRED_NVS_KEYS = {
    "hostname",
    "stratumurl",
    "stratumport",
    "stratumuser",
    "stratumpass",
    "stratumdiff",
    "fbstratumurl",
    "fbstratumport",
    "fbstratumuser",
    "fbstratumpass",
    "fbstratumdiff",
    "boardversion",
    "devicemodel",
    "asicmodel",
}
REQUIRED_MANIFEST_FIELDS = {
    "canonicalSourceName",
    "configuredResolvedCommit",
    "sourceName",
    "sourceDisplayName",
    "sourceRepoUrl",
    "sourceReleaseTag",
    "sourceSupportState",
    "configuredUpstreamRef",
    "resolvedUpstreamCommit",
    "patchSeriesPath",
    "patchSeriesSha256",
    "patches",
    "sourceBuildVars",
    "virtualProfile",
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
    "buildTimestampUtc",
    "artifacts",
}


class ValidationError(ValueError):
    pass


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{display_path(path)} is not valid JSON: {exc}") from exc


def validate_sources(path: Path) -> list[str]:
    payload = load_json(path)
    return source_registry.validate_sources_payload(payload, root=ROOT_DIR)


def validate_profile(path: Path) -> list[str]:
    errors: list[str] = []
    payload = load_json(path)
    if not isinstance(payload, dict):
        return [f"{display_path(path)} must contain an object"]
    missing = sorted(REQUIRED_PROFILE_KEYS - set(payload))
    if missing:
        errors.append(f"{display_path(path)} is missing required keys: {', '.join(missing)}")
    if payload.get("id") != "gamma":
        errors.append("gamma profile id must be gamma")
    if payload.get("boardVersion") != "virtual-gamma":
        errors.append("gamma profile boardVersion must be virtual-gamma")
    if payload.get("deviceModel") != "Gamma":
        errors.append("gamma profile deviceModel must be Gamma")
    if payload.get("asicModel") != "BM1370":
        errors.append("gamma profile asicModel must be BM1370")
    for key in ("asicCount", "nominalVoltage", "voltageDomains", "maxPower"):
        if not isinstance(payload.get(key), (int, float)) or payload[key] <= 0:
            errors.append(f"gamma profile {key} must be a positive number")
    sensors = payload.get("sensors")
    if not isinstance(sensors, dict):
        errors.append("gamma profile sensors must be an object")
    elif "EMC2101" not in sensors or "TPS546" not in sensors:
        errors.append("gamma profile sensors must declare EMC2101 and TPS546")
    return errors


def validate_nvs_csv(path: Path) -> list[str]:
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return ["configs/nvs/config-virtual.csv must contain NVS rows"]
    if rows[0].keys() != {"key", "type", "encoding", "value"}:
        errors.append("configs/nvs/config-virtual.csv header must be key,type,encoding,value")
    by_key = {row.get("key", ""): row for row in rows}
    missing = sorted(REQUIRED_NVS_KEYS - set(by_key))
    if missing:
        errors.append(f"configs/nvs/config-virtual.csv missing keys: {', '.join(missing)}")
    if by_key.get("main", {}).get("type") != "namespace":
        errors.append("NVS CSV must declare main namespace")
    for key in ("stratumport", "fbstratumport"):
        value = by_key.get(key, {}).get("value", "")
        try:
            port = int(value)
        except ValueError:
            errors.append(f"NVS {key} must be an integer port")
            continue
        if port < 1 or port > 65535:
            errors.append(f"NVS {key} must be between 1 and 65535")
    for key in ("stratumdiff", "fbstratumdiff"):
        try:
            difficulty = float(by_key.get(key, {}).get("value", ""))
        except ValueError:
            errors.append(f"NVS {key} must be numeric")
            continue
        if difficulty <= 0:
            errors.append(f"NVS {key} must be positive")
    return errors


def validate_manifest(path: Path) -> list[str]:
    if not path.exists():
        return []
    errors: list[str] = []
    payload = load_json(path)
    if not isinstance(payload, dict):
        return [f"{display_path(path)} must contain an object"]
    missing = sorted(REQUIRED_MANIFEST_FIELDS - set(payload))
    if missing:
        errors.append(f"{display_path(path)} missing fields: {', '.join(missing)}")
    source_name = payload.get("sourceName")
    try:
        registry = source_registry.load_source_registry(ROOT_DIR / "configs" / "sources.json")
        canonical_source = registry.canonical_name(str(source_name))
    except source_registry.SourceRegistryError as exc:
        errors.append(f"out/manifest.json sourceName must resolve to a configured source: {exc}")
        canonical_source = ""
    if payload.get("canonicalSourceName") and payload.get("canonicalSourceName") != canonical_source:
        errors.append("out/manifest.json canonicalSourceName must match sourceName")
    if payload.get("virtualProfile") != "gamma":
        errors.append("out/manifest.json virtualProfile must be gamma when present")
    patches = payload.get("patches", [])
    if patches and not all(isinstance(patch, dict) and patch.get("file") and patch.get("sha256") for patch in patches):
        errors.append("out/manifest.json patches must include file and sha256")
    return errors


def validate_repo(root: Path = ROOT_DIR, manifest_path: Path | None = None) -> dict[str, Any]:
    def collect(check, path: Path) -> list[str]:
        try:
            return check(path)
        except ValidationError as exc:
            return [str(exc)]

    checks = {
        "sources": collect(validate_sources, root / "configs" / "sources.json"),
        "profile": collect(validate_profile, root / "configs" / "profiles" / "gamma.json"),
        "nvs": collect(validate_nvs_csv, root / "configs" / "nvs" / "config-virtual.csv"),
    }
    if manifest_path is not None:
        checks["manifest"] = collect(validate_manifest, manifest_path)
    errors = [f"{name}: {error}" for name, group in checks.items() for error in group]
    return {
        "status": "passed" if not errors else "failed",
        "checks": checks,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate virtualAxe configuration contracts.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Validate a generated manifest path in addition to tracked configuration.",
    )
    args = parser.parse_args()
    payload = validate_repo(manifest_path=args.manifest)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["errors"]:
        for error in payload["errors"]:
            print(f"config validation error: {error}", file=sys.stderr)
    else:
        print("config validation passed")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
