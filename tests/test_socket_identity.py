"""A join for a page rendered for someone else than the socket's handshake (#209).

A boosted navigation keeps the socket while the page it fetches is rendered for
whoever the cookies name now. Every ``data-state`` carries who its page was for
(``live_session.identity_fingerprint``), and a join whose identity is not the
connection's is answered with ``renew``: the page opens a socket under the
cookies it holds now, and its first round of joins there says ``renewed``.
These pin the comparison (under the signing key and its fallbacks), the
envelope, what an HTTP render reads for it, the answer, and what a renewed join,
a tab past its renew budget, an older client and a socket with no session get
instead. The browser side is tests/test_socket_identity_e2e.py.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import SynchronousOnlyOperation
from django.test import override_settings
from testproj.outbound import RecordingOutbound

from wireview import Component, live_session, telemetry
from wireview import session as session_module
from wireview.core.live_session import (
    AUTH_GENERATION_KEY,
    AUTH_USER_ID_KEY,
    identity_difference,
    identity_fingerprint,
    identity_fingerprints,
)
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION, RENEW_SINCE
from wireview.core.session import SessionView
from wireview.core.state import sign_state, unsign_envelope
from wireview.repository import ComponentRepository
from wireview.session import RenewBudget, WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

TEMPLATES = {
    "si/card.html": "{% load wireview %}<p {% tag_header %}>{{ this.user.get_username }}</p>",
    # Reads neither the session nor the user
    "si/plain.html": "{% load wireview %}<p {% tag_header %}>{{ n }}</p>",
    "si/page.html": "{% load wireview %}{% component 'SiPlain' id='plain' %}",
}

#: A boundary that admits everyone: what it adds is the auth fingerprint
INSIDE = live_session("si-inside")

ADA = SessionView({AUTH_USER_ID_KEY: "1", AUTH_GENERATION_KEY: "ada-1"}, session_key="k-ada")
ADA_AGAIN = SessionView({AUTH_USER_ID_KEY: "1", AUTH_GENERATION_KEY: "ada-2"}, session_key="k-ada-2")
GRACE = SessionView({AUTH_USER_ID_KEY: "2", AUTH_GENERATION_KEY: "grace-1"}, session_key="k-grace")
NOBODY = SessionView()


class SiCard(Component):
    class Meta:
        template_name = "si/card.html"


class SiPlain(Component):
    class Meta:
        template_name = "si/plain.html"

    n: int = 1


@pytest.fixture(autouse=True)
def _budget(monkeypatch):
    """Each test starts with the tabs' renew budgets full."""
    monkeypatch.setattr(session_module, "renew_budget", RenewBudget())


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    # The probe's pages too, with the context the project's pages get
                    "loaders": [
                        ("django.template.loaders.locmem.Loader", TEMPLATES),
                        "django.template.loaders.app_directories.Loader",
                    ],
                    "context_processors": [
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                    ],
                },
            }
        ]
    ):
        yield


