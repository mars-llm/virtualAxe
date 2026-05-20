# Patch Stack

Each supported upstream source has its own ordered patch stack:

- Bitaxe ESP-Miner:
  [patches/esp-miner/bitaxe/series.txt](../patches/esp-miner/bitaxe/series.txt)
- NerdNos / NerdQAxePlus:
  [patches/esp-miner/nerdnos/series.txt](../patches/esp-miner/nerdnos/series.txt)

The selected stack is exported from the pinned upstream ref in
`configs/sources.json` and applies into disposable worktrees under `.worktrees/`.

Each patch is split by release boundary, not by chronology. A patch should be
removable only when the feature or failure class it owns is removed, replaced
upstream, or proven redundant by patch-check, build, API boot, submit replay,
and live-pool evidence. Files outside the selected source's `series.txt` are
not part of that source's release stack.

## Bitaxe Patch Stack

| Patch | Release Rationale | Purpose |
| --- | --- | --- |
| `0001-virtual-gamma-add-qemu-firmware-foundation.patch` | Required to compile and boot a virtual Gamma board under ESP32-S3 QEMU. | Adds the virtual Gamma QEMU target, QEMU network backend, guest-side virtual ASIC foundation, measured virtual hashrate reporting, hardware stubs, and system API metadata required to boot and inspect the device. |
| `0002-virtual-mining-keep-stratum-and-worker-responsive.patch` | Prevents stale Stratum work from blocking fresh jobs while the guest scans nonces. | Allows fresh Stratum work to preempt stale guest searches and keeps the mining worker cooperative under QEMU. |
| `0003-virtual-axeos-support-qemu-partitions-and-openeth-ui.patch` | Makes AxeOS, HTTP, NVS, and the network form work against QEMU loopback/OpenETH. | Provides QEMU partition handling, loopback WebUI/API origin support, and a QEMU-network-aware AxeOS network form. |
| `0004-virtual-mining-preserve-work-and-reduce-nonce-overhead.patch` | Cuts guest per-nonce overhead without discarding valid in-flight pool work. | Reduces guest per-nonce overhead and preserves valid in-flight work across scheduler updates. |
| `0005-virtual-gamma-apply-profile-metadata-and-deterministic-sensors.patch` | Makes device identity, lanes, and thermal telemetry deterministic for repeatable QEMU/API/browser gates. | Applies Gamma profile metadata, deterministic worker lanes, deterministic thermal state, and virtual backend routing. |
| `0006-virtual-mining-precompute-nonce-search-material.patch` | Moves invariant SHA header setup out of the nonce loop so live low-difficulty shares are feasible inside the guest. | Precomputes invariant SHA material used by the guest nonce search path. |
| `0007-virtual-api-handle-qemu-patch-and-static-responses.patch` | Prevents API settings updates from corrupting responses or leaving runtime config stale. | Handles full HTTP PATCH payloads, avoids shared static response buffer corruption, and syncs runtime settings after API updates. |
| `0008-virtual-mining-keep-guest-worker-responsive-under-qemu.patch` | Bounds mining batches so HTTP, NVS, and Stratum tasks keep running under QEMU load. | Bounds guest search batches so API, NVS, and Stratum tasks remain responsive while mining. |
| `0044-virtual-share-canonical-header-material.patch` | Establishes one block-header byte contract for guest search, validation, and submit. | Defines the canonical block-header material shared by guest search and validation. |
| `0045-virtual-align-guest-digest-path-with-software-validator.patch` | Keeps the fast digest filter aligned with the validator while preserving rolled-version submit behavior. | Aligns guest digest filtering with the software validator while preserving submit-time version-bit handling. |
| `0046-virtual-guard-submit-boundary-with-work-generations.patch` | Stops clean-jobs-invalidated work from reaching submit after a candidate is found. | Adds immutable work descriptors, generation checks, and no-false-negative candidate filtering so clean jobs invalidate stale virtual submissions without slowing low-difficulty guest search. |
| `0047-virtual-api-keep-settings-updates-responsive.patch` | Prevents repeated AxeOS settings writes from stalling the API while preserving persisted values. | Coalesces queued NVS settings writes so API updates stay responsive while preserving persisted values. |
| `0048-virtual-pool-support-low-difficulty-interoperability.patch` | Makes the virtual miner interoperate with the low-difficulty public pools used for release evidence. | Adds virtual-scoped Stratum subscribe identity overrides, fractional suggested difficulty, low-difficulty Stratum timing, and submit-response recovery required by the blocking pool smoke gate. |

