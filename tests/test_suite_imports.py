"""No test module is imported as ``tests.test_x``.

``tests`` has no ``__init__.py`` and ``pythonpath`` holds both the repository
root and ``tests``, so pytest imports ``tests/test_x.py`` as ``test_x`` while
``from tests.test_x import ...`` imports it a second time as ``tests.test_x``.
The components it defines then register twice under one name, and the later
class shadows the earlier: the module's own tests fail or pass depending on
which file ran first. Shared helpers live in
``tests/testproj/``; ``from test_x import ...`` names the module pytest
already loaded and is not a second copy.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
TEST_MODULE_IMPORT = re.compile(r"^\s*(?:from|import)\s+tests\.test_\w+|^\s*from\s+tests\s+import\s+test_\w+", re.M)


def test_no_test_module_is_imported_under_the_tests_package():
    offenders = [
        f"{path.relative_to(ROOT)}: {match.group(0).strip()}"
        for directory in ("tests", "examples")
        for path in sorted((ROOT / directory).rglob("*.py"))
        for match in TEST_MODULE_IMPORT.finditer(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
