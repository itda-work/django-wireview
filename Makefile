.PHONY: all install test test-cov lint check build watch-js run shell clean collectstatic

all: install build

# uv 기반 설치
install:
	uv venv
	uv pip install -e ".[dev]"

# collectstatic
collectstatic:
	cd tests && uv run python manage.py collectstatic --noinput

# pytest 테스트 (위치 인자 지원: make test tests/test_security.py)
TEST_TARGET := $(filter-out test test-cov,$(MAKECMDGOALS))

test: collectstatic
	uv run pytest $(if $(TEST_TARGET),$(TEST_TARGET),tests/) -v

test-cov: collectstatic
	uv run pytest $(if $(TEST_TARGET),$(TEST_TARGET),tests/) --cov=wireview --cov-report=term-missing

# 위치 인자를 타겟으로 인식하지 않도록 처리
%:
	@:

# 린트 및 타입 체크
lint:
	uv run ruff check wireview tests
	uv run djlint --check .

check:
	uv run pyright wireview

# 빌드
build:
	node esbuild.conf.js
	uv build

# JavaScript
watch-js:
	node esbuild.conf.js -w

# 개발 서버
run:
	cd tests && uv run python manage.py runserver

shell:
	cd tests && uv run python manage.py shell

# 정리
clean:
	rm -rf dist build *.egg-info .pytest_cache .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