`0044` and `0045` remain separate by design. `0044` defines the canonical header
material boundary; `0045` aligns the digest/validator path against that boundary.
Submit semantics remain upstream-shaped: work is sent via `ASIC_send_work()`,
results flow through `ASIC_process_work()`, `asic_result_task()` revalidates with
`test_nonce_value()`, and rolled-version bits are converted only at submit time.

## NerdNos Patch Stack

NerdNos source support targets
`shufps/ESP-Miner-NerdQAxePlus` `v1.0.37` at
`c18abafebde66c39f4bd8ae6d839088b84b4e79c`. NerdNos has a different firmware,
board, ASIC, Stratum, and dashboard layout from the default Bitaxe source, so
its virtualAxe support uses a source-specific patch series under
`patches/esp-miner/nerdnos/`.

| NerdNos Patch | Release Rationale | Purpose |
| --- | --- | --- |
| `0001-nerdnos-add-virtual-gamma-api-boot-path.patch` | Required to boot the NerdNos fork as virtual Gamma in ESP32-S3 QEMU. | Adds a source-specific `VIRTUALAXE_GAMMA` board, QEMU OpenETH networking, display guards, DNS startup guards, deterministic API telemetry, and single-write JSON responses required to boot NerdNos in ESP32-S3 QEMU and expose `/api/system/info` through the normal readiness gate. |
| `0002-nerdnos-add-virtual-asic-submit-path.patch` | Adds the NerdNos-native virtual ASIC path and guards stale work at submit. | Adds a source-specific virtual BM1370 ASIC, immutable job generation checks before and after nonce validation, fractional Stratum V1 difficulty handling, OpenETH-aware Stratum connectivity, and virtual startup tasks kept below the HTTP server priority so NerdNos can prove `sendWork()`/`processWork()`/`test_nonce_value()` through deterministic submit replay without starving API readiness or submitting work invalidated before the submit boundary. |
| `0003-nerdnos-keep-virtual-mining-api-responsive.patch` | Keeps NerdNos pool work fresh without starving the source-native HTTP/API tasks. | Keeps NerdNos live mining responsive by sending virtual ASIC work once per new Stratum job or assigned difficulty change, allowing queued pool work to preempt stale guest searches, keeping the virtual worker at the Bitaxe-equivalent priority, yielding cooperatively, and idling the virtual result task when no result is queued. |
| `0004-nerdnos-low-difficulty-pool-interoperability.patch` | Preserves fractional pool difficulty and Stratum setup ordering required by public low-difficulty pools. | Preserves fractional suggested and assigned pool difficulty, carries source-native primary/fallback difficulty through the Stratum config snapshot, uses a Nerdminer-compatible virtual subscribe identity, reserves optional setup response IDs so first-submit accepts are counted as shares, and prevents V1 job construction until subscribe extranonce and notify data are both present. |
| `0005-nerdnos-precompute-virtual-nonce-search-material.patch` | Keeps NerdNos live-share throughput inside the guest by precomputing invariant header material. | Precomputes invariant SHA-256 header material in the NerdNos virtual ASIC so low-difficulty live smoke phases can produce guest-side submits reliably without host-side proof-of-work or verifier leniency. |
| `0006-nerdnos-brand-virtualaxe-header.patch` | Fixes source-specific UI branding for the shipped virtual runtime screenshots. | Keeps the NerdNos header logo source-native while adding a `virtualaxe` sublabel for the virtual Gamma runtime, avoiding a broken logo asset path in source-specific UI screenshots. |

The patch stack itself proves clean source-specific apply behavior and supports
the deterministic NerdNos build, QEMU API boot, and submit-replay gates. NerdNos
live qualification evidence is recorded separately by release evidence. The
current qualification standard requires PublicPool, Bitronics, and Nerdminers to
pass in the same run with five validated remote-pool Stratum accepted responses
per pool and zero rejected-share delta. That evidence does not claim full
NerdNos feature parity.
