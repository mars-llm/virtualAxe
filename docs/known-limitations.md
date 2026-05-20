# Known Limitations

- Only the `gamma` virtual profile is supported.
- The virtual ASIC is a software-backed guest implementation, not a cycle-accurate
  BM1370 device model.
- Display, button, fan, VCORE, and thermal paths are deterministic virtual
  software surfaces.
- Hashrate follows guest scheduling and host CPU availability; there is no public
  per-chip pacing interface.
- Passing virtual UI, API, persistence, or pool checks does not certify behavior
  on physical Bitaxe hardware.
- Remote-pool verification requires outbound network access and enough runtime
  for real guest-side proof-of-work to produce accepted shares.

`verify-release` is the acceptance surface for remote pools. Smoke mode is the
default short gate. Qualification mode is the explicit release-prep gate for
collecting repeated pool-side accepted-share proof.

## Toolchain Reproducibility

- Python test/runtime helper dependencies are locked through `uv.lock`.
- Browser test dependencies are locked through `tests/browser/package-lock.json`.
- ESP-Miner source is pinned in `configs/sources.json`.
- The development container uses an ESP-IDF image tag, but operating-system
  package feeds, NodeSource packages, ESP-IDF tool downloads, and container
  registry contents can still drift unless the operator builds from an already
  reviewed local image or adds stricter external pinning for that release.
