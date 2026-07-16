# Upstream Integration

Upstream firmware sources are fetched from the repositories and refs configured
in `configs/sources.json`. Fetched checkouts live in ignored local state under
`.sources/`; they are not shipped as part of this repository.

The release contract supports only `gamma`, mined by the guest-side ESP32-S3
virtual ASIC path. `bitaxe` is the default source. `nerdnos` selects the
NerdNos / NerdQAxePlus firmware source, not a new virtual hardware profile.

Sync workflow:

1. Fetch or reset the configured upstream checkout with `scripts/sync-upstream.sh`.
2. Re-apply the patch series with `scripts/apply-patches.sh`.
3. Build a reusable `gamma` QEMU image with `make build SOURCE=bitaxe` or
   `make build SOURCE=nerdnos`.
4. Run local tests, clean patch apply, firmware unit proof, and `verify-release`.

Patch drift checks:

```sh
make patch-check
make patch-check SOURCE=nerdnos
make patch-check-upstream
```

`make patch-check` applies the default Bitaxe stack against the configured
source pin in a disposable directory and reports the touched upstream surfaces.
`make patch-check SOURCE=nerdnos` applies the NerdNos source-specific series
against `ESP-Miner-NerdQAxePlus` `v1.0.37`. Current NerdNos validation covers
patch-check, build, QEMU API boot, deterministic submit replay, and live pool
qualification evidence. Qualification requires
pool-side accepted-share proof: either direct live Stratum accepted responses
from the remote pool for current-phase submits, or worker-bound pool stats
accepted-share counters when those counters are available. Firmware/API counters
are diagnostic only. Bitronics status evidence remains diagnostic because it is
not an accepted-share counter. PublicPool is an optional interoperability target,
not a release quality gate, because its public deployment policy can change
independently of the configured source pin.
The upstream drift check
`make patch-check-upstream` performs the same check against
`origin/master` after a fetch. It is an upstream-risk signal only; it does not
change `configs/sources.json` or the release pin.

Source-specific runtime API guarantees:

- Bitaxe smoke covers `/api/system/info` virtual Gamma metadata, the
  `/api/system/statistics/dashboard` JSON alias, `/api/system` persistence for
  pool/network/tuning settings, `/api/system/asic` tuning options, and the
  Bitaxe AxeOS dashboard, pool, network, and tuning form flows.
- NerdNos smoke covers `/api/system/info` source-native Gamma identity
  (`deviceModel == "virtualAxe Gamma"`, one BM1370 ASIC, OpenETH host IP, MAC,
  and connected network status), top-level Stratum settings, nested
  `stratum.pools[]` pool telemetry, gzip JSON responses, and browser dashboard
  loading.

Tracked NerdNos screenshots under `img/` are captured through
`vaxe --source nerdnos` against the same `gamma` virtual profile. They are
source-specific UI smoke evidence only. They are not independent pool-side
proof, and they do not make NerdNos a separate hardware profile or imply full
NerdNos feature parity.

NerdNos tests must not require Bitaxe-only fields such as `isVirtual`,
`virtualAsicWorkers`, `poolConnectionInfo`, `blockHeight`, or Bitaxe-specific
settings forms. Add those as NerdNos guarantees only when the pinned NerdNos
source exposes them cleanly in QEMU and tests cover them directly.

Use `make patch-check-upstream` before a release when you want an upstream-head
drift signal. Treat upstream-head failures as rebase work unless the configured
source pin also fails.

Patch order comes from each source's configured `series.txt`, not filesystem
glob order. Bitaxe patches live under `patches/esp-miner/bitaxe/`; NerdNos
patches live under `patches/esp-miner/nerdnos/`. Keep reasons are documented in
[docs/patch-stack.md](patch-stack.md).

Keep rules:

- required to build or boot `gamma`
- required for AxeOS, API, or NVS persistence correctness
- required to keep nonce search, target checks, clean-jobs handling, and submit
  validation inside the guest firmware
- required for PublicPool, Bitronics, or Nerdminers Stratum interoperability
- required to keep source-specific UI branding correct in the virtual runtime

Do not introduce host-side proof-of-work, relay-assisted proof-of-work, or
diagnostic firmware surfaces that are not part of the product.