class TestTheFingerprint:
    @pytest.mark.parametrize(
        ("page", "connection", "secrets"),
        [
            (NOBODY, ADA, ("s", "s")),  # logged in elsewhere
            (ADA, NOBODY, ("s", "s")),  # logged out elsewhere
            (ADA, GRACE, ("s", "s")),  # another user
            (ADA_AGAIN, ADA, ("s", "s")),  # the same user, logged in again
            (ADA, ADA, ("new", "s")),  # the CSRF secret alone rotated
            (ADA, ADA, ("s", None)),  # the page knew its secret, the socket carried none
        ],
        ids=["log-in", "log-out", "another-user", "log-in-again", "rotate-csrf", "no-secret-on-the-socket"],
    )
    def test_a_change_of_identity_differs(self, page, connection, secrets):
        difference = identity_difference(
            identity_fingerprint(page, secrets[0]), [identity_fingerprint(connection, secrets[1])]
        )
        assert difference == ("login" if page is not connection else "csrf")

    @pytest.mark.parametrize(
        ("page", "connection", "secrets"),
        [
            (NOBODY, NOBODY, (None, None)),
            (ADA, ADA, ("s", "s")),
            # A page whose state was signed before its first {% csrf_token %} made one
            (ADA, ADA, (None, "s")),
            # Another session key, the same login: a signed-cookie session moves its key on every write
            (ADA, SessionView(dict(ADA), session_key="another"), ("s", "s")),
        ],
        ids=["anonymous", "same", "page-knew-no-secret", "another-session-key"],
    )
    def test_the_same_identity_does_not(self, page, connection, secrets):
        assert (
            identity_difference(identity_fingerprint(page, secrets[0]), [identity_fingerprint(connection, secrets[1])])
            == ""
        )

    def test_it_reveals_neither_the_nonce_nor_the_secret(self):
        value = identity_fingerprint(ADA, "a-csrf-secret-of-thirty-two-chars")
        assert "ada-1" not in value and "a-csrf-secret" not in value
        assert len(value) == 33

    @override_settings(WIREVIEW={"SIGNING_KEY": "new-key", "SIGNING_KEY_FALLBACKS": ["old-key"]})
    def test_a_page_hashed_under_a_fallback_key_is_the_same_identity(self):
        """A page rendered before a key rotation, or by a process still on the old key."""
        before = identity_fingerprint(ADA, "s", key="old-key")
        assert before != identity_fingerprint(ADA, "s"), "the keys hash apart"
        assert identity_difference(before, identity_fingerprints(ADA, "s")) == ""
        assert (
            identity_difference(identity_fingerprint(GRACE, "s", key="old-key"), identity_fingerprints(ADA, "s"))
            == "login"
        )

    @override_settings(WIREVIEW={"SIGNING_KEY": "new-key", "SIGNING_KEY_FALLBACKS": []})
    def test_a_key_no_longer_accepted_is_not(self):
        assert (
            identity_difference(identity_fingerprint(ADA, "s", key="old-key"), identity_fingerprints(ADA, "s"))
            == "login"
        )


class TestTheEnvelope:
    def test_a_registered_component_signs_its_repositorys_identity(self):
        repo = ComponentRepository(is_live=True, session=GRACE)
        card = repo.build("SiCard", {"id": "card"})
        payload = unsign_envelope(sign_state(card), "SiCard")
        assert payload.identity == identity_fingerprint(GRACE, None)

    def test_one_no_repository_registered_signs_none(self):
        card = SiCard(id="card", user=AnonymousUser(), wire=WireviewMeta(params={}))
        assert unsign_envelope(sign_state(card), "SiCard").identity is None

    def test_an_http_render_signs_the_requests_session_and_csrf_secret(self, client):
        client.get("/identityprobe/a/")  # the first visit makes the CSRF cookie
        user = get_user_model().objects.create(username="si-ada")
        client.force_login(user)
        response = client.get("/identityprobe/a/")
        request = response.wsgi_request
        state = response.content.decode().split('data-state="', 1)[1].split('"', 1)[0]
        payload = unsign_envelope(state, "IdentityCard")
        assert payload.identity == identity_fingerprint(request.session, request.META["CSRF_COOKIE"])
        assert payload.identity.split(".")[1], "the page knew its secret"

    def test_an_http_render_that_cannot_read_its_session_signs_none(self, rf):
        class Unreadable:
            """A database session store, loaded on first read."""

            session_key = "k"

            def items(self):
                raise SynchronousOnlyOperation("an async view's render, on the event loop")

        request = rf.get("/")
        repo = ComponentRepository(is_live=False, session=Unreadable(), request=request)
        assert repo.identity() is None


def _render_plain(rf, *, cookie: bool = False, log_in=None):
    """A page whose component reads neither the session nor the user, through Django's session middleware."""
    from django.conf import settings as django_settings
    from django.contrib.auth import login
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.http import HttpResponse
    from django.template import engines

    request = rf.get("/")
    if cookie:
        request.COOKIES[django_settings.SESSION_COOKIE_NAME] = "no-such-session"
    middleware = SessionMiddleware(lambda request: HttpResponse())
    middleware.process_request(request)
    request.user = AnonymousUser()
    if log_in is not None:
        login(request, log_in, backend="django.contrib.auth.backends.ModelBackend")
    html = engines["django"].get_template("si/page.html").render({}, request)
    response = middleware.process_response(request, HttpResponse(html))
    state = html.split('data-state="', 1)[1].split('"', 1)[0]
    return request, response, unsign_envelope(state, "SiPlain").identity


