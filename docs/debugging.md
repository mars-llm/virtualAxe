# Debugging

- Start with `./vaxe --source bitaxe` or `./vaxe --source nerdnos` from the
  repository root. Bare `./vaxe` prints usage and does not start QEMU.
- Use `vaxe --source bitaxe --pool public`, `vaxe --source bitaxe --pool
  bitronics`, or `vaxe --source nerdnos --pool nerdminers` to switch to a
  verified pool preset. Use `vaxe --source bitaxe --pool host:port` for manual
  host/port testing.
- Use `make build SOURCE=bitaxe` or `make build SOURCE=nerdnos` to generate a
  reusable QEMU image without starting the runtime.
- Inspect `out/build.log` or `out/nerdnos/gamma/build.log` for build or AxeOS
  packaging failures.
- Use `make run` and inspect `out/qemu.log` for boot, DHCP, Stratum, or submit
  issues. The default AxeOS URL is `http://127.0.0.1:18080`.
- While a runtime is active, open `http://127.0.0.1:18080` and read
  `http://127.0.0.1:18080/api/system/info` to confirm the image boots, serves
  AxeOS, and exposes the source-native firmware API.
- Use `./vaxe --source bitaxe --sim-actions` and `./vaxe --source nerdnos
  --sim-actions` to test the local Simulation Actions proxy. Check
  `/sim/capabilities`, then compare `127.0.0.1:18080/api/system/info` with the
  backend at `127.0.0.1:18082/api/system/info`.
- Use `make dashboard` for the terminal operator view.
- Use `/api/system/info` while QEMU is running to inspect pool settings, worker
  metadata, share counters, and virtual device metadata.
- Use `make verify-submit-replay` for deterministic local submit-boundary proof.
- Use `make verify-release` for the Bitronics and Nerdminers remote-pool smoke
  gate. PublicPool is an optional interoperability target, not a release gate.
- Use `VERIFY_RELEASE_MODE=qualification make verify-release` for the
  release-prep qualification gate.
- Re-run `scripts/apply-patches.sh` after changing or syncing the configured
  upstream source checkout.
