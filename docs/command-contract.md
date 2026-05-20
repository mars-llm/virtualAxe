# Command Contract

This file classifies repository commands by side effect so users can choose the
right gate without guessing.

## Safety Classes

- `tracked-source read-only`: does not modify tracked files or ignored local
  state. It may inspect tracked source, git metadata, or existing ignored state.
- `report-generating`: does not modify tracked files, but writes ignored reports
  under `out/`.
- `ignored-state mutating`: does not modify tracked files, but creates or
  updates ignored local state such as `.sources/`, `.worktrees/`, `.state/`,
  `.venv/`, browser `node_modules/`, `out/`, or disposable patch worktrees.
- `runtime mutating`: starts or stops QEMU or local services and may write
  runtime state, logs, PIDs, NVS state, or local dashboard state.
- `destructive`: deletes local state, worktrees, caches, evidence, or other
  files. Destructive commands require an explicit operator decision.
- `external/live`: contacts live pools, upstream repositories, package feeds, or
  other public services and depends on external behavior.

## Commands

| Command | Class | Contract |
| --- | --- | --- |
| `make audit` | report-generating | Writes JSON and Markdown readiness reports under ignored `out/audit/`. |
| `make validate` | ignored-state mutating | Prepares deterministic local dependencies, then runs local checks plus configured-pin patch and submit gates. It may create `.sources/`, `.worktrees/`, `.state/`, `.venv/`, browser `node_modules/`, `out/`, and disposable patch worktrees. It never runs live pools. |
| `make validate-lite` | ignored-state mutating | Prepares the locked Python environment, then runs fast CI-safe checks: Python compile, shell syntax, config validation, secret scan, release hygiene, and Python tests. |
| `make validate-config` | tracked-source read-only | Validates tracked source, profile, and NVS configuration. Generated manifests are validated only when passed explicitly to the validator. |
| `make secret-scan` | tracked-source read-only | Scans tracked files for private material and reports location plus classification only. |
| `make local-state-report` | tracked-source read-only | Reports ignored/generated state, size, git classification, and cleanup guidance without deleting anything. |
| `make clean-clone-smoke` | external/live, ignored-state mutating | Requires a clean tracked worktree, clones the current repository into a temporary directory, then runs the first-user path: `./vaxe`, `make help`, `make drift-check`, `make validate-lite`, `make build SOURCE=bitaxe`, and `make build SOURCE=nerdnos`. The temporary clone is removed on success and preserved on failure for inspection. |
| `make drift-check` | ignored-state mutating | Checks configured-pin patch apply in a disposable temp directory, reports patch-series hash, generated manifest status, and local state presence. |
| `make patch-audit` | tracked-source read-only | Reports patch subjects, touched files, hunk counts, changed-line counts, touched surfaces, keep reasons, and follow-up recommendations. |
| `make patch-check` | ignored-state mutating | Applies the default Bitaxe patch stack to a disposable target directory. It may replace `PATCH_TARGET_DIR` after patch-target safety checks. |
| `make patch-check SOURCE=nerdnos` | ignored-state mutating | Applies the NerdNos source-specific patch series to a disposable target directory. |
| `make patch-check-upstream` | external/live, ignored-state mutating | Fetches upstream and applies patches against `UPSTREAM_REF`. Failure is maintenance drift unless the configured pin also fails. |
| `make release-evidence` | report-generating | Normalizes the latest `make verify-release` summary into ignored JSON and Markdown reports under `out/release-evidence/`. |
| `make sync-upstream` | external/live, ignored-state mutating | Fetches the configured upstream source into `.sources/`. |
| `./vaxe` | tracked-source read-only | Prints usage and concrete start examples. It does not build, start QEMU, contact pools, or mutate local runtime state. |
| `./vaxe --source bitaxe` | runtime mutating, external/live when first-run dependencies are missing | Starts the default Bitaxe source. It builds or reuses the matching QEMU image, starts the firmware, and shows the local AxeOS URL. Test the image by opening `http://127.0.0.1:18080` and reading `/api/system/info`. |
| `./vaxe --source nerdnos` | runtime mutating, external/live when first-run dependencies are missing | Starts the NerdNos source with the same `gamma` virtual profile. It builds or reuses `out/nerdnos/gamma/qemu_flash.bin`, starts the firmware, and shows the local AxeOS URL. Test the image by opening `http://127.0.0.1:18080` and reading `/api/system/info`. |
| `./vaxe --source bitaxe --sim-actions` | runtime mutating, external/live when first-run dependencies are missing | Starts the Bitaxe source with the local Simulation Actions proxy. `/sim/*` endpoints are local UI/operator-flow controls and must not alter mining, Stratum, share accounting, or normal API response shape. |
| `./vaxe --source nerdnos --sim-actions` | runtime mutating, external/live when first-run dependencies are missing | Starts the NerdNos source with the local Simulation Actions proxy. Use `/sim/capabilities` to verify the proxy and `127.0.0.1:18082` to inspect the source-native firmware backend. |
| `./scripts/install-vaxe.sh` | ignored-state mutating | Optional convenience command that installs a local `vaxe` launcher symlink on `PATH`. It is not required for first-run setup. |
| `make build SOURCE=bitaxe` | ignored-state mutating, external/live when first-run dependencies are missing | Builds the default Bitaxe reusable QEMU image under `out/` and writes a matching manifest. If the dev container image or AxeOS frontend dependencies are missing, the build provisions them automatically. Container runtime selection is health-checked: Docker must be reachable, and Podman is used only after `podman info` succeeds or an existing Podman machine starts successfully. Human TTY runs show a terminal build cockpit with reference clean-build timing, stage progress, elapsed time, reusable-image paths, and flight-recorder notes while the full compiler log is written under `out/`. Successful builds print the image path, manifest path, boot command, deterministic replay command, and rebuild command. Non-interactive runs keep stable line-oriented progress. |
| `make build SOURCE=nerdnos` | ignored-state mutating, external/live when first-run dependencies are missing | Builds the NerdNos reusable QEMU image under `out/nerdnos/gamma/` and writes a matching manifest. If the dev container image or AxeOS frontend dependencies are missing, the build provisions them automatically. Container runtime selection is health-checked: Docker must be reachable, and Podman is used only after `podman info` succeeds or an existing Podman machine starts successfully. Human TTY runs show a terminal build cockpit with reference clean-build timing, stage progress, elapsed time, reusable-image paths, and flight-recorder notes while the full compiler log is written under `out/nerdnos/gamma/`. Successful builds print the image path, manifest path, boot command, deterministic replay command, and rebuild command. Non-interactive runs keep stable line-oriented progress. Current NerdNos validation covers build, QEMU API boot, deterministic submit replay, and automated external/live accepted-share proof. |
| `make run` | runtime mutating | Launches QEMU and writes runtime state/logs. It builds the selected image first only when the reusable image is missing. |
| `make dashboard` | runtime mutating | Starts the local operator dashboard. |
| `make e2e` | runtime mutating | Runs QEMU/API/browser validation against local runtime state. |
| `make verify-submit-replay` | runtime mutating | Runs deterministic local Stratum replay and QEMU firmware proof. |
| `make verify-release` | external/live | Runs the automated live PublicPool, Bitronics, and Nerdminers smoke gate with the default public test pool user. Each pool phase exits when its accepted-share proof requirement is met, a fatal invariant fails, or the phase timeout is reached. |
| `VERIFY_RELEASE_MODE=qualification make verify-release` | external/live | Runs the automated release-prep qualification gate with the same three-pool verifier. Each pool phase requires five pool-side accepted shares and zero rejected-share delta violation. Pool-side proof means direct remote-pool Stratum accepted responses for current-phase submits, or worker-bound pool stats accepted-share counters when available. Firmware/API counters, best-difficulty/chart data, worker-active status, and generic QEMU logs are diagnostic only. The wait helper streams progress diagnostics and fails early when fatal invariants such as API readability, expected pool identity, or rejected-share limits are broken. |
| `make test-ci` | runtime mutating | Runs upstream ESP-IDF test-ci proof when the selected source provides it. Sources without an upstream `test-ci/` project use the source-aware QEMU API boot smoke with local-only closed Stratum endpoints. |
| `make test-api` | runtime mutating | Runs API validation against a local virtual runtime. |
| `make test-browser` | runtime mutating | Runs browser smoke validation against a local virtual runtime. |
| `make verify-persistence` | runtime mutating | Verifies settings persistence across restart/rebuild. |
| `make doctor` | tracked-source read-only | Probes canonical configured sources and local tool availability. |

## Release Rule

Public release readiness requires `make validate`, fresh same-session
`make verify-release` runs for the sources being claimed, and
`make release-evidence` to normalize ignored release evidence. If live
verification has not been run for a claimed source, that source is not release
ready.
