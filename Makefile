.PHONY: ext-install ext-check ext-test ext-build ext-package ext-test-host all install test test-unit test-e2e test-concurrent test-matrix test-latest test-lowest test-cov test-js bench bench-compare lint format check check-js quality build watch-js run shell clean collectstatic playwright-install build-js ci-smoke docs-site docs-serve

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
# afterwards. Override the layer with LAYER=redis or LAYER=memory. ARGS may name paths
# (ARGS=tests/test_streams_e2e.py), which then replace the default tests/ and examples/.
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
	uv run ruff check wireview tests bench examples scripts conftest.py
	uv run ruff format --check wireview tests bench examples scripts conftest.py
	uv run djlint --check .

# Format code with ruff
format:
	uv run ruff check --fix wireview tests bench examples scripts conftest.py
	uv run ruff format wireview tests bench examples scripts conftest.py
	uv run djlint --reformat .

# Type check with pyright
check:
	uv run pyright wireview scripts

# Type check JavaScript
check-js:
	npm run typecheck

# Unit-test the pure client modules (node --test)
test-js:
	npm test

# Run all quality checks
quality: lint check check-js test-js

# =============================================================================
# VS Code extension (editors/vscode)
# =============================================================================
# Not part of quality or test: they need editors/vscode/node_modules, which the
# library's own workflow does not install. tests/test_vscode_extension.py runs in
# `make test` and needs node alone.

EXT = editors/vscode

ext-install:
	cd $(EXT) && npm ci

ext-check:
	cd $(EXT) && npm run typecheck

ext-test:
	cd $(EXT) && npm test

ext-build:
	cd $(EXT) && npm run build

# The .vsix, in editors/vscode/dist/
ext-package:
	cd $(EXT) && npm run package

# Smoke tests in VS Code itself (the release engines.vscode names; downloaded once into .vscode-test)
ext-test-host:
	cd $(EXT) && npm run test:host

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
	uv run pyright wireview scripts

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
# The docs dependency group as --with arguments: outside the project uv installs no groups, and
# tests/test_docs_site_build.py needs them (#159). Names only, so the newest release comes.
DOCS_WITH = $(shell uv run --no-project --python 3.12 python scripts/docs_site/nav.py)
LATEST_RUN = uv run --no-project --isolated --python $(or $(PYTHON),3.12) --with-editable ".[dev]" $(DOCS_WITH)
test-latest:
	$(LATEST_RUN) python tests/manage.py collectstatic --noinput
	$(LATEST_RUN) pytest tests examples -m "not e2e and not slow" -q --no-header -p no:warnings $(ARGS)

# The other end of pyproject.toml's bounds: the oldest release of each runtime dependency
# it allows, which nothing else installs (#132). `uv pip compile --resolution lowest-direct
# --no-deps` reads the floors from pyproject.toml, so raising one moves this run with it.
# Only the runtime dependencies go to their floors -- the promise is to users. The dev
# extras have no lower bounds (lowest-direct on them picked ipython 0.10), so they resolve
# to the newest release that fits the pinned floors. On Python 3.12, the oldest supported:
# a floor with no wheel there is a floor nobody can install. The warnings plugin stays on,
# as in `make test`: with it off, a warning the floors raise at every import went unseen.
LOWEST_PYTHON = 3.12
test-lowest:
	@set -ef; \
	floors=$$(uv pip compile pyproject.toml --resolution lowest-direct --no-deps --python-version $(LOWEST_PYTHON) \
		--quiet --no-header --no-annotate); \
	echo "test-lowest:" $$floors; \
	run="uv run --no-project --isolated --python $(LOWEST_PYTHON) --with-editable .[dev] $(DOCS_WITH) $$(printf -- '--with %s ' $$floors)"; \
	$$run python tests/manage.py collectstatic --noinput; \
	$$run pytest tests examples -m "not e2e and not slow" -q --no-header $(ARGS)

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
# On LAYER (nats by default, the layer this project targets); ci.yml runs it once per
# layer docs/COMPATIBILITY.md supports (#130). ci.yml provides the brokers as service
# containers, so this does not start one (tests/e2e.sh would, locally).
ci-test-e2e:
	cd tests && uv run python manage.py collectstatic --noinput
	uv run playwright install --with-deps chromium
	WIREVIEW_TEST_LAYER=$(LAYER) uv run pytest tests examples -m "e2e" -v

