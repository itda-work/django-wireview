"""A join for a page rendered for someone else than the socket's handshake (#209).

A boosted navigation keeps the socket while the page it fetches is rendered for
whoever the cookies name now. Every ``data-state`` carries who its page was for
(``live_session.identity_fingerprint``), and a join whose identity is not the
connection's is answered with ``renew``: the page opens a socket under the
cookies it holds now. These pin the comparison, the envelope, the answer and
what an older client or a renewed socket gets instead. The browser side is
tests/test_socket_identity_e2e.py.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import SynchronousOnlyOperation
from django.test import override_settings
from testproj.outbound import RecordingOutbound

from wireview import Component, live_session, telemetry
from wireview.core.live_session import AUTH_GENERATION_KEY, AUTH_USER_ID_KEY, identity_differs, identity_fingerprint
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION, RENEW_SINCE, socket_renewed
from wireview.core.session import SessionView
from wireview.core.state import sign_state, unsign_envelope
from wireview.repository import ComponentRepository
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

TEMPLATES = {"si/card.html": "{% load wireview %}<p {% tag_header %}>{{ this.user.get_username }}</p>"}

#: A boundary that admits everyone: what it adds is the auth fingerprint
INSIDE = live_session("si-inside")

ADA = SessionView({AUTH_USER_ID_KEY: "1", AUTH_GENERATION_KEY: "ada-1"}, session_key="k-ada")
ADA_AGAIN = SessionView({AUTH_USER_ID_KEY: "1", AUTH_GENERATION_KEY: "ada-2"}, session_key="k-ada-2")
GRACE = SessionView({AUTH_USER_ID_KEY: "2", AUTH_GENERATION_KEY: "grace-1"}, session_key="k-grace")
NOBODY = SessionView()


class SiCard(Component):
    class Meta:
        template_name = "si/card.html"


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
        assert identity_differs(identity_fingerprint(page, secrets[0]), identity_fingerprint(connection, secrets[1]))

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
        assert not identity_differs(
            identity_fingerprint(page, secrets[0]), identity_fingerprint(connection, secrets[1])
        )

    def test_it_reveals_neither_the_nonce_nor_the_secret(self):
        value = identity_fingerprint(ADA, "a-csrf-secret-of-thirty-two-chars")
        assert "ada-1" not in value and "a-csrf-secret" not in value
        assert len(value) == 33


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


async def started(session_view: SessionView, *, vsn: int = PROTOCOL_VERSION, renewed: bool = False, csrf=None):
    outbound = RecordingOutbound()
    session = WireviewSession(outbound, user=AnonymousUser(), channel_name="si-1")
    await session.start(session=session_view, vsn=vsn, csrf_cookies=csrf, renewed=renewed)
    return session, outbound


def page_state(page: SessionView, *, boundary=None, csrf: str | None = None) -> str:
    """A state as a page rendered for ``page`` signed it."""
    repo = ComponentRepository(is_live=False, session=page, live_session=boundary, request=None)
    card = repo.build("SiCard", {"id": "card"})
    card.wire.identity = lambda: identity_fingerprint(page, csrf)
    return sign_state(card)


async def join(session: WireviewSession, state: str, ref: int | None = None) -> None:
    payload = {"name": "SiCard", "state": state}
    if ref is not None:
        payload["ref"] = ref
    await session.handle_message({"command": "join", "payload": payload})


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

    @pytest.mark.parametrize(
        "renewed, vsn", [(True, PROTOCOL_VERSION), (False, RENEW_SINCE - 1)], ids=["renewed", "older-client"]
    )
    async def test_otherwise_it_joins_as_the_connection_as_before(self, renewed, vsn, caplog):
        """A renewed socket is not told again -- that would loop -- and an older client cannot hear it."""
        session, outbound = await started(NOBODY, vsn=vsn, renewed=renewed)
        await join(session, page_state(ADA))
        assert outbound.renders(), "outside a boundary: rendered for this connection"
        assert "renew" not in [command for command, _ in outbound.commands]
        assert ("still names another identity" in caplog.text) is renewed

    @pytest.mark.parametrize(
        "renewed, vsn", [(True, PROTOCOL_VERSION), (False, RENEW_SINCE - 1)], ids=["renewed", "older-client"]
    )
    async def test_inside_a_boundary_the_boundary_still_refuses_it(self, renewed, vsn):
        session, outbound = await started(NOBODY, vsn=vsn, renewed=renewed)
        await join(session, page_state(ADA, boundary=INSIDE), ref=3)
        assert outbound.commands == [("reload", {"id": "card", "reason": "live_session", "ref": 3})]

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


@pytest.mark.parametrize(
    ("query", "renewed"),
    [(b"vsn=11&tab=x&renewed=1", True), (b"vsn=11", False), (b"renewed=0", False), (b"renewed=1&renewed=1", False)],
)
def test_the_socket_says_it_was_opened_to_renew(query, renewed):
    assert socket_renewed(query) is renewed
