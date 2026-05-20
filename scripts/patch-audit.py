#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_registry import load_source_registry


KEEP_REASONS = {
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
    "0001-nerdnos-add-virtual-gamma-api-boot-path.patch": "Required to boot the NerdNos fork as virtual Gamma in ESP32-S3 QEMU.",
    "0002-nerdnos-add-virtual-asic-submit-path.patch": "Adds the NerdNos-native virtual ASIC path and guards stale work at submit.",
    "0003-nerdnos-keep-virtual-mining-api-responsive.patch": "Keeps NerdNos pool work fresh without starving the source-native HTTP/API tasks.",
    "0004-nerdnos-low-difficulty-pool-interoperability.patch": "Preserves fractional pool difficulty and Stratum setup ordering required by public low-difficulty pools.",
    "0005-nerdnos-precompute-virtual-nonce-search-material.patch": "Keeps NerdNos live-share throughput inside the guest by precomputing invariant header material.",
    "0006-nerdnos-brand-virtualaxe-header.patch": "Fixes source-specific UI branding for the shipped virtual runtime screenshots.",
}
RECOMMENDATIONS = {
    "0003-virtual-axeos-support-qemu-partitions-and-openeth-ui.patch": "Track whether AxeOS frontend hunks can move outside the upstream patch surface after release.",
    "0044-virtual-share-canonical-header-material.patch": "Keep boundary separate unless a smaller equivalent is proven by deterministic tests and live pool evidence.",
    "0045-virtual-align-guest-digest-path-with-software-validator.patch": "Keep validator alignment separate from canonical header material.",
    "0048-virtual-pool-support-low-difficulty-interoperability.patch": "Split into low-difficulty, subscribe-agent, timeout recovery, and UI-display patches.",
}


def source_patch_series(source_name: str) -> tuple[Path, list[str]]:
    source = load_source_registry().get(source_name)
    series_file = source.patch_series_path
    names = [
        line.strip()
        for line in series_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return series_file, names


def subject(text: str) -> str:
    match = re.search(r"^Subject: \[PATCH[^\]]*\] (.+)$", text, re.MULTILINE)
    return match.group(1) if match else ""


def body_field(text: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}\s*(.+?)(?=^[A-Z][A-Za-z -]+:|\n---\n)", text, re.MULTILINE | re.DOTALL)
    return " ".join(match.group(1).split()) if match else ""


def touched_files(text: str) -> list[str]:
    files = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4 and parts[3].startswith("b/"):
                files.append(parts[3][2:])
    return files


def surfaces(files: list[str]) -> list[str]:
    result = set()
    for file in files:
        if "axe-os" in file:
            result.add("AxeOS frontend")
        if "http_server" in file:
            result.add("HTTP API")
        if "nvs" in file:
            result.add("NVS")
        if "stratum" in file:
            result.add("Stratum")
        if "mining" in file or "asic_result" in file:
            result.add("mining validation")
        if "tasks/" in file:
            result.add("task scheduling")
        if "virtual_asic" in file or "CONFIG_BITAXE_VIRTUAL" in file:
            result.add("virtual-only code")
        if "CMakeLists" in file or "Kconfig" in file or "sdkconfig" in file:
            result.add("build/config")
    return sorted(result)


def audit_patch(name: str, patch_dir: Path) -> dict[str, Any]:
    path = patch_dir / name
    text = path.read_text(encoding="utf-8", errors="replace")
    files = touched_files(text)
    additions = 0
    deletions = 0
    hunk_count = 0
    for line in text.splitlines():
        if line.startswith("@@"):
            hunk_count += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    patch_surfaces = surfaces(files)
    risk = "high" if any(surface in patch_surfaces for surface in ("Stratum", "NVS", "HTTP API", "mining validation")) else "medium"
    if name.startswith("0044") or name.startswith("0045") or name.startswith("0046"):
        risk = "critical"
    return {
        "patch": name,
        "subject": subject(text),
        "touchedFiles": files,
        "hunkCount": hunk_count,
        "additions": additions,
        "deletions": deletions,
        "surfaces": patch_surfaces,
        "risk": risk,
        "keepReason": KEEP_REASONS.get(name, ""),
        "verification": body_field(text, "Verify:"),
        "recommendation": RECOMMENDATIONS.get(name, "Keep for release; revisit only with hunk-level proof and tests."),
    }


def audit() -> dict[str, Any]:
    registry = load_source_registry()
    source_reports = []
    for source_name in sorted(registry.sources):
        series_file, names = source_patch_series(source_name)
        patches = [audit_patch(name, series_file.parent) for name in names]
        source_reports.append(
            {
                "source": source_name,
                "patchSeries": str(series_file.relative_to(ROOT_DIR)),
                "patchCount": len(patches),
                "patches": patches,
            }
        )
    patch_count = sum(report["patchCount"] for report in source_reports)
    return {
        "status": "reported",
        "patchCount": patch_count,
        "hunkMinimized": False,
        "sources": source_reports,
        "patches": [
            {**patch, "source": report["source"]}
            for report in source_reports
            for patch in report["patches"]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report hunk-level ESP-Miner patch metadata.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = audit()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for source in payload["sources"]:
            for patch in source["patches"]:
                print(
                    f"{source['source']}/{patch['patch']}: "
                    f"{patch['hunkCount']} hunks, {patch['risk']} risk, "
                    f"{', '.join(patch['surfaces']) or 'unclassified'}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
