# sempipe developer tasks. `make gates` is what CI runs and what a PR must pass.
# Every recipe runs un-piped so a failing step fails the whole target (piping to
# `tail` silently swallows non-zero exit codes — don't reintroduce it).

.DEFAULT_GOAL := help
.PHONY: help install test cov lint fmt fmt-check types gates smoke golden startup clean

help: ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Sync the dev environment (all extras)
	uv sync --all-extras

test: ## Run the test suite
	uv run pytest -q

cov: ## Run tests with coverage report
	uv run coverage run -m pytest -q
	uv run coverage report

lint: ## Lint with ruff
	uv run ruff check

fmt: ## Format with ruff
	uv run ruff format

fmt-check: ## Check formatting without changing files
	uv run ruff format --check

types: ## Type-check with pyright (strict)
	uv run pyright

gates: lint fmt-check types cov ## The full PR gate: lint + format + types + coverage
	@echo "✓ all gates green"

golden: ## Refresh golden files (review the diff before committing)
	UPDATE_GOLDEN=1 uv run pytest -q

startup: ## Time `--help` startup (advisory; the deterministic gate is tests/test_startup_imports.py)
	uv run hyperfine --warmup 3 'python -m sempipe --help' || \
	  uv run python -c "import subprocess, sys, time; t = [__import__('timeit').timeit(lambda: subprocess.run([sys.executable, '-m', 'sempipe', '--help'], capture_output=True, check=True), number=1) for _ in range(8)]; t.sort(); print(f'median --help wall clock: {t[len(t)//2]*1000:.0f} ms (hyperfine not installed; rough fallback)')"

smoke: ## Build the wheel and run it from a clean environment
	rm -rf dist
	uv build
	uvx --from ./dist/*.whl sempipe --version
	@printf 'a\n{ "b" :1}\n' | uvx --from ./dist/*.whl sempipe echo

clean: ## Remove build and cache artifacts
	rm -rf dist build .ruff_cache .pytest_cache .coverage htmlcov src/*.egg-info
