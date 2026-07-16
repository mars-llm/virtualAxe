SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
SOURCE ?= $(if $(SOURCE_NAME),$(SOURCE_NAME),bitaxe)
VIRTUAL_PROFILE ?= gamma
HTTP_PORT ?= 18080
VERIFY_RELEASE_MODE ?= smoke
PATCH_TARGET_DIR ?= /tmp/virtualaxe-patchcheck-$(SOURCE)
UPSTREAM_REF ?= origin/master

help:
	@printf "Human operator entrypoint: ./vaxe --source bitaxe (or --source nerdnos)\n"
	@printf "make targets below are repo/developer helpers:\n"
	@printf "virtualAxe targets:\n"
	@printf "  make validate       [ignored-state mutating] Prepare local deps and validate SOURCE (bitaxe by default)\n"
	@printf "  make validate-lite  [ignored-state mutating] Prepare Python deps and run fast network-free validation for CI\n"
	@printf "  make validate-config [tracked-source read-only] Validate tracked release configuration\n"
	@printf "  make secret-scan    [tracked-source read-only] Scan tracked files for private material\n"
	@printf "  make local-state-report [tracked-source read-only] Report ignored/generated local state\n"
	@printf "  make clean-clone-smoke [external/live, ignored-state mutating] Clone the repo into temp state and run the first-user path\n"
	@printf "  make drift-check    [ignored-state mutating] Report release drift against the configured pin\n"
	@printf "  make patch-audit    [tracked-source read-only] Report ESP-Miner patch hunk metadata\n"
	@printf "  make audit          [report-generating] Write a compact report under ignored out/audit/\n"
	@printf "  make release-evidence [report-generating] Normalize latest live gate evidence under ignored out/release-evidence/\n"
	@printf "  make sync-upstream  [external/live, ignored-state mutating] Fetch/reset the pinned upstream source cache\n"
	@printf "  make build          [ignored-state mutating] Build the selected virtual firmware/profile\n"
	@printf "  make run            [runtime mutating] Launch QEMU in NAT mode by default\n"
	@printf "  make dashboard      [runtime mutating] Launch the Bitaxe operator dashboard on the safe default port\n"
	@printf "  make e2e            [runtime mutating] Run the end-to-end QEMU/API/browser validation flow\n"
	@printf "  make verify-submit-replay [runtime mutating] Run the deterministic low-difficulty submit-boundary replay\n"
	@printf "  make verify-release [external/live] Run the gamma pool smoke gate (Bitronics + Nerdminers)\n"
	@printf "  make patch-check    [ignored-state mutating] Apply the patch stack against the configured source pin\n"
	@printf "  make patch-check-upstream [external/live, ignored-state mutating] Apply the patch stack against UPSTREAM_REF (default origin/master)\n"
	@printf "  make test-ci        [runtime mutating] Run the upstream test-ci QEMU proof\n"
	@printf "  make test-api       [runtime mutating] Run the API pytest suite\n"
	@printf "  make test-browser   [runtime mutating] Run the Playwright smoke test\n"
	@printf "  make verify-persistence  [runtime mutating] Verify saved settings survive restart and rebuild\n"
	@printf "  make doctor         [ignored-state mutating] Probe source patch health and local tooling\n"

.PHONY: help validate validate-lite validate-config secret-scan local-state-report clean-clone-smoke drift-check patch-audit audit release-evidence sync-upstream build run dashboard e2e verify-submit-replay verify-release patch-check patch-check-upstream test-ci test-api test-browser verify-persistence doctor

validate:
	./scripts/ensure-test-python.sh
	"$${VIRTUALAXE_TEST_PYTHON:-./.venv/bin/python}" ./scripts/validate.py

validate-lite:
	./scripts/ensure-test-python.sh
	"$${VIRTUALAXE_TEST_PYTHON:-./.venv/bin/python}" ./scripts/validate.py --lite

validate-config:
	python3 ./scripts/validate-config.py

secret-scan:
	python3 ./scripts/secret-scan.py

local-state-report:
	python3 ./scripts/local-state-report.py

clean-clone-smoke:
	./scripts/clean-clone-smoke.sh

drift-check:
	python3 ./scripts/drift-check.py

patch-audit:
	python3 ./scripts/patch-audit.py

audit:
	python3 ./scripts/audit-report.py

release-evidence:
	python3 ./scripts/release-evidence.py

sync-upstream:
	./scripts/sync-upstream.sh

build:
	python3 ./scripts/virtualaxe.py build --source "$(SOURCE)" --profile "$(VIRTUAL_PROFILE)"

run:
	python3 ./scripts/virtualaxe.py run --source "$(SOURCE)" --profile "$(VIRTUAL_PROFILE)"

dashboard:
	python3 ./scripts/virtualaxe.py dashboard --source "$(SOURCE)" --profile "$(VIRTUAL_PROFILE)" --http-port "$(HTTP_PORT)"

e2e:
	python3 ./scripts/virtualaxe.py verify --source "$(SOURCE)" --profile "$(VIRTUAL_PROFILE)"

verify-submit-replay:
	python3 ./scripts/virtualaxe.py verify-submit-replay --source "$(SOURCE)" --profile "$(VIRTUAL_PROFILE)"

verify-release:
	python3 ./scripts/virtualaxe.py verify-release --source "$(SOURCE)" --http-port "$(HTTP_PORT)" --mode "$(VERIFY_RELEASE_MODE)"

patch-check:
	python3 ./scripts/virtualaxe.py patch-check --source "$(SOURCE)" --target-dir "$(PATCH_TARGET_DIR)"

patch-check-upstream:
	python3 ./scripts/virtualaxe.py patch-check --source "$(SOURCE)" --upstream-ref "$(UPSTREAM_REF)" --target-dir "$${PATCH_TARGET_DIR:-/tmp/virtualaxe-patchcheck-upstream-$(SOURCE)}" --fetch

test-ci:
	python3 ./scripts/virtualaxe.py verify-test-ci --source "$(SOURCE)"

test-api:
	python3 ./scripts/virtualaxe.py verify --source "$(SOURCE)" --profile "$(VIRTUAL_PROFILE)" --api-only

test-browser:
	python3 ./scripts/virtualaxe.py verify --source "$(SOURCE)" --profile "$(VIRTUAL_PROFILE)" --browser-only

verify-persistence:
	./scripts/verify-settings-persistence.sh

doctor:
	python3 ./scripts/virtualaxe.py doctor
