#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT_DIR / "configs" / "sources.json"
SOURCE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
SOURCE_REF_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_SOURCE_KEYS = {
    "displayName",
    "repoUrl",
    "ref",
    "resolvedCommit",
    "patchSeries",
    "profile",
    "supportState",
}
SUPPORT_STATES = (
    "metadata_only",
    "patch_applies",
    "build_verified",
    "api_boot_verified",
    "submit_replay_verified",
    "live_verified",
)
SUPPORT_STATE_RANK = {state: index for index, state in enumerate(SUPPORT_STATES)}


class SourceRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class FirmwareSource:
    name: str
    display_name: str
    repo_url: str
    ref: str
    resolved_commit: str
    patch_series: str
    profile: str
    release_tag: str = ""
    nvs_template: str = ""
    build_vars: dict[str, str] = field(default_factory=dict)
    init_submodules: bool = False
    qemu_memory_mb: int = 32
    support_state: str = "metadata_only"
    support_status: str = ""

    @property
    def patch_series_path(self) -> Path:
        return ROOT_DIR / self.patch_series

    @property
    def nvs_template_path(self) -> Path | None:
        return (ROOT_DIR / self.nvs_template) if self.nvs_template else None

    def as_legacy_entry(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "displayName": self.display_name,
            "repoUrl": self.repo_url,
            "ref": self.ref,
            "resolvedCommit": self.resolved_commit,
            "patchSeries": self.patch_series,
            "profile": self.profile,
        }
        if self.release_tag:
            payload["releaseTag"] = self.release_tag
        if self.nvs_template:
            payload["nvsTemplate"] = self.nvs_template
        if self.build_vars:
            payload["buildVars"] = dict(self.build_vars)
        if self.init_submodules:
            payload["initSubmodules"] = self.init_submodules
        if self.qemu_memory_mb != 32:
            payload["qemuMemoryMb"] = self.qemu_memory_mb
        payload["supportState"] = self.support_state
        if self.support_status:
            payload["supportStatus"] = self.support_status
        return payload

    def supports(self, minimum_state: str) -> bool:
        if minimum_state not in SUPPORT_STATE_RANK:
            raise SourceRegistryError(f"Unknown support state {minimum_state!r}")
        return SUPPORT_STATE_RANK[self.support_state] >= SUPPORT_STATE_RANK[minimum_state]


class SourceRegistry:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.default_source = payload["defaultSource"]
        self.aliases: dict[str, str] = dict(payload.get("aliases", {}))
        self.sources: dict[str, FirmwareSource] = {
            name: _source_from_entry(name, entry) for name, entry in payload["sources"].items()
        }

    def canonical_name(self, name: str | None = None) -> str:
        selected = name or self.default_source
        canonical = self.aliases.get(selected, selected)
        if canonical not in self.sources:
            raise SourceRegistryError(f"Unknown source {selected!r}")
        return canonical

    def get(self, name: str | None = None) -> FirmwareSource:
        return self.sources[self.canonical_name(name)]

    def as_legacy_payload(self, *, include_aliases: bool = True) -> dict[str, Any]:
        sources = {name: source.as_legacy_entry() for name, source in self.sources.items()}
        if include_aliases:
            for alias, canonical in self.aliases.items():
                if alias not in sources and canonical in self.sources:
                    entry = self.sources[canonical].as_legacy_entry()
                    entry["canonicalSource"] = canonical
                    sources[alias] = entry
        payload: dict[str, Any] = {
            "defaultSource": self.default_source,
            "sources": sources,
        }
        if self.aliases:
            payload["aliases"] = dict(self.aliases)
        return payload