# CI: Build and check package
ci-build:
	npm ci
	npm run build
	@# --clear: ci-smoke runs every wheel in dist/, and an older one left there fails it
	uv build --clear
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
	@# package. hatch_build.py copies it from a directory the sdist has to carry, and the
	@# wheel is built from the sdist -- a mismatch there fails the build rather than
	@# shipping quietly, but only if something looks.
	@python3 -c "import glob, sys, zipfile; \
	w = sorted(glob.glob('dist/*.whl'))[-1]; \
	names = zipfile.ZipFile(w).namelist(); \
	sys.exit(0) if any(n.endswith('agent_skills/wireview/SKILL.md') for n in names) \
	else sys.exit(f'{w} has no agent_skills/wireview/SKILL.md - check the sdist include patterns')"
	@echo "ci-build: wheel contains the agent skill"
	@# And the starter template tutorial 01 points `startproject --template` at (#131):
	@# data files with no Python module among them, so nothing imports them to notice.
	@python3 -c "import glob, sys, zipfile; \
	w = sorted(glob.glob('dist/*.whl'))[-1]; \
	names = set(zipfile.ZipFile(w).namelist()); \
	want = {'wireview/project_template/' + n for n in ('manage.py-tpl', 'project_name/settings.py-tpl', \
	'project_name/asgi.py-tpl', 'project_name/urls.py-tpl', 'hello/live.py-tpl', 'hello/templates/hello/index.html', '.gitignore')}; \
	sys.exit(0) if want <= names else sys.exit(f'{w} lacks {sorted(want - names)}')"
	@echo "ci-build: wheel contains the starter template"
	@# The PyPI page is this metadata. hatch_build.py pins the README's links to the
	@# release tag; the wheel is built from the sdist, which has to carry the hook.
	@python3 -c "import glob, sys, zipfile; \
	w = sorted(glob.glob('dist/*.whl'))[-1]; z = zipfile.ZipFile(w); \
	meta = z.read(next(n for n in z.namelist() if n.endswith('.dist-info/METADATA'))).decode(); \
	version = next(l.split(': ', 1)[1] for l in meta.splitlines() if l.startswith('Version: ')); \
	bad = '/main/' in meta or meta.count(f'/v{version}/') < 30; \
	sys.exit(f'{w}: the description does not link to v{version}') if bad else None"
	@echo "ci-build: the description links to the release tag"
	@# So does the skill wireview_agent_setup installs: it describes this release's API.
	@python3 -c "import glob, sys, zipfile; \
	w = sorted(glob.glob('dist/*.whl'))[-1]; z = zipfile.ZipFile(w); \
	skill = {n: z.read(n).decode() for n in z.namelist() if '/agent_skills/' in n and n.endswith('.md')}; \
	version = w.split('-')[1]; \
	bad = [n for n, text in skill.items() if '/main/' in text]; \
	sys.exit(f'{w}: {bad} link to main') if bad or f'/v{version}/' not in skill['wireview/agent_skills/wireview/SKILL.md'] else None"
	@echo "ci-build: the agent skill links to the release tag"

# The built wheel in a fresh environment that resolves its dependencies anew, as a
# user's install does -- not uv.lock's versions. rc3 passed every test on the lock
# and did not import on the newest pydantic (#127). release.yml runs this on the
# artifact it is about to publish (#122). Run ci-build first.
ci-smoke:
	@set -e; for w in dist/*.whl; do \
		uv run --no-project --isolated --python $(or $(PYTHON),3.12) --with "$$w" python tests/wheel_smoke.py; \
	done

# =============================================================================
# Documentation site (itda.work/wireview/, #159)
# =============================================================================

# The document guards are the site build's first gate: a broken link or an unclassified page
# fails here before anything is rendered. The build then checks what it wrote (internal links
# and anchors, docs/site-urls.txt) and exits non-zero on any problem. Output: build/docs-site,
# served as is by `python -m http.server -d build/docs-site` at /wireview/.
DOCS_GUARDS = tests/test_doc_links.py tests/test_doc_examples.py tests/test_tutorials.py tests/test_doc_site.py tests/test_agent_docs.py
docs-site:
	uv run pytest $(DOCS_GUARDS) -q --no-header -p no:cacheprovider
	uv run python -m scripts.docs_site build $(ARGS)

# Build, serve on http://127.0.0.1:8765/wireview/ and rebuild on every change to docs/, README.md
# or the layout. ARGS="--port 9000" to move it. Skips the document guards on rebuilds.
docs-serve:
	uv run python -m scripts.docs_site serve $(ARGS)

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
	@echo "  make test-lowest      - Run tests on the oldest dependencies pyproject.toml allows"
	@echo "  make test-all         - Run all tests including E2E"
	@echo "  make test-cov         - Run tests with coverage report"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint             - Run linters (ruff, djlint)"
	@echo "  make format           - Auto-format code"
	@echo "  make check            - Run type checker (pyright)"
	@echo "  make quality          - Run all quality checks"
	@echo ""
	@echo "VS Code extension (editors/vscode):"
	@echo "  make ext-install      - Install its npm dependencies"
	@echo "  make ext-check        - Type-check it"
	@echo "  make ext-test         - Run its unit and grammar tests"
	@echo "  make ext-build        - Bundle it"
	@echo "  make ext-package      - Build the .vsix"
	@echo "  make ext-test-host    - Run its smoke tests in VS Code"
	@echo ""
	@echo "Build:"
	@echo "  make build            - Build JS and Python package"
	@echo "  make build-js         - Build JavaScript bundle only"
	@echo "  make build-py         - Build Python package only"
	@echo "  make watch-js         - Watch JS for changes"
	@echo ""
	@echo "Documentation site:"
	@echo "  make docs-site        - Build the documentation site into build/docs-site, with its gates"
	@echo "  make docs-serve       - Serve the site and rebuild it on every change (ARGS=\"--port N\")"
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
	@echo "  make ci-test-e2e      - CI: Run E2E tests (LAYER=nats|redis)"
	@echo "  make ci-build         - CI: Build package"
	@echo "  make ci-smoke         - CI: Import the built wheel on fresh dependencies"
