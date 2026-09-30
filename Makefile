.PHONY: all install test test-unit test-e2e test-concurrent test-matrix test-latest test-cov test-js bench bench-compare lint format check check-js quality build watch-js run shell clean collectstatic playwright-install build-js ci-smoke

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

# Run all tests (excluding E2E and slow). --ff lives on the targets, not in addopts:
# there it made `pytest -p no:cacheprovider` refuse to start (#125).
test: collectstatic
	uv run pytest tests examples -m "not e2e and not slow" --ff -v $(ARGS)

# Run unit tests only
test-unit: collectstatic
	uv run pytest tests examples -m "unit" --ff -v $(ARGS)

# Two copies of the `make test` suite at once, in this one checkout -- what several agents
# verifying the same copy do. Each run has its own test database, so both must pass; with a
# shared one they failed each other with "readonly database" and "no such table" (#125).
# No cache provider: two runs writing .pytest_cache at once is its own race.
test-concurrent: collectstatic
	@set -e; \
	uv run pytest tests examples -m "not e2e and not slow" -q --no-header -p no:cacheprovider $(ARGS) > .test-concurrent-1.log 2>&1 & first=$$!; \
	uv run pytest tests examples -m "not e2e and not slow" -q --no-header -p no:cacheprovider $(ARGS) > .test-concurrent-2.log 2>&1 & second=$$!; \
	status=0; wait $$first || status=1; wait $$second || status=1; \
	tail -n 1 .test-concurrent-1.log .test-concurrent-2.log; \
	if [ $$status -ne 0 ]; then echo "a concurrent run failed; the logs are .test-concurrent-{1,2}.log"; exit 1; fi; \
	rm -f .test-concurrent-1.log .test-concurrent-2.log

# Run E2E tests with Playwright on the NATS channel layer (the layer this project targets).
# tests/e2e.sh starts a throwaway nats-server unless one is already running, and stops it
# afterwards. Override the layer with LAYER=redis or LAYER=memory.
LAYER ?= nats
# build-js first: wireview.min.js is gitignored, so after a pull the browser would
# otherwise run the bundle from before it and fail on whatever the pull added.
test-e2e: build-js collectstatic playwright-install
	WIREVIEW_TEST_LAYER=$(LAYER) ./tests/e2e.sh --ff $(ARGS)

# Run all tests including E2E (runs separately to avoid async conflicts)
test-all: test test-e2e

# Run tests with coverage
test-cov: collectstatic
	uv run pytest tests examples -m "not e2e" --ff --cov=wireview --cov-report=term-missing --cov-report=html $(ARGS)

# =============================================================================
# Code Quality
# =============================================================================

# Lint Python code and templates (same checks as CI's lint job)
lint:
	uv run ruff check wireview tests bench examples conftest.py
	uv run ruff format --check wireview tests bench examples conftest.py
	uv run djlint --check .

# Format code with ruff
format:
	uv run ruff check --fix wireview tests bench examples conftest.py
	uv run ruff format wireview tests bench examples conftest.py
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

# Unit and integration tests on every supported Python x Django pair (docs/COMPATIBILITY.md).
# CI only runs when dispatched by hand, so this is how the range gets checked.
MATRIX_PYTHON ?= 3.12 3.13 3.14
MATRIX_DJANGO ?= 5.2 6.0 6.1
test-matrix: collectstatic
	@set -e; for py in $(MATRIX_PYTHON); do for dj in $(MATRIX_DJANGO); do \
		echo "== Python $$py, Django $$dj"; \
		uv run --isolated --python $$py --extra dev --with "django~=$$dj.0" \
			pytest tests examples -m "not e2e and not slow" -q --no-header -p no:warnings $(ARGS); \
	done; done

# The dependencies a fresh `pip install django-wireview` gets today, not uv.lock's:
# the newest release of each within pyproject.toml's bounds. uv.lock held pydantic
# at 2.12 while new installs got 2.13, which broke the import (#127).
# collectstatic runs in that environment too, not through the `collectstatic` target:
# that one uses the project venv, and CI's job never installs the dev extras into it,
# so testproj's INSTALLED_APPS (daphne) did not import there (#136).
LATEST_RUN = uv run --no-project --isolated --python $(or $(PYTHON),3.12) --with-editable ".[dev]"
test-latest:
	$(LATEST_RUN) python tests/manage.py collectstatic --noinput
	$(LATEST_RUN) pytest tests examples -m "not e2e and not slow" -q --no-header -p no:warnings $(ARGS)

# CI: Run tests (non-E2E). --no-sync: ci.yml installs one Django over the lock's, and a
# syncing `uv run` put the lock's back before the first test -- every lane of the grid ran
# Django 6.0 whatever its name said. DJANGO=<x.y> fails the run unless that is what imports.
ci-test:
	@if [ -n "$(DJANGO)" ]; then uv run --no-sync python -c "import sys, django; \
	v = '.'.join(map(str, django.VERSION[:2])); print('ci-test: Django', django.get_version()); \
	sys.exit(0) if v == '$(DJANGO)' else sys.exit(f'ci-test: expected Django $(DJANGO), got {v}')"; fi
	cd tests && uv run --no-sync python manage.py collectstatic --noinput
	uv run --no-sync pytest tests examples -m "not e2e and not slow" -q

# CI: Run E2E tests
# CI runs E2E on NATS, the layer this project targets. ci.yml provides the server as a
# service container, so this does not start one (tests/e2e.sh would, locally).
ci-test-e2e:
	cd tests && uv run python manage.py collectstatic --noinput
	uv run playwright install --with-deps chromium
	WIREVIEW_TEST_LAYER=nats uv run pytest tests examples -m "e2e" -v

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
	@# Without py.typed a type checker ignores the package's annotations (#98).
	@python3 -c "import glob, sys, zipfile; \
	w = sorted(glob.glob('dist/*.whl'))[-1]; \
	sys.exit(0) if 'wireview/py.typed' in zipfile.ZipFile(w).namelist() \
	else sys.exit(f'{w} has no wireview/py.typed')"
	@echo "ci-build: wheel contains wireview.min.js"
	@# And the agent skill, which `manage.py wireview_agent_setup` installs from the
	@# package. It is force-included from a directory the sdist has to carry, and the
	@# wheel is built from the sdist -- a mismatch there fails the build rather than
	@# shipping quietly, but only if something looks.
	@python3 -c "import glob, sys, zipfile; \
	w = sorted(glob.glob('dist/*.whl'))[-1]; \
	names = zipfile.ZipFile(w).namelist(); \
	sys.exit(0) if any(n.endswith('agent_skills/wireview/SKILL.md') for n in names) \
	else sys.exit(f'{w} has no agent_skills/wireview/SKILL.md - check the sdist include patterns')"
	@echo "ci-build: wheel contains the agent skill"

# The built wheel in a fresh environment that resolves its dependencies anew, as a
# user's install does -- not uv.lock's versions. rc3 passed every test on the lock
# and did not import on the newest pydantic (#127). release.yml runs this on the
# artifact it is about to publish (#122). Run ci-build first.
ci-smoke:
	@set -e; for w in dist/*.whl; do \
		uv run --no-project --isolated --python $(or $(PYTHON),3.12) --with "$$w" python tests/wheel_smoke.py; \
	done

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
	@echo "  make test-concurrent  - Run the test suite twice at once, as agents sharing a checkout do"
	@echo "  make test-matrix      - Run tests on every supported Python x Django pair"
	@echo "  make test-latest      - Run tests on the newest dependencies, ignoring uv.lock"
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
	@echo "  make ci-smoke         - CI: Import the built wheel on fresh dependencies"
