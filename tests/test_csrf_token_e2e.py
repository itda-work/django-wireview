"""A plain POST form in a component posts with its ``{% csrf_token %}`` (#206, docs/features/dead-view.md).

A component's template renders with its own context, not the page's
``RequestContext``, so the token was empty -- on the first HTTP render and on
every live render -- and the form got a 403 from Django's CSRF check. Each test
here submits a form a different render drew to a view that checks the token as
any view does.

Fixture: tests/testproj/csrfprobe/.
"""

import pytest
from testproj.csrfprobe import live, views
from testproj.e2e_browser import expect_count, expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

POST_URL = "/csrfprobe/post/"


@pytest.fixture(scope="function")
def server():
    live.NOTES.clear()
    views.POSTED.clear()
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def by(page, testid: str):
    return page.get_by_test_id(testid)


def submit(page, name: str) -> int:
    """Submit the form ``name`` and return the status the view answered with."""
    with page.expect_response(lambda response: response.url.endswith(POST_URL)) as answered:
        by(page, f"{name}-submit").click()
    return answered.value.status


def token_of(page, name: str) -> str:
    return by(page, f"{name}-form").locator("input[name=csrfmiddlewaretoken]").input_value()


def posts(page, name: str, text: str | None = None) -> None:
    assert submit(page, name) == 200
    expect_text(by(page, "posted"), f"posted {text or name}")


@pytest.mark.parametrize("name", ["own", "joined", "child"])
def test_without_javascript_the_first_render_posts(browser, server, name):
    """The HTTP render's token: the page's own, from the request."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    page.goto(f"{server}/csrfprobe/")
    posts(page, name)
    context.close()


@pytest.mark.parametrize("name", ["own", "joined", "child"])
def test_a_form_the_join_drew_posts(page, server, name):
    """The join renders the component again, and its LiveComponent, from the connection's secret."""
    open_live(page, f"{server}/csrfprobe/")
    assert len(token_of(page, name)) == 64
    posts(page, name)
    assert views.POSTED == [(name, "")]


def test_a_form_an_event_drew_posts(page, server):
    """Drawn only by the render after the event: there was no token on the page for it before."""
    open_live(page, f"{server}/csrfprobe/")
    expect_count(by(page, "event-form"), 0)
    by(page, "show").click()
    expect_count(by(page, "event-form"), 1)
    posts(page, "event")


def test_a_form_a_notification_drew_posts(browser, page, server):
    """Drawn by the render a broadcast asked for, published from another viewer's page."""
    open_live(page, f"{server}/csrfprobe/")
    other = browser.new_context().new_page()
    open_live(other, f"{server}/csrfprobe/")
    by(other, "publish").click()
    expect_count(by(page, "noted-form"), 1)
    posts(page, "noted")


def test_with_the_secret_in_the_session(page, server, settings):
    """``CSRF_USE_SESSIONS``: no cookie, the secret is in the session the socket read."""
    settings.CSRF_USE_SESSIONS = True
    open_live(page, f"{server}/csrfprobe/")
    assert not [c for c in page.context.cookies() if c["name"] == "csrftoken"]
    by(page, "show").click()
    expect_count(by(page, "event-form"), 1)
    posts(page, "event")


def test_after_a_login_rotates_the_secret(browser, server):
    """A new page and its connection take the new secret; a page open from before is refused, as Django's is."""
    context = browser.new_context()
    before, django_before = context.new_page(), context.new_page()
    open_live(before, f"{server}/csrfprobe/")
    open_live(django_before, f"{server}/csrfprobe/")
    stale_own = token_of(before, "own")

    after = context.new_page()
    after.goto(f"{server}/csrfprobe/sign-in/ada/")  # login() calls rotate_token()
    open_live(after, f"{server}/csrfprobe/")
    assert token_of(after, "own") != stale_own
    by(after, "show").click()
    expect_count(by(after, "event-form"), 1)
    posts(after, "event")
    assert views.POSTED == [("event", "ada")]

    # The pages from before still hold the old secret's tokens: the component's form, as its
    # connection read the cookie at connect, and the page's own Django form alike
    assert submit(before, "own") == 403
    assert submit(django_before, "page") == 403
    open_live(before, f"{server}/csrfprobe/")  # a new page, a new connection
    posts(before, "own")
    assert views.POSTED[-1] == ("own", "ada")
    context.close()


def test_each_viewer_posts_with_their_own_token(browser, server):
    """A token is bound to its viewer's secret: another viewer's is refused."""
    pages = {}
    for name in ("ada", "grace"):
        page = browser.new_context().new_page()
        page.goto(f"{server}/csrfprobe/sign-in/{name}/")
        open_live(page, f"{server}/csrfprobe/")
        pages[name] = page
    adas = token_of(pages["ada"], "own")

    by(pages["grace"], "own-form").locator("input[name=csrfmiddlewaretoken]").evaluate(
        "(input, token) => { input.value = token }", adas
    )
    assert submit(pages["grace"], "own") == 403

    posts(pages["ada"], "own")
    open_live(pages["grace"], f"{server}/csrfprobe/")
    posts(pages["grace"], "own")
    assert views.POSTED == [("own", "ada"), ("own", "grace")]
