"""The `hooks` example, and the first test anywhere of the client half.

`wire-hook` had no user in this repository -- no example, no fixture, no browser
test. The server side (``handle_hook_event``, ``push_event``) was covered by
``tests/test_hooks.py``; what had never run was ``mountHooks``, the six lifecycle
callbacks, and the ``pushEvent`` round trip. So the E2E block below is not a
demonstration of this example, it is the coverage that was missing (GAP-032 memo
§7, ``docs/design/colocated-hooks.md``).
"""

import time

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_text, open_live
from testproj.e2e_server import serve, server_errors

from .live import XLifecycle

# --- Without a browser -------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_touch_restamps():
    from wireview import mount

    view = await mount(XLifecycle, stamp="2020-01-01T00:00:00")
    await view.call("touch")

    assert view.component.stamp != "2020-01-01T00:00:00"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hiding_takes_the_hooked_element_out_of_the_html():
    """``destroyed()`` has to have something to be destroyed by."""
    from wireview import mount

    view = await mount(XLifecycle)
    assert 'wire-hook="Timeago"' in (view.render() or "")

    await view.call("hide")
    assert 'wire-hook="Timeago"' not in (view.render() or "")

    await view.call("reveal")
    assert 'wire-hook="Timeago"' in (view.render() or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_hook_event_is_counted_and_answered():
    from wireview import mount

    view = await mount(XLifecycle)

    assert await view.component.handle_hook_event("hook-1", "noted", {}) == {"count": 1}
    assert await view.component.handle_hook_event("hook-1", "noted", {}) == {"count": 2}
    assert view.component.notes == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_unknown_hook_event_is_not_answered():
    """``None`` means the hook's callback never runs -- worth being explicit about."""
    from wireview import mount

    view = await mount(XLifecycle)

    assert await view.component.handle_hook_event("hook-1", "something-else", {}) is None
    assert view.component.notes == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_highlight_pushes_to_every_hook_of_this_component():
    from wireview import mount

    view = await mount(XLifecycle)
    await view.call("highlight")

    pushed = [m for m in view.sent_messages if m.get("type") == "push_event"]
    assert len(pushed) == 1
    assert pushed[0]["event"] == "highlight"
    assert pushed[0]["payload"] == {"text": "서버가 보냈다"}
    # No hook_id: every hook on the component hears it.
    assert pushed[0]["hook_id"] is None


# --- With a browser ----------------------------------------------------------


@pytest.fixture(scope="function")
def hooks_server():
    with serve() as base_url:
        yield base_url


def _open(page, server):
    open_live(page, f"{server}/hooks/")


@pytest.mark.e2e
class TestHookLifecycle:
    @pytest.fixture(autouse=True)
    def setup_db(self, transactional_db):
        pass

    def test_a_registered_hook_mounts_and_rewrites_its_element(self, page, hooks_server):
        """The whole point: the server sent an instant, the browser shows a duration."""
        _open(page, hooks_server)

        expect_text(page.locator('[data-testid="count-mounted"]'), "1")
        expect(page.locator('[data-testid="timeago"]')).to_contain_text("초 전")

    def test_a_hook_is_mounted_before_the_first_render_lands(self, page, hooks_server):
        """`mounted()` must have run by the time there is anything to have mounted on.

        The hook file is a second deferred script, so it runs after the bundle;
        the bundle opens the socket as soon as it runs. Joining is held until the
        document is ready, which is after every deferred script -- otherwise this
        is a race, and the losing side is silent (the client only warns to the
        console that the hook is "not registered").
        """
        _open(page, hooks_server)

        expect_text(page.locator('[data-testid="count-mounted"]'), "1")
        assert "not registered" not in "\n".join(server_errors())

    def test_a_slow_hook_file_still_registers_before_the_first_join(self, page, hooks_server):
        """The race made deterministic: hold the hook file back and see who wins.

        The bundle opens the socket the moment it runs, and joining is what asks
        the server for a render -- the render whose elements the hooks mount on.
        If joining did not wait for the document, a hook file that arrives even
        slightly late would simply not be there, and the only trace would be a
        console warning in a browser nobody is watching.
        """
        page.route("**/hooks/lifecycle.js", lambda route: (time.sleep(1.0), route.continue_()))

        _open(page, hooks_server)

        expect_text(page.locator('[data-testid="count-mounted"]'), "1")

    def test_a_morph_runs_beforeupdate_then_updated(self, page, hooks_server):
        _open(page, hooks_server)
        expect_text(page.locator('[data-testid="count-mounted"]'), "1")

        page.click('[data-testid="touch"]')

        expect_text(page.locator('[data-testid="count-updated"]'), "1")
        expect_text(page.locator('[data-testid="count-beforeUpdate"]'), "1")
        # Still the hook's text, not the ISO instant the server just wrote.
        expect(page.locator('[data-testid="timeago"]')).to_contain_text("초 전")

    def test_a_morph_does_not_mount_a_second_time(self, page, hooks_server):
        """The element survived the morph, so the hook instance did too."""
        _open(page, hooks_server)
        expect_text(page.locator('[data-testid="count-mounted"]'), "1")

        page.click('[data-testid="touch"]')
        expect_text(page.locator('[data-testid="count-updated"]'), "1")

        expect(page.locator('[data-testid="count-mounted"]')).to_have_text("1")

    def test_removing_the_element_destroys_the_hook(self, page, hooks_server):
        _open(page, hooks_server)
        expect_text(page.locator('[data-testid="count-mounted"]'), "1")

        page.click('[data-testid="hide"]')

        expect_text(page.locator('[data-testid="count-destroyed"]'), "1")
        expect(page.locator('[data-testid="gone"]')).to_be_visible()

    def test_putting_it_back_mounts_a_new_instance(self, page, hooks_server):
        _open(page, hooks_server)
        expect_text(page.locator('[data-testid="count-mounted"]'), "1")

        page.click('[data-testid="hide"]')
        expect_text(page.locator('[data-testid="count-destroyed"]'), "1")
        page.click('[data-testid="reveal"]')

        expect_text(page.locator('[data-testid="count-mounted"]'), "2")

    def test_the_hook_pushes_an_event_and_reads_the_answer(self, page, hooks_server):
        _open(page, hooks_server)
        expect_text(page.locator('[data-testid="count-mounted"]'), "1")

        page.click('[data-testid="noter"]')

        # The hook's callback, and the server's own render, agree.
        expect_text(page.locator('[data-testid="reply"]'), "1")
        expect_text(page.locator('[data-testid="notes"]'), "1")

    def test_the_server_pushes_an_event_the_hook_is_listening_for(self, page, hooks_server):
        _open(page, hooks_server)
        expect_text(page.locator('[data-testid="count-mounted"]'), "1")

        page.click('[data-testid="highlight"]')

        expect_text(page.locator('[data-testid="pushed"]'), "서버가 보냈다")
