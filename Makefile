.PHONY: all install test test-cov lint check build watch-js run shell clean

all: install build

# uv 기반 설치
install:
	uv venv
	uv pip install -e ".[dev]"

# pytest 테스트
test:
	cd tests && pytest

test-cov:
	cd tests && pytest --cov=wireview --cov-report=term-missing

# 린트 및 타입 체크
lint:
	ruff check wireview tests
	djlint --check .

check:
	pyright wireview

# 빌드
build:
	node esbuild.conf.js
	uv build

# JavaScript
watch-js:
	node esbuild.conf.js -w

# 개발 서버
run:
	cd tests && python manage.py runserver

shell:
	cd tests && python manage.py shell

# 정리
clean:
	rm -rf dist build *.egg-info .pytest_cache .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
