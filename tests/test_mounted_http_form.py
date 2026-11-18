"""``submit_form()``: a form's own HTTP submission through Django's test client (GAP-056, #252).

``HTTP_SUBMITS`` is the table ``tests/test_dom_events_e2e.py`` submits in a
browser: the same forms, the same buttons, what the view gets.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from testproj.domprobe.live import DomHttp
from testproj.domprobe.scenarios import HTTP_SUBMITS
from testproj.domprobe.views import RECEIVED

from wireview import mount
from wireview.testing_dom import DomError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

PAGE = "/domprobe/http/"


@pytest.fixture(autouse=True)
def _clear():
    RECEIVED.clear()
    yield
    RECEIVED.clear()


@pytest.mark.parametrize("case", HTTP_SUBMITS, ids=[c.name for c in HTTP_SUBMITS])
async def test_the_view_gets_what_the_browser_sends(case):
    view = await mount(DomHttp, path=PAGE)
    response = await view.submit_form(f"[data-testid={case.form}]", submitter=f"[data-testid={case.submitter}]")
    assert response.status_code == 200
    (got,) = RECEIVED
    assert got["method"] == case.method
    assert got["fields"] == case.fields


async def test_two_fields_of_one_name_keep_the_pressed_button_of_that_name_between_them():
    """The order #246 found a browser keeps: field, submitter, field of one name (getlist)."""
    view = await mount(DomHttp, path=PAGE)
    await view.submit_form("[data-testid=post-form]", submitter="[data-testid=post-pick]")
    await view.submit_form("[data-testid=multi-form]", submitter="[data-testid=multi-pick]")
    assert [r["fields"]["pick"] for r in RECEIVED] == [["a", "b", "c"], ["a", "b", "c"]]


async def test_values_go_in_the_fields_they_name():
    view = await mount(DomHttp, path=PAGE)
    await view.submit_form(
        "[data-testid=post-form]", values={"note": "x", "box": True}, submitter="[data-testid=post-pick]"
    )
    fields = RECEIVED[0]["fields"]
    assert fields["note"] == ["x"] and fields["box"] == ["1"]


async def test_a_form_without_its_token_is_refused_as_csrf_refuses_it():
    view = await mount(DomHttp, path=PAGE)
    response = await view.submit_form("[data-testid=bare-form]")
    assert response.status_code == 403
    assert RECEIVED == []


@pytest.mark.parametrize("backend", ["db", "signed_cookies"])
async def test_the_token_passes_with_the_secret_in_the_session(backend, settings):
    settings.SESSION_ENGINE = f"django.contrib.sessions.backends.{backend}"
    settings.CSRF_USE_SESSIONS = True
    view = await mount(DomHttp, path=PAGE)
    response = await view.submit_form("[data-testid=post-form]", submitter="[data-testid=post-pick]")
    assert response.status_code == 200 and len(RECEIVED) == 1


async def test_the_mounts_user_is_the_one_logged_in():
    from asgiref.sync import sync_to_async

    user = await sync_to_async(get_user_model().objects.create_user)("poster")
    view = await mount(DomHttp, path=PAGE, user=user)
    await view.submit_form("[data-testid=post-form]", submitter="[data-testid=post-pick]")
    assert RECEIVED[0]["user"] == "poster"


async def test_a_redirect_is_followed_when_asked():
    view = await mount(DomHttp, path=PAGE)
    response = await view.submit_form("[data-testid=get-form]", values={"away": "1"})
    assert response.status_code == 302
    response = await view.submit_form("[data-testid=get-form]", values={"away": "1"}, follow=True)
    assert response.status_code == 200 and response.redirect_chain == [("/domprobe/http/", 302)]


async def test_an_own_client_keeps_its_cookies():
    client = Client(enforce_csrf_checks=True)
    client.cookies["kept"] = "1"
    view = await mount(DomHttp, path=PAGE)
    response = await view.submit_form("[data-testid=post-form]", submitter="[data-testid=post-pick]", client=client)
    assert response.status_code == 200
    assert client.cookies["kept"].value == "1"


async def test_a_relative_action_needs_the_pages_address():
    view = await mount(DomHttp)
    with pytest.raises(DomError, match=r"mount\(\.\.\., path=\.\.\.\)"):
        await view.submit_form("[data-testid=multi-form]", submitter="[data-testid=multi-pick]")
    # An absolute path needs none
    await view.submit_form("[data-testid=get-form]")
    assert RECEIVED[0]["fields"] == {"q": ["1", "2"], "away": [""]}


async def test_what_is_not_a_submission_fails():
    view = await mount(DomHttp, path=PAGE)
    with pytest.raises(DomError, match="is not a form"):
        await view.submit_form("[data-testid=post-pick]")
    with pytest.raises(DomError, match="is not a submit button of"):
        await view.submit_form("[data-testid=get-form]", submitter="[data-testid=post-pick]")
