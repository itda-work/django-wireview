"""What this example is for: a debounced query that keeps its result list and a
keyboard-navigable selection in component state."""

import pytest

from wireview import mount

from .live import XLiveSearch
from .models import Book


async def _seed_books():
    """An async fixture would need pytest_asyncio; a helper keeps the example small."""
    await Book.objects.acreate(title="장고 실전", author="김장고")
    await Book.objects.acreate(title="파이썬 입문", author="박파이")


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_short_query_searches_nothing():
    view = await mount(XLiveSearch)
    await view.call("search", q="장")

    assert view.component.results == []
    assert view.component.is_open is False


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_query_matches_title_or_author():
    await _seed_books()
    view = await mount(XLiveSearch)
    await view.call("search", q="장고")

    titles = [book.title for book in view.component.results]
    assert "장고 실전" in titles
    assert view.component.is_open is True


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_arrow_keys_wrap_around_the_results():
    await _seed_books()
    view = await mount(XLiveSearch)
    await view.call("search", q="입문")
    count = len(view.component.results)
    assert count >= 1

    await view.call("navigate", direction=1)
    assert view.component.selected_index == 0

    await view.call("navigate", direction=-1)
    assert view.component.selected_index == count - 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_selecting_a_result_closes_the_dropdown():
    await _seed_books()
    view = await mount(XLiveSearch)
    await view.call("search", q="파이썬")
    await view.call("select_result", index=0)

    assert view.component.is_open is False
    assert view.component.selected_book is not None
    assert view.component.query == view.component.selected_book.title