class TestTheHttpRenderLeavesTheSessionAlone:
    """Reading a session marks it accessed, and Django then varies the response on Cookie (#209)."""

    def test_no_session_cookie_signs_the_anonymous_identity_without_reading_it(self, rf):
        request, response, identity = _render_plain(rf)
        assert not request.session.accessed
        assert not response.has_header("Vary"), "a shared cache keeps the page as it did before"
        assert identity == identity_fingerprint(None, None)

    def test_a_session_cookie_is_read(self, rf):
        request, response, identity = _render_plain(rf, cookie=True)
        assert request.session.accessed
        assert "Cookie" in response["Vary"]
        assert identity == identity_fingerprint(None, None), "an unknown session key is an empty session"

    def test_a_login_in_the_same_request_is_read(self, rf):
        """No cookie came in, but the view logged in before it rendered."""
        user = get_user_model().objects.create(username="si-same-request")
        request, _, identity = _render_plain(rf, log_in=user)
        assert identity == identity_fingerprint(request.session, request.META.get("CSRF_COOKIE"))
        assert identity != identity_fingerprint(None, None)


async def started(
    session_view: SessionView,
    *,
    vsn: int = PROTOCOL_VERSION,
    csrf=None,
    tab: str = "tab-1",
    session_known: bool = True,
):
    outbound = RecordingOutbound()
    session = WireviewSession(outbound, user=AnonymousUser(), channel_name="si-1")
    await session.start(session=session_view, vsn=vsn, csrf_cookies=csrf, tab=tab, session_known=session_known)
    return session, outbound


def page_state(page: SessionView, *, boundary=None, csrf: str | None = None, id: str = "card", key=None) -> str:
    """A state as a page rendered for ``page`` signed it."""
    repo = ComponentRepository(is_live=False, session=page, live_session=boundary, request=None)
    card = repo.build("SiCard", {"id": id})
    card.wire.identity = lambda: identity_fingerprint(page, csrf, key=key)
    return sign_state(card)


async def join(session: WireviewSession, state: str, ref: int | None = None, renewed: bool | None = None) -> None:
    payload: dict = {"name": "SiCard", "state": state}
    if ref is not None:
        payload["ref"] = ref
    if renewed is not None:
        payload["renewed"] = renewed
    await session.handle_message({"command": "join", "payload": payload})


def commands(outbound: RecordingOutbound) -> list[str]:
    return [command for command, _ in outbound.commands]


