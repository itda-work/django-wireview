.PHONY: all install test test-unit test-e2e test-cov test-js bench bench-compare lint format check check-js quality build watch-js run shell clean collectstatic playwright-install

# Default target
all: install build

# =============================================================================
# Installation
# =============================================================================

# Install dependencies using uv
install:
	uv sync --all-extras

# Install Playwright browsers
playwright-install:
	uv run playwright install chromium

# =============================================================================
# Testing
# =============================================================================

# Collect static files (required for tests)
collectstatic:
	cd tests && uv run python manage.py collectstatic --noinput

# Run all tests (excluding E2E and slow)
test: collectstatic
	DJANGO_ALLOW_ASYNC_UNSAFE=1 uv run pytest tests examples -m "not e2e and not slow" -v $(ARGS)

# Run unit tests only
test-unit: collectstatic
	uv run pytest tests examples -m "unit" -v $(ARGS)

# Run E2E tests with Playwright on the NATS channel layer (the layer this project targets).
# tests/e2e.sh starts a throwaway nats-server unless one is already running, and stops it
# afterwards. Override the layer with LAYER=redis or LAYER=memory.
LAYER ?= nats
test-e2e: collectstatic playwright-install
	WIREVIEW_TEST_LAYER=$(LAYER) ./tests/e2e.sh $(ARGS)

# Run all tests including E2E (runs separately to avoid async conflicts)
test-all: test test-e2e

# Run tests with coverage
test-cov: collectstatic
	DJANGO_ALLOW_ASYNC_UNSAFE=1 uv run pytest tests examples -m "not e2e" --cov=wireview --cov-report=term-missing --cov-report=html $(ARGS)

# =============================================================================
# Code Quality
# =============================================================================

# Lint Python code and templates (same checks as CI's lint job)
lint:
	uv run ruff check wireview tests bench
	uv run ruff format --check wireview tests bench
	uv run djlint --check .

# Format code with ruff
format:
	uv run ruff check --fix wireview tests bench
	uv run ruff format wireview tests bench
	uv run djlint --reformat .

# Type check with pyright
check:
	uv run pyright wireview

# Type check JavaScript
check-js:
	npm run typecheck

# Unit-test the pure client modules (node --test)
test-js:
	npm test

# Run all quality checks
quality: lint check check-js test-js

# =============================================================================
# Build
# =============================================================================

# Build JavaScript bundle
build-js:
	npm run build

# Build Python package
build-py:
	uv build

# Build everything
build: build-js build-py

# Watch JavaScript for changes
watch-js:
	npm run watch

# =============================================================================
# Development Server
# =============================================================================

# Run development server
run:
	cd tests && uv run python manage.py runserver

# Run with daphne (WebSocket support)
run-daphne:
	cd tests && uv run daphne testproj.asgi:application

# Django shell
shell:
	cd tests && uv run python manage.py shell

# Django migrations
migrate:
	cd tests && uv run python manage.py migrate

# =============================================================================
# Benchmarks (see bench/README.md)
# =============================================================================

# Payload sizes, per-event cost, WebSocket memory/throughput. ARGS="--skip-ws" for in-process only
bench:
	uv run python -m bench.run $(ARGS)

# Benchmark a past commit next to the current tree: make bench-compare BASE=997ee59
bench-compare:
	./bench/compare.sh $(BASE) $(ARGS)

# =============================================================================
# Cleanup
# =============================================================================

# Clean build artifacts
clean:
	rm -rf dist build *.egg-info .pytest_cache .coverage htmlcov .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

# Clean test databases
clean-db:
	rm -f tests/db.sqlite3 tests/db_test.sqlite3

# Clean everything
clean-all: clean clean-db

# =============================================================================
# CI Targets (used by GitHub Actions)
# =============================================================================

# CI: Install dependencies
ci-install:
	uv sync --all-extras

# CI: Run linting (keep in sync with `lint`)
ci-lint: lint

# CI: Run type checking
ci-check:
	uv run pyright wireview

# CI: Run tests (non-E2E)
ci-test:
	cd tests && uv run python manage.py collectstatic --noinput
	DJANGO_ALLOW_ASYNC_UNSAFE=1 uv run pytest tests examples -m "not e2e and not slow" -q

# CI: Run E2E tests
# CI runs E2E on NATS, the layer this project targets. ci.yml provides the server as a
# service container, so this does not start one (tests/e2e.sh would, locally).
ci-test-e2e:
	cd tests && uv run python manage.py collectstatic --noinput
	uv run playwright install --with-deps chromium
	WIREVIEW_TEST_LAYER=nats DJANGO_ALLOW_ASYNC_UNSAFE=1 uv run pytest tests examples -m "e2e" -v

# CI: Build and check package
ci-build:
	npm ci
	npm run build
	uv build
	uvx twine check dist/*
	@# The wheel is useless without the built JS: {% wireview_header %} loads it by name.
	@python3 -c "import glob, sys, zipfile; \
	w = sorted(glob.glob('dist/*.whl'))[-1]; \
	names = zipfile.ZipFile(w).namelist(); \
	sys.exit(0) if any(n.endswith('wireview.min.js') for n in names) \
	else sys.exit(f'{w} has no wireview.min.js - run npm run build before uv build')"
	@echo "ci-build: wheel contains wireview.min.js"

# =============================================================================
# Help
# =============================================================================

help:
	@echo "django-wireview Makefile"
	@echo ""
	@echo "Installation:"
	@echo "  make install          - Install all dependencies"
	@echo "  make playwright-install - Install Playwright browsers"
	@echo ""
	@echo "Testing:"
	@echo "  make test             - Run tests (excluding E2E and slow)"
	@echo "  make test-unit        - Run unit tests only"
	@echo "  make test-e2e         - Run E2E tests with Playwright"
	@echo "  make test-all         - Run all tests including E2E"
	@echo "  make test-cov         - Run tests with coverage report"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint             - Run linters (ruff, djlint)"
	@echo "  make format           - Auto-format code"
	@echo "  make check            - Run type checker (pyright)"
	@echo "  make quality          - Run all quality checks"
	@echo ""
	@echo "Build:"
	@echo "  make build            - Build JS and Python package"
	@echo "  make build-js         - Build JavaScript bundle only"
	@echo "  make build-py         - Build Python package only"
	@echo "  make watch-js         - Watch JS for changes"
	@echo ""
	@echo "Development:"
	@echo "  make run              - Run dev server"
	@echo "  make run-daphne       - Run with daphne (WebSocket)"
	@echo "  make shell            - Django shell"
	@echo "  make migrate          - Run migrations"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean            - Clean build artifacts"
	@echo "  make clean-all        - Clean everything"
	@echo ""
	@echo "CI (used by GitHub Actions):"
	@echo "  make ci-install       - CI: Install dependencies"
	@echo "  make ci-lint          - CI: Run linting"
	@echo "  make ci-check         - CI: Run type checking"
	@echo "  make ci-test          - CI: Run tests (non-E2E)"
	@echo "  make ci-test-e2e      - CI: Run E2E tests"
	@echo "  make ci-build         - CI: Build package"
