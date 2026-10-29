set shell := ["bash", "-c"]

# Help
@default:
    just --list --unsorted

# Install every workspace package and all dev dependencies
@install:
    uv sync --all-packages --all-groups

# Update dependencies
@update:
    uv lock --upgrade
    uv sync --all-packages --all-groups

# Lint with Ruff
@lint:
    uv run ruff check --config .pytools/ruff.toml src packages tests scripts

@lock-check:
    uv lock --check

# Check formatting without modifying files
@format-check:
    uv run ruff format --check --config .pytools/ruff.toml src packages tests scripts

# Format with Ruff
@format:
    uv run ruff format --config .pytools/ruff.toml src packages tests scripts
    uv run ruff check --config .pytools/ruff.toml --fix src packages tests scripts

# Type check with ty
@typecheck:
    uv run ty check --config-file .pytools/ty.toml src packages

# Unit tests (no external services)
@test:
    uv run pytest -c .pytools/pytest.ini --rootdir . tests -m unit -v

# Integration tests (start the services in docker-compose.yml)
@test-integration:
    uv run pytest -c .pytools/pytest.ini --rootdir . tests -m integration -v

# Unit tests with coverage
@test-cov:
    uv run pytest -c .pytools/pytest.ini --rootdir . tests -m unit --cov=src --cov=packages --cov-config=.pytools/coverage.toml --cov-report=term-missing --cov-report=xml

# Check that all distributions share one version and pin each other consistently
@check-versions:
    python scripts/check_versions.py

# Build every distribution (umbrella + one per backend) into dist/
@build:
    rm -rf dist
    uv build --all-packages --out-dir dist

# Install each built wheel family in a throw-away venv and check the extras
@smoke: build
    uv run scripts/smoke_install.py dist

# Audit installed dependencies
@audit:
    uv run pip-audit

# Create a guided Conventional Commit, the file must be staged before running this command
@commit:
    uv run cz --config .pytools/cz.toml commit

# Bump the version of every distribution in lockstep
@bump:
    uv run cz --config .pytools/cz.toml bump

# Local quality gate
@code-quality: lint format-check typecheck lock-check check-versions

# Full CI gate
@ci: code-quality test-cov