def load_sources_payload(path: Path = SOURCES_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_source_registry(path: Path = SOURCES_FILE) -> SourceRegistry:
    payload = load_sources_payload(path)
    errors = validate_sources_payload(payload)
    if errors:
        raise SourceRegistryError("; ".join(errors))
    return SourceRegistry(payload)


def validate_sources_payload(payload: Any, *, root: Path = ROOT_DIR) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["configs/sources.json must contain an object"]

    default_source = payload.get("defaultSource")
    sources = payload.get("sources")
    aliases = payload.get("aliases", {})
    if not isinstance(default_source, str) or not default_source:
        errors.append("configs/sources.json defaultSource must be a non-empty string")
    if not isinstance(sources, dict) or not sources:
        errors.append("configs/sources.json sources must be a non-empty object")
        return errors
    if not isinstance(aliases, dict):
        errors.append("configs/sources.json aliases must be an object when present")
        aliases = {}

    if isinstance(default_source, str) and default_source not in sources:
        errors.append("configs/sources.json defaultSource must name a canonical source entry")

    for name, entry in sources.items():
        if not isinstance(name, str) or not SOURCE_NAME_RE.fullmatch(name):
            errors.append(f"source {name!r} must use a lowercase source alias")
        if not isinstance(entry, dict):
            errors.append(f"source {name} must be an object")
            continue
        missing = sorted(REQUIRED_SOURCE_KEYS - set(entry))
        if missing:
            errors.append(f"source {name} is missing required keys: {', '.join(missing)}")
        repo_url = entry.get("repoUrl", "")
        if not isinstance(repo_url, str) or not repo_url.startswith("https://"):
            errors.append(f"source {name} repoUrl must be an https URL")
        for key in ("ref", "resolvedCommit"):
            value = entry.get(key, "")
            if not isinstance(value, str) or not SOURCE_REF_RE.fullmatch(value):
                errors.append(f"source {name} {key} must be a pinned 40-character commit SHA")
        ref = entry.get("ref")
        resolved_commit = entry.get("resolvedCommit")
        if (
            isinstance(ref, str)
            and isinstance(resolved_commit, str)
            and SOURCE_REF_RE.fullmatch(ref)
            and SOURCE_REF_RE.fullmatch(resolved_commit)
            and ref != resolved_commit
        ):
            errors.append(f"source {name} ref must match resolvedCommit")
        if entry.get("releaseTag") is not None and not isinstance(entry.get("releaseTag"), str):
            errors.append(f"source {name} releaseTag must be a string when present")
        if entry.get("profile") != "gamma":
            errors.append(f"source {name} profile must be gamma")
        support_state = entry.get("supportState")
        if support_state not in SUPPORT_STATE_RANK:
            errors.append(
                f"source {name} supportState must be one of: {', '.join(SUPPORT_STATES)}"
            )
        for path_key in ("patchSeries", "nvsTemplate"):
            value = entry.get(path_key, "")
            if value and not isinstance(value, str):
                errors.append(f"source {name} {path_key} must be a repository-relative path")
                continue
            if value and Path(value).is_absolute():
                errors.append(f"source {name} {path_key} must be repository-relative")
            if value and ".." in Path(value).parts:
                errors.append(f"source {name} {path_key} must stay inside the repository")
            if value and not (root / value).is_file():
                errors.append(f"source {name} {path_key} must exist: {value}")
        build_vars = entry.get("buildVars", {})
        if build_vars and not isinstance(build_vars, dict):
            errors.append(f"source {name} buildVars must be an object when present")
        elif isinstance(build_vars, dict):
            for key, value in build_vars.items():
                if not isinstance(key, str) or not key:
                    errors.append(f"source {name} buildVars keys must be non-empty strings")
                if not isinstance(value, str):
                    errors.append(f"source {name} buildVars values must be strings")
        if entry.get("initSubmodules") is not None and not isinstance(entry.get("initSubmodules"), bool):
            errors.append(f"source {name} initSubmodules must be a boolean when present")
        qemu_memory_mb = entry.get("qemuMemoryMb", 32)
        if not isinstance(qemu_memory_mb, int) or qemu_memory_mb <= 0 or qemu_memory_mb > 32:
            errors.append(f"source {name} qemuMemoryMb must be an integer from 1 to 32 when present")

    for alias, canonical in aliases.items():
        if not isinstance(alias, str) or not SOURCE_NAME_RE.fullmatch(alias):
            errors.append(f"source alias {alias!r} must use a lowercase source alias")
        if alias in sources:
            errors.append(f"source alias {alias} must not duplicate a canonical source")
        if not isinstance(canonical, str) or canonical not in sources:
            errors.append(f"source alias {alias} must target a canonical source")

    return errors


def _source_from_entry(name: str, entry: dict[str, Any]) -> FirmwareSource:
    build_vars = entry.get("buildVars", {})
    return FirmwareSource(
        name=name,
        display_name=entry["displayName"],
        repo_url=entry["repoUrl"],
        ref=entry["ref"],
        resolved_commit=entry["resolvedCommit"],
        patch_series=entry["patchSeries"],
        profile=entry["profile"],
        release_tag=entry.get("releaseTag", ""),
        nvs_template=entry.get("nvsTemplate", ""),
        build_vars=dict(build_vars) if isinstance(build_vars, dict) else {},
        init_submodules=bool(entry.get("initSubmodules", False)),
        qemu_memory_mb=int(entry.get("qemuMemoryMb", 32)),
        support_state=entry.get("supportState", "metadata_only"),
        support_status=entry.get("supportStatus", ""),
    )
