"""Hook lifecycle edges, in a browser (#107, #108).

- A hook belongs to the component it sits in, not to the first one whose scan
  reached it: a parent joins before its nested components and took their hooks,
  so what a nested component pushed reached nothing.
- A row a render moves keeps its hook. The MutationObserver reports a move as a
  removal, after the morph, so the hook was destroyed on an element still on
  the page and never mounted again: it stopped hearing the server.
- A component that leaves the page takes its hooks with it. The MutationObserver
  that destroys hooks watches the component's root from the inside, so it never
  sees the root itself go: a hook on the root, and every hook under a root
  removed whole, missed ``destroyed()`` -- a timer or a microphone kept running
  after the page moved on.
- An event a new LiveComponent pushes from ``joined()`` reaches its hook,
  though it arrives before the element the hook sits on.
- A ``pushEvent`` reply reaches the callback that asked. Refs were counted per
  component from ``hook-1``, and a reply went to the first component holding
  that ref, so two components waiting at once could swap answers.

Fixture: tests/testproj/hookprobe/.
"""

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_text, open_live, wait_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

ROOTED = "#rooted[data-is-live='true']"


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture
def page_live(page, server):
    open_live(page, f"{server}/hookprobe/", selector=ROOTED)
    wait_live(page, "#ask-second[data-is-live='true']")
    return page


def expect_counted(page, callback: str, who: str, times: int = 1) -> None:
    """Wait until the probe hook counted ``callback`` for ``who`` ``times`` times."""
    expect(page.locator("html")).to_have_attribute(f"data-{callback}-{who}", str(times))


# --- which component a hook belongs to ----------------------------------------------------


def test_a_nested_component_reaches_its_own_hooks(page_live):
    """The parent joins first; its scan must stop at the nested component's root."""
    page = page_live

    page.get_by_test_id("ping").click()

    expect_counted(page, "pinged", "rooted-root")
    expect_counted(page, "pinged", "rooted-inner")


def test_a_row_the_render_moves_keeps_a_live_hook(page_live):
    """A move looks like a removal to the MutationObserver, so the hook is destroyed
    and the element stays. It has to be mounted again, not skipped as mounted."""
    page = page_live
    expect_text(page.locator("#rows li").first, "a")

    page.get_by_test_id("rotate").click()
    expect_text(page.locator("#rows li").first, "b")
    page.get_by_test_id("ping-rows").click()

    for row in ("a", "b", "c"):
        expect_counted(page, "pinged", f"row-{row}")
        # The same instance: never destroyed, mounted once
        expect_counted(page, "mounted", f"row-{row}")
        assert page.locator("html").get_attribute(f"data-destroyed-row-{row}") is None


# --- #107: destroyed() when the component goes -------------------------------------------


def test_a_component_its_parent_stops_rendering_destroys_its_hooks(page_live):
    page = page_live

    page.get_by_test_id("take-away").click()
    expect(page.locator("#rooted")).to_have_count(0)

    expect_counted(page, "destroyed", "rooted-root")
    expect_counted(page, "destroyed", "rooted-inner")
    # The components that stay keep their hooks
    for who in ("first", "second"):
        assert page.locator("html").get_attribute(f"data-destroyed-{who}") is None


def test_a_component_a_render_brings_back_mounts_its_hooks_and_hears_its_pushes(page_live):
    """A component a live render draws comes marked live: the page has nothing
    to join, and joining was where its hooks were looked for."""
    page = page_live

    page.get_by_test_id("take-away").click()
    expect_counted(page, "destroyed", "rooted-root")
    page.get_by_test_id("bring-back").click()

    expect_counted(page, "mounted", "rooted-root", 2)
    expect_counted(page, "mounted", "rooted-inner", 2)
    page.get_by_test_id("ping").click()
    expect_counted(page, "pinged", "rooted-inner")


