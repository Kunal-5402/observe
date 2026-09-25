.DEFAULT_GOAL := help
.PHONY: help setup test lint format format-check check show install uninstall clean

help: ## Show the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-13s %s\n", $$1, $$2}'

setup: ## Create the virtual env and install dev dependencies
	@command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
	uv sync --group dev

test: ## Run the test suite
	uv run pytest -q

lint: ## Lint Python and check the UI script
	uv run ruff check .
	node --check src/observe/ui/app.js

format: ## Format the code
	uv run ruff format .
	uv run ruff check --fix .

format-check: ## Check formatting without changes
	uv run ruff format --check .

check: lint format-check test ## Run every CI check locally

show: ## Open the UI from the dev env
	uv run observe show

install: ## Install the observe command and add hooks to detected agents
	uv tool install --force .
	observe install

uninstall: ## Remove the hooks and the observe command
	-observe uninstall
	uv tool uninstall observe

clean: ## Remove build and cache files
	rm -rf .venv .pytest_cache .ruff_cache dist build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
