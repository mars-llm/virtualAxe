# Architecture

`virtualAxe` is a wrapper around a configured upstream ESP-Miner revision. It
fetches source into an ignored local cache, applies the local patch stack into
disposable worktrees, then builds a QEMU-only ESP32-S3 firmware image for the
virtual Bitaxe Gamma profile.

The virtual target keeps upstream-facing behavior wherever possible:

1. ESP-Miner firmware runs with `CONFIG_BITAXE_VIRTUAL=y`.
2. QEMU networking provides outbound Stratum access and local AxeOS access.
3. The real upstream HTTP server serves the real AxeOS bundle from SPIFFS.
4. NVS-backed settings are rendered, persisted, and migrated through the same
   firmware API surfaces used by AxeOS.
5. The virtual ASIC backend consumes upstream work and submits nonce results
   through the normal ESP-Miner result path.

The wrapper control plane is intentionally small:

- `configs/sources.json` pins the supported upstream checkout.
- `configs/profiles/gamma.json` defines the only supported virtual profile.
- `scripts/virtualaxe.py` exposes build, run, verification, dashboard, and state
  commands.
- `.sources/` stores ignored upstream source checkouts.
- `.state/` stores mutable local device state.
- `.worktrees/` stores patched disposable upstream trees.
- `out/` stores generated reusable QEMU images, logs, manifests, and evidence.

`make build SOURCE=bitaxe` writes the default reusable QEMU image under `out/`.
`make build SOURCE=nerdnos` writes the NerdNos image under `out/nerdnos/gamma/`.
Runtime and verification commands reuse matching images until tracked build
inputs or requested source/profile/configuration change.

The mining boundary stays upstream-shaped. Work enters the virtual ASIC through
`ASIC_send_work()`, results return through `ASIC_process_work()`, and
`asic_result_task()` revalidates with `test_nonce_value()` before submit.
Rolled-version bits are converted to submit-time `version_bits` only at the
submit boundary.

`verify-release` is the remote-pool integration gate for the virtual submit path.
It runs `gamma` against PublicPool, Bitronics, and Nerdminers. Smoke mode is short
and defaults to one accepted share per pool. Qualification mode is explicit and
requires five pool-side accepted shares per pool in the same run. Qualification
can use direct live Stratum accepted responses from the
remote pool or delayed worker-bound pool stats accepted-share counters when a
pool exposes them. Firmware/API counters, best-difficulty/chart data,
worker-active status, and generic QEMU logs remain diagnostic evidence; they do
not satisfy qualification thresholds unless the log entry is validated as a real
remote-pool accepted response to a current-phase `mining.submit`.
