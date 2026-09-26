"""A page on another site cannot open this site's socket, seen from a browser (#96).

Playwright serves a page as http://evil.test/ and that page opens a WebSocket to
the test server. The browser sends ``Origin: http://evil.test``, which is not in
ALLOWED_HOSTS, so the handshake is refused; the server's own page still connects.
"""

import pytest
from django.test import override_settings
from testproj.e2e_browser import open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

OPEN_SOCKET = """
(url) => new Promise((resolve) => {
  const socket = new WebSocket(url);
  socket.onopen = () => { socket.close(); resolve("open"); };
  socket.onclose = () => resolve("refused");
})
"""


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def test_a_page_on_another_site_is_refused_and_the_sites_own_page_is_not(page):
    with override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost"]), serve() as base_url:
        socket_url = base_url.replace("http", "ws", 1) + "/__wireview__"

        page.route("http://evil.test/", lambda route: route.fulfill(content_type="text/html", body="<p>evil</p>"))
        page.goto("http://evil.test/")
        from_elsewhere = page.evaluate(OPEN_SOCKET, socket_url)

        open_live(page, f"{base_url}/valueprobe/")
        from_the_site = page.evaluate(OPEN_SOCKET, socket_url)

    assert (from_elsewhere, from_the_site) == ("refused", "open")
