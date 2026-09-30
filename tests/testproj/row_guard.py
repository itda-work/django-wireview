"""Fail a test that leaves committed rows behind (#133).

A test under ``django_db`` runs in a transaction that is rolled back at its end,
but only on the connection of the thread that opened it. An async test's ORM
calls run on a worker thread with a connection of its own, in autocommit: its
writes are committed at once and outlive the test. The next test that creates
the same row fails on a UNIQUE constraint, or one that counts rows counts
another test's -- depending on the order the tests happen to run in.

After every test's teardown, rolled back or flushed, no table may hold more rows
than before it. The first count is taken as the test database is created. A
``transaction=True`` test is flushed at its end, which only lowers the counts,
so it needs no telling apart.

The fix for a test this fails is ``django_db(transaction=True)``.
"""

from __future__ import annotations

import typing as t

import pytest

_before: dict[str, int] | None = None


def _counts() -> dict[str, int]:
    from django.db import connection

    tables = sorted(connection.introspection.django_table_names(only_existing=True, include_views=False))
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute("SELECT " + ", ".join(f"(SELECT COUNT(*) FROM {quote(name)})" for name in tables))
        return dict(zip(tables, cursor.fetchone(), strict=True))


# Hooks rather than an override of ``django_db_setup``: which of two fixtures of one
# name wins depends on the order the plugins were registered in, and the losing
# guard would say nothing at all.
@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(
    fixturedef: pytest.FixtureDef, request: pytest.FixtureRequest
) -> t.Generator[None, object, object]:
    global _before
    result = yield
    if fixturedef.argname == "django_db_setup":
        with _unblocked(request.config):
            _before = _counts()
    return result


def pytest_fixture_post_finalizer(fixturedef: pytest.FixtureDef, request: pytest.FixtureRequest) -> None:
    global _before
    # The last test's teardown takes the database down with it; nothing is left to count.
    if fixturedef.argname == "django_db_setup":
        _before = None


def _unblocked(config: pytest.Config) -> t.ContextManager[None]:
    from pytest_django.plugin import blocking_manager_key

    return config.stash[blocking_manager_key].unblock()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> t.Generator[None, None, None]:
    global _before
    result = yield
    if _before is None:
        return result
    with _unblocked(item.config):
        after = _counts()
    left = {name: after[name] - _before.get(name, 0) for name in after if after[name] > _before.get(name, 0)}
    # Whatever the verdict, the next test is measured from here: one leak is reported once.
    _before = after
    if left:
        raise AssertionError(
            f"{item.nodeid} left committed rows behind: {left}. An async test's ORM calls commit on "
            f"a worker thread's connection, outside the transaction that is rolled back; mark it "
            f"django_db(transaction=True) (#133)."
        )
    return result