def test_a_component_a_render_draws_again_is_joined_and_watches_its_own_viewport(page_live):
    """A component a live render draws with ``{% component %}`` is a new instance the
    server built and never joined. Nothing joined it: its joined() never ran and its
    viewport binding was never watched -- and before the bindings were sorted by
    owner, the shelf watched it and ran its own ``more``."""
    page = page_live
    # joined() and one call of its own viewport handler
    expect_text(page.locator("#rooted [data-testid=mores]"), "101")

    page.get_by_test_id("take-away").click()
    expect_counted(page, "destroyed", "rooted-root")
    page.get_by_test_id("bring-back").click()

    expect_text(page.locator("#rooted [data-testid=mores]"), "101")
    expect_text(page.get_by_test_id("stolen"), "0")
    expect_counted(page, "mounted", "rooted-root", 2)


def test_a_binding_the_parents_patch_draws_in_a_nested_component_is_the_nested_ones(page_live):
    """The shelf's render draws the rooted component inline, so a binding new in it
    arrives in the shelf's patch. The shelf leaves it to the rooted component's
    observer, which only hears of it if the shelf's patch tells it."""
    page = page_live
    expect_text(page.locator("#rooted [data-testid=mores]"), "101")

    page.get_by_test_id("reach-out").click()

    expect_text(page.locator("#rooted [data-testid=furthers]"), "1")
    expect_text(page.get_by_test_id("stolen"), "0")


def test_an_event_reaches_a_hook_the_same_handlers_render_draws(page_live):
    """The shelf is on the page, so its element is there and the event applied at
    once -- before the frame that patches the new hook in. It reached nothing."""
    page = page_live

    page.get_by_test_id("sprout").click()

    expect_counted(page, "mounted", "shelf-new")
    expect_counted(page, "pinged", "shelf-new")


def test_an_event_reaches_a_hook_the_parents_patch_draws_in_a_live_component(page_live):
    """The shelf's patch draws a hook inside the sprout, which the page already took
    up. The patch rescanned only the shelf's own hooks, so this one never mounted.
    And the sprout's update() pushes to it: the sprout's element is there and no
    patch of its own is due -- the one to wait for is the shelf's."""
    page = page_live
    page.get_by_test_id("sprout").click()
    expect_counted(page, "pinged", "sprout")

    page.get_by_test_id("light").click()

    expect_counted(page, "mounted", "sprout-lit")
    expect_counted(page, "lit", "sprout-lit")


def test_a_new_live_component_reaches_its_hooks_from_joined(page_live):
    """The event its joined() pushes arrives before the render that brings the
    element is patched in, so before the hook exists: it reached nothing."""
    page = page_live

    page.get_by_test_id("sprout").click()

    expect_counted(page, "mounted", "sprout")
    expect_counted(page, "pinged", "sprout")


def test_leaving_the_page_destroys_every_hook_on_it(page_live):
    page = page_live

    page.get_by_test_id("leave").click()
    expect_text(page.get_by_test_id("elsewhere"), "elsewhere")

    for who in ("rooted-root", "rooted-inner", "first", "second"):
        expect_counted(page, "destroyed", who)


# --- #108: a reply reaches the callback that asked ---------------------------------------


def test_replies_reach_their_own_callbacks_when_two_components_wait_at_once(page_live):
    """The later component on the page asks first, so its reply comes back first.

    The server answers one socket's messages in order, which makes this
    deterministic: with per-component refs both requests were ``hook-1``, and
    the first reply went to the first component holding ``hook-1`` -- the other one.
    """
    page = page_live

    page.evaluate(
        """() => {
          for (const who of ["second", "first"]) {
            window.__askers[who].pushEvent("ask", {}, (reply) => {
              document.documentElement.setAttribute(`data-reply-${who}`, reply.answer);
            });
          }
        }"""
    )

    html = page.locator("html")
    expect(html).to_have_attribute("data-reply-first", "first")
    expect(html).to_have_attribute("data-reply-second", "second")