@pytest.mark.asyncio
class TestTheJoin:
    async def test_another_identity_is_told_to_renew_and_mounts_nothing(self):
        session, outbound = await started(NOBODY)
        await join(session, page_state(ADA), ref=7)
        assert outbound.commands == [("renew", {"id": "card", "ref": 7})]
        assert session.repo.get("card") is None
        assert session.live_session_name is None, "the boundary is not settled by a join that did not happen"

    async def test_the_same_identity_joins(self):
        session, outbound = await started(ADA)
        await join(session, page_state(ADA))
        assert [command for command, _ in outbound.commands][:1] == ["render"]

    async def test_a_state_with_no_identity_is_not_checked(self):
        """What a server before #209 issued, or a component no repository registered."""
        session, outbound = await started(NOBODY)
        card = SiCard(id="card", user=AnonymousUser(), wire=WireviewMeta(params={}))
        await join(session, sign_state(card))
        assert outbound.renders()

    async def test_a_rotated_csrf_secret_is_told_to_renew(self):
        session, outbound = await started(ADA, csrf={"csrftoken": "a" * 32})
        await join(session, page_state(ADA, csrf="b" * 32))
        assert [command for command, _ in outbound.commands] == ["renew"]

    async def test_inside_a_boundary_it_is_told_to_renew_rather_than_reload(self):
        session, outbound = await started(NOBODY)
        await join(session, page_state(ADA, boundary=INSIDE))
        assert [command for command, _ in outbound.commands] == ["renew"]

    @pytest.mark.parametrize("boundary", [None, INSIDE], ids=["no-boundary", "boundary"])
    async def test_a_renewed_join_whose_login_still_differs_reloads(self, boundary, caplog):
        """The page is older than the cookies (a reconnect, a sticky component, the back button):
        another socket would not help, and joining would put one person's state under another's user."""
        session, outbound = await started(NOBODY)
        await join(session, page_state(ADA, boundary=boundary), ref=4, renewed=True)
        assert outbound.commands == [("reload", {"id": "card", "reason": "identity", "ref": 4})]
        assert session.repo.get("card") is None

    async def test_a_renewed_join_whose_csrf_secret_alone_differs_joins_as_the_connection(self, caplog):
        session, outbound = await started(ADA, csrf={"csrftoken": "a" * 32})
        await join(session, page_state(ADA, csrf="b" * 32), renewed=True)
        assert commands(outbound)[:1] == ["render"]
        assert (
            "The page is older than the browser's cookies (a reconnect, a sticky component, the back button)"
            in caplog.text
        )
        assert "do not reach the socket" in caplog.text

    async def test_only_the_renewed_join_is_spared_a_second_renew(self):
        """A renewed socket meets another tab's change later: the boosted move's join is told again."""
        session, outbound = await started(ADA)
        await join(session, page_state(ADA), renewed=True)
        assert commands(outbound)[:1] == ["render"]
        before = len(outbound.commands)
        await join(session, page_state(NOBODY, id="other"), ref=9)
        assert outbound.commands[before:] == [("renew", {"id": "other", "ref": 9})]

    @pytest.mark.parametrize("boundary", [None, INSIDE], ids=["no-boundary", "boundary"])
    async def test_an_older_client_joins_as_before(self, boundary, caplog):
        """It cannot hear renew: outside a boundary it joins as the connection, inside the boundary refuses it."""
        session, outbound = await started(NOBODY, vsn=RENEW_SINCE - 1)
        await join(session, page_state(ADA, boundary=boundary), ref=3)
        if boundary is None:
            assert outbound.renders(), "rendered for this connection"
        else:
            assert outbound.commands == [("reload", {"id": "card", "reason": "live_session", "ref": 3})]
        assert "renew" not in commands(outbound)
        assert "socket opened afresh" not in caplog.text

    async def test_a_socket_with_no_session_compares_nothing(self):
        """A WebSocket stack without SessionMiddleware (wireview.W020): the handshake says nobody."""
        session, outbound = await started(NOBODY, session_known=False)
        await join(session, page_state(ADA))
        assert outbound.renders()
        assert unsign_envelope(session.repo.get("card").wire._state_token[1], "SiCard").identity is None

    async def test_a_tab_past_its_renew_budget_is_answered_as_a_renewed_join(self):
        """The server's own stop, for a client that never says renewed: a fresh socket each time."""
        answers = []
        for _ in range(RenewBudget.LIMIT + 1):
            session, outbound = await started(NOBODY, tab="looping")
            await join(session, page_state(ADA))
            answers.append(commands(outbound))
        assert answers == [["renew"]] * RenewBudget.LIMIT + [["reload"]]
        session, outbound = await started(NOBODY, tab="another")
        await join(session, page_state(ADA))
        assert commands(outbound) == ["renew"], "another tab has its own"

    async def test_a_connection_spends_one_renew_however_many_joins_it_answers(self):
        session, outbound = await started(NOBODY, tab="many")
        for index in range(RenewBudget.LIMIT + 2):
            await join(session, page_state(ADA, id=f"card-{index}"))
        assert commands(outbound) == ["renew"] * (RenewBudget.LIMIT + 2)

    @override_settings(WIREVIEW={"SIGNING_KEY": "new-key", "SIGNING_KEY_FALLBACKS": ["old-key"]})
    async def test_a_page_from_before_a_key_rotation_joins(self):
        """Every open tab reconnects after a deploy that rotated the key; none is told to renew for it."""
        session, outbound = await started(ADA)
        await join(session, page_state(ADA, key="old-key"))
        assert commands(outbound)[:1] == ["render"]

    async def test_telemetry_names_the_reason(self):
        heard = []

        def receiver(sender, **kwargs):
            heard.append(kwargs["reason"])

        telemetry.join_rejected.connect(receiver)
        telemetry.enable()
        try:
            session, _ = await started(NOBODY)
            await join(session, page_state(GRACE))
        finally:
            telemetry.disable()
            telemetry.join_rejected.disconnect(receiver)
        assert heard == ["identity"]
        assert "identity" in telemetry.JOIN_REJECTED_REASONS


class TestTheRenewBudget:
    def test_a_tab_gets_its_renews_back_after_the_window(self):
        now = [0.0]
        budget = RenewBudget(clock=lambda: now[0])
        assert [budget.take("t") for _ in range(RenewBudget.LIMIT + 1)] == [True] * RenewBudget.LIMIT + [False]
        now[0] = RenewBudget.WINDOW
        assert budget.take("t")

    def test_stale_tabs_are_forgotten(self):
        now = [0.0]
        budget = RenewBudget(clock=lambda: now[0])
        for index in range(RenewBudget.SWEEP_AT):
            budget.take(f"t{index}")
        now[0] = RenewBudget.WINDOW
        budget.take("fresh")
        assert len(budget._renews) == 1
