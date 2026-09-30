"""Fail a test that leaves committed rows behind (#133).

A test under ``django_db`` runs in a transaction that is rolled back at its end,
but only on the connection of the thread that opened it. An async test's ORM
calls run on a worker thread with a connection of its own, in autocommit: its
writes are committed at once and outlive the test. The next test that creates
the same row fails on a UNIQUE constraint, or one that counts rows counts
another test's -- depending on the order the tests happen to run in.

After every test's teardown, rolled back or flushed, no table may hold more rows
than before it. The first count is taken as the test database is created, and
the last test is counted as the database is taken down, before it goes. A
``transaction=True`` test is flushed at its end, which only lowers the counts,
so it needs no telling apart.

It counts rows, in the ``default`` database's Django tables: an UPDATE, a leaked
row deleted again within the test, and a table no model owns are not seen.

The fix for a test this fails is ``django_db(transaction=True)``.
"""

from __future__ import annotations

import typing as t

import pytest

_before: dict[str, int] | None = None
#: The test being torn down: the last one's leak is found in the teardown of the database itself.
_current: pytest.Item | None = None


def _counts() -> dict[str, int]:
    from django.db import connection

    tables = sorted(connection.introspection.django_table_names(only_existing=True, include_views=False))
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute("SELECT " + ", ".join(f"(SELECT COUNT(*) FROM {quote(name)})" for name in tables))
        return dict(zip(tables, cursor.fetchone(), strict=True))


def _left(config: pytest.Config) -> dict[str, int]:
    """The rows added since the last count. Whatever the verdict, the next test is
    measured from here: one leak is reported once."""
    global _before
    if _before is None:
        return {}
    with _unblocked(config):
        after = _counts()
    left = {name: after[name] - _before.get(name, 0) for name in after if after[name] > _before.get(name, 0)}
    _before = after
    return left


def _failure(item: pytest.Item, left: dict[str, int]) -> AssertionError:
    return AssertionError(
        f"{item.nodeid} left committed rows behind: {left}. An async test's ORM calls commit on "
        f"a worker thread's connection, outside the transaction that is rolled back; mark it "
        f"django_db(transaction=True) (#133)."
    )


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
        # The last test's teardown takes the database down, after its own fixtures have
        # rolled back or flushed. A finalizer added now runs before the database's own:
        # the last count, laid at the door of the test being torn down. Without it the
        # last test of a run -- the only one, when one test is run -- would never be seen.
        fixturedef.addfinalizer(lambda: _check_last(request.config))
    return result


def _check_last(config: pytest.Config) -> None:
    left = _left(config)
    if left and _current is not None:
        raise _failure(_current, left)


def pytest_fixture_post_finalizer(fixturedef: pytest.FixtureDef, request: pytest.FixtureRequest) -> None:
    global _before
    # The database is gone; nothing is left to count.
    if fixturedef.argname == "django_db_setup":
        _before = None


def _unblocked(config: pytest.Config) -> t.ContextManager[None]:
    from pytest_django.plugin import blocking_manager_key

    return config.stash[blocking_manager_key].unblock()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> t.Generator[None, None, None]:
    global _current
    _current = item
    try:
        result = yield
    except BaseException as exc:
        # The count is taken all the same, or the next test would carry this one's leak.
        # The teardown's own error stays the one raised.
        if left := _left(item.config):
            exc.add_note(str(_failure(item, left)))
        raise
    finally:
        _current = None
    if left := _left(item.config):
        raise _failure(item, left)
    return result
