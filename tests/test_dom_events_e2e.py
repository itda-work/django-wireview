"""A browser hears what the test helpers say it hears (GAP-056, #252).

Each action in ``testproj.domprobe.scenarios`` is done here by a user in a
browser, on the page that draws the probe's components together, and the
handlers must hear what ``tests/test_dom_events.py`` holds the helpers to:
delegation, .stop, a modifier that does not hold, a debounce, a JS() chain,
a checkbox and a radio, a form's fields and named buttons, a nested component
and a LiveComponent.
"""

import pytest
from testproj.domprobe.live import HEARD
from testproj.domprobe.scenarios import SCENARIOS, by, heard
from testproj.e2e_browser import expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_a_browser_hears_what_the_helpers_send(page, server, scenario):
    open_live(page, f"{server}/domprobe/")
    HEARD.clear()
    scenario.browser(page)
    expect_text(by(page, scenario.output), scenario.done)  # type: ignore[arg-type]
    assert heard(scenario.component, scenario.expected, HEARD) == scenario.expected
