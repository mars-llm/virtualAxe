# virtualAxe Agent Guide

This file is the repository contract for coding and operations agents. Read it
with `README.md`, `docs/architecture.md`, and `docs/command-contract.md` before
changing source or running mutating commands.

## Project Boundary

virtualAxe fetches pinned upstream firmware, applies source-specific patch
series, and runs a virtual Bitaxe Gamma in ESP32-S3 QEMU.

- `bitaxe` is the default `bitaxeorg/ESP-Miner` source.
- `nerdnos` is the supported NerdNos / NerdQAxePlus source.
- `gamma` is the only virtual hardware profile.
- Upstream firmware belongs in ignored `.sources/`; never vendor it.
- Do not change a configured source pin without maintainer approval.

## Protected Runtime Invariants

- Keep proof-of-work inside the guest firmware's virtual ASIC path.
- Do not add host-side or relay-assisted proof-of-work.
- Do not add fabricated accepted-share paths.
- Keep Normal Mode on the real patched firmware, AxeOS, API, NVS, and Stratum
  path in QEMU.
- Keep Simulation Actions opt-in, loopback-only, and isolated under `/sim/*`.
- Simulation Actions must not affect hashrate, pools, Stratum, share accounting,
  CPU mining, or virtual ASIC submit behavior.
- Normal ESP-Miner-compatible API responses must not expose simulator metadata.
- Preserve the Bitaxe submit boundary: `ASIC_send_work()`,
  `ASIC_process_work()`, `asic_result_task()` revalidation with
  `test_nonce_value()`, and `rolled_version` conversion to `version_bits` only
  at submit time.
- Keep the Bitaxe `0044`/`0045` canonical-header and validator-alignment
  boundary unless a maintainer explicitly approves a tested replacement.

## Source Of Truth

- `configs/sources.json`: source repositories, immutable pins, patch series,
  support state, and source-specific build settings.
- `configs/profiles/gamma.json`: virtual hardware identity.
- `configs/nvs/config-virtual.csv`: seeded runtime configuration.
- `patches/esp-miner/<source>/series.txt`: ordered upstream patch stack.
- `docs/patch-stack.md`: patch keep reasons and verification methods.
- `docs/command-contract.md`: command side effects and release gates.

`.sources/`, `.worktrees/`, `.state/`, `.venv/`, browser dependencies, and
generated `out/` contents are disposable local state, not source of truth. Run
`make local-state-report` before trusting existing local outputs. Keep private
scratch notes and follow-ups in ignored `.state/todo_local.md`.

## Operating Contract

Start with:

```sh
git status --short
make local-state-report
make validate SOURCE=bitaxe
```

Use `make validate SOURCE=nerdnos` when changing shared source management,
NerdNos integration, or source-agnostic runtime behavior. Use
`make drift-check` to compare the configured Bitaxe pin, patch series, manifest,
and local state. An upstream-head patch failure is maintenance drift unless the
configured pin also fails.

Command safety classes are defined in `docs/command-contract.md`:

- Read-only checks inspect tracked source or existing state.
- Report-generating commands write ignored files under `out/`.
- Ignored-state commands may fetch sources, prepare worktrees, or build output.
- Runtime commands may start QEMU and update local NVS state.
- External/live commands contact upstream services or mining pools.

Do not run `make verify-release` or qualification mode without explicit
approval in the current session. Do not delete local state or release evidence
without first reviewing the target and obtaining approval. `state reset`
accepts managed `.state/*` targets; an external custom target additionally
requires `VIRTUALAXE_CONFIRM_STATE_RESET=1` and protected paths are always
refused.

## Patch Changes

Do not edit patch behavior as routine cleanup. Before changing a patch:

1. State the concrete release or correctness defect.
2. Read the entire patch and its adjacent dependencies.
3. Preserve source-specific virtual guards and submit validation.
4. Run the source-specific patch check and deterministic firmware gates.
5. Update the patch rationale and verification contract if its responsibility
   changes.

The patch stacks are documented and release-scoped; they are not claimed to be
hunk-minimized.

## Change Discipline

- Preserve user changes and keep diffs narrowly scoped.
- Do not weaken negative or error-path tests.
- Do not commit generated state, logs, credentials, pool identities, or release
  evidence.
- Use repository-native validation and report exact commands run.
- Keep public documentation focused on durable user behavior. Put local process
  notes in `.state/todo_local.md`.
- Ask before changing public commands, release criteria, source pins, pool
  verification policy, patch semantics, or runtime mode semantics.
