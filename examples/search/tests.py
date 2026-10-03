"""What this example is for: a debounced query kept in the address -- the
results come from params_changed(), so a reload, a shared link and Back show
them too -- and a keyboard-navigable selection in component state."""

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_count, expect_text, open_live
from testproj.e2e_server import serve

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
    view = await mount(XLiveSearch, path="/search/")
    await view.call("search", q="장")
    await view.follow_push()

    assert view.component.results == []
    assert view.component.is_open is False


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_query_matches_title_or_author():
    await _seed_books()
    view = await mount(XLiveSearch, params={"q": "장고"})

    titles = [book.title for book in view.component.results]
    assert "장고 실전" in titles
    assert view.component.is_open is True
    # Rows in state are signed as pks; list[Book] failed to sign before #113
    assert "장고 실전" in view.render()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_arrow_keys_wrap_around_the_results():
    await _seed_books()
    view = await mount(XLiveSearch, params={"q": "입문"})
    count = len(view.component.results)
    assert count >= 1

    await view.call("navigate", direction=1)
    assert view.component.selected_index == 0

    await view.call("navigate", direction=-1)
    assert view.component.selected_index == count - 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_selecting_a_result_closes_the_dropdown():
    await _seed_books()
    view = await mount(XLiveSearch, params={"q": "파이썬"})
    await view.call("select_result", index=0)

    assert view.component.is_open is False
    assert view.component.selected_book is not None
    assert view.component.query == view.component.selected_book.title
    assert view.component.selected_book.title in view.render()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_link_with_a_query_opens_on_its_results():
    """A reload or a shared link: the join hears the address's query."""
    await _seed_books()
    view = await mount(XLiveSearch, params={"q": "파이썬"})

    assert view.component.query == "파이썬"
    assert [book.title for book in view.component.results] == ["파이썬 입문"]
    assert "파이썬 입문" in view.render()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_typing_puts_the_query_in_the_address():
    await _seed_books()
    view = await mount(XLiveSearch, path="/search/")
    await view.call("search", q="장고 실전")

    # The handler only moves the address; the results wait for the patch
    view.assert_pushed_to("?q=%EC%9E%A5%EA%B3%A0+%EC%8B%A4%EC%A0%84", params={"q": "장고 실전"})
    assert view.component.results == []

    await view.follow_push()
    assert view.component.wire.params == {"q": "장고 실전"}
    assert [book.title for book in view.component.results] == ["장고 실전"]
    assert view.component.is_open is True


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_empty_query_leaves_no_query_in_the_address():
    await _seed_books()
    view = await mount(XLiveSearch, params={"q": "장고"}, path="/search/")
    await view.call("search", q="  ")

    view.assert_pushed_to("/search/")
    await view.follow_push()
    assert view.component.wire.params == {}
    assert view.component.query == ""
    assert view.component.results == []


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_clear_empties_the_address_too():
    await _seed_books()
    view = await mount(XLiveSearch, params={"q": "파이썬"}, path="/search/")
    await view.call("select_result", index=0)
    await view.call("clear")

    view.assert_pushed_to("/search/")
    await view.follow_push()
    assert view.component.selected_book is None
    assert view.component.query == ""
    assert view.component.is_open is False


@pytest.fixture
def wireview_server():
    with serve() as base_url:
        yield base_url


@pytest.mark.e2e
class TestQueryInTheAddress:
    @pytest.fixture(autouse=True)
    def books(self, transactional_db):
        Book.objects.create(title="장고 실전", author="김장고")
        Book.objects.create(title="파이썬 입문", author="박파이")

    def test_reload_and_back_show_the_query_in_the_address(self, page, wireview_server):
        open_live(page, f"{wireview_server}/search/", "[data-name=XLiveSearch][data-is-live=true]")
        box = page.locator("input[name=q]")
        titles = page.locator(".dropdown .book-title")

        box.fill("장고")
        expect(page).to_have_url(re.compile(r"/search/\?q=%EC%9E%A5%EA%B3%A0$"))
        expect_text(titles, "장고 실전")

        box.fill("파이썬")
        expect(page).to_have_url(re.compile(r"/search/\?q=%ED%8C%8C%EC%9D%B4%EC%8D%AC$"))
        expect_text(titles, "파이썬 입문")

        # A reload, like a shared link, opens on the address's results
        page.reload()
        expect_text(titles, "파이썬 입문")
        expect(box).to_have_value("파이썬")

        # Back returns to the previous query: the entry the page before the
        # reload made is fetched, and its join hears that query
        page.go_back()
        expect(page).to_have_url(re.compile(r"/search/\?q=%EC%9E%A5%EA%B3%A0$"))
        expect_text(titles, "장고 실전")
        expect(box).to_have_value("장고")

        # Back to where the search started: no query, no results
        page.go_back()
        expect(page).to_have_url(re.compile(r"/search/$"))
        expect_count(titles, 0)
        expect(box).to_have_value("")

    def test_back_between_patches_restores_the_query(self, page, wireview_server):
        open_live(page, f"{wireview_server}/search/", "[data-name=XLiveSearch][data-is-live=true]")
        box = page.locator("input[name=q]")
        titles = page.locator(".dropdown .book-title")

        box.fill("장고")
        expect_text(titles, "장고 실전")
        box.fill("파이썬")
        expect_text(titles, "파이썬 입문")

        # A patch: nothing is fetched, the same instance hears the query. The
        # field shows what the server last rendered, so the answer replaces it
        page.evaluate("window.__samePage = true")
        page.go_back()
        expect(page).to_have_url(re.compile(r"/search/\?q=%EC%9E%A5%EA%B3%A0$"))
        expect_text(titles, "장고 실전")
        expect(box).to_have_value("장고")
        assert page.evaluate("window.__samePage === true"), "patched in place, not loaded"
