"""Signing of component state for the ``data-state`` attribute.

The client never inspects this value. It stores it on the component's root
element and sends it back on (re)connect so the server can rebuild the
component. Since #76 the signed payload is a versioned envelope that binds the
state to what it was issued for; #58 added the page's boundary to that::

    {"v": 2, "n": "<component FQN>", "s": "<live_session>", "a": "<auth>", "d": {<state>}}

A ``TimestampSigner`` on the ``wireview.state.v2`` salt signs it, so the token also
carries an issue time and ``unsign_state`` rejects anything older than
``STATE_MAX_AGE``. Each field closes one substitution:

- ``n`` (the class) because the name travels beside the token in the ``join``
  frame: without it a signature issued for one class could be presented as
  another whose fields happen to fit (#76).
- ``s`` (the ``live_session`` the page declared, ``""`` for none) because
  otherwise a *validly* signed public-page state and a protected component's
  state could be joined together and the protected page's policy would never
  run. No forgery is needed for that, so signing the policy name on its own
  does not help -- it has to be in the same envelope as the state (#58).
- ``a`` (the authentication generation, present only inside a live_session)
  because a state issued before a logout is otherwise still a valid state.

Both ``s`` and ``a`` are absent from pages that declare no live_session, which
is what keeps the feature opt-in.

``sign_object`` keeps the value base64 (no quotes, so it does not inflate when
HTML-escaped into an attribute) and zlib-compresses it when that is smaller.

Older formats (the v1 envelope without ``s`` and ``a``, and the unversioned
tokens before it) are not read: a page that carries one reloads, which renders
it under the current auth context with fresh tokens. The window that used to
accept them, ``STATE_ACCEPT_LEGACY``, went before 1.0 (#99).
"""

from __future__ import annotations

import json
import logging
import time
import typing as t
from dataclasses import dataclass

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

from .. import settings
from .signing import get_signer

if t.TYPE_CHECKING:
    from .component import Component

log = logging.getLogger("wireview")

__all__ = [
    "ENVELOPE_VERSION",
    "SignatureExpired",
    "StateMismatch",
    "StatePayload",
    "sign_state",
    "signable_json",
    "unsign_envelope",
    "unsign_state",
]

#: Version stored in the envelope's ``v`` field.
ENVELOPE_VERSION = 2

#: Salt for the state signer. Namespaced by version so one envelope cannot be
#: verified with another's key material: a v1 token fails the v2 signer instead
#: of decoding into a policy-free payload.
STATE_SALT = "wireview.state.v2"


class StateMismatch(BadSignature):
    """The envelope was issued for a different component class than the one asked for.

    A subclass of ``BadSignature`` so a caller that only cares about "this
    state is not usable" keeps working, while the consumer can log it apart
    from an expiry.
    """

    def __init__(self, message: str, *, component_id: str = "", signed_name: str = "", asked_name: str = "") -> None:
        super().__init__(message)
        self.component_id = component_id
        self.signed_name = signed_name
        self.asked_name = asked_name


@dataclass(frozen=True)
class StatePayload:
    """What one ``data-state`` token decoded to.

    Attributes:
        state: the component's fields.
        live_session: the boundary the page declared when it issued the token,
            ``""`` when it declared none (and for every pre-v2 format).
        auth: the authentication generation the token was issued under, or
            ``None`` outside a live_session, where nothing binds it.
        version: the envelope version it came from. ``0`` for the two
            unversioned formats.
    """

    state: dict[str, t.Any]
    live_session: str = ""
    auth: str | None = None
    version: int = ENVELOPE_VERSION


class _JSONStringSerializer:
    """Serializer for ``sign_object`` that carries an already-serialized JSON string.

    Pydantic's ``model_dump_json`` is the single source of truth for how
    component fields serialize, so the signer must not re-serialize the state.
    """

    def dumps(self, obj: str) -> bytes:
        return obj.encode("utf-8")

    def loads(self, data: bytes) -> t.Any:
        return json.loads(data.decode("utf-8"))


def _signer() -> TimestampSigner:
    return get_signer(STATE_SALT)


def _envelope_json(name: str, state_json: str, live_session: str, auth: str | None) -> str:
    """Wrap an already-serialized state in the v2 envelope, as JSON text.

    Built by hand so ``state_json`` passes through untouched: Pydantic stays
    the only thing that decides how a field serializes.

    ``s`` and ``a`` are omitted outside a live_session rather than written as
    empty values, so a page that declares no boundary keeps issuing a token the
    same size it always did.
    """
    boundary = ""
    if live_session:
        boundary = f',"s":{json.dumps(live_session)},"a":{json.dumps(auth or "")}'
    return f'{{"v":{ENVELOPE_VERSION},"n":{json.dumps(name)}{boundary},"d":{state_json}}}'


def issuing_context(component: "Component") -> tuple[str, str | None]:
    """The boundary a token issued for ``component`` right now belongs to.

    Returns:
        The live_session name (``""`` when the page declared none) and the
        authentication fingerprint, which is ``None`` outside a live_session
        because nothing there would check it.
    """
    session = getattr(component.wire, "live_session", None)
    if session is None:
        return "", None
    from .live_session import auth_fingerprint

    return session.name, auth_fingerprint(component.user, component.session)


def signable_json(component: "Component") -> str:
    """The component's state as :func:`sign_state` would sign it, as JSON text."""
    # A temporary assign is left out as well: it is reset after this render, and
    # a join loads it again in joined(). Carried, a list of ten thousand rows went
    # into a page attribute (#111).
    return component.model_dump_json(exclude=set(component._meta.exclude_fields | component._meta.temporary_assigns))


def state_of(component: "Component") -> dict[str, t.Any]:
    """The state :func:`sign_state` signs, as :func:`unsign_state` gives it back."""
    return json.loads(signable_json(component))


def sign_state(component: "Component") -> str:
    """Sign the component state for embedding in ``data-state``.

    The token is reused while the state is unchanged and younger than
    ``STATE_REFRESH_AFTER``. A timestamp that moved on every render would make
    ``data-state`` differ on every render, and it is a dynamic part, so an
    unchanged component would ship a diff every time (see
    ``tests/test_diff_stability.py``). Re-issuing well before ``STATE_MAX_AGE``
    means a component that renders at least once per
    ``STATE_MAX_AGE - STATE_REFRESH_AFTER`` never expires while its page is open.
    """
    state_json = signable_json(component)
    wire = component.wire
    unheard = getattr(wire, "_unheard_state", None)
    if unheard is not None:
        if unheard[1] == state_json:
            # An HTTP render drew what the query made of the component, but the join
            # starts from the state before it heard the query and hears it again
            # (#177). Signed as drawn, a ``params_changed`` that returns early when
            # the query matches its own field skipped the second hearing: the work the
            # HTTP render cancelled never restarted and the fields left out of the
            # state came back empty.
            state_json = unheard[0]
        else:
            # Anything that changed the state since drops it for good: changed back,
            # the instance is no longer the one the query left.
            wire._unheard_state = None
    now = time.time()
    cached = getattr(wire, "_state_token", None)
    if cached is not None:
        cached_json, cached_token, issued_at = cached
        if cached_json == state_json and (now - issued_at) < settings.STATE_REFRESH_AFTER:
            return cached_token

    live_session, auth = issuing_context(component)
    envelope_json = _envelope_json(type(component)._fqn, state_json, live_session, auth)
    token = _signer().sign_object(envelope_json, serializer=_JSONStringSerializer, compress=True)
    wire._state_token = (state_json, token, now)
    return token


def unsign_envelope(value: str, name: str) -> StatePayload:
    """Decode a ``data-state`` value and check it was issued for ``name``.

    The class check happens here. The boundary checks do not: what a
    live_session admits depends on the connection asking, so ``command_join``
    compares this payload's ``live_session`` and ``auth`` against its own.

    Args:
        value: the signed token the client sent back.
        name: the component name the client sent beside it. Simple names,
            ``app:Name`` and FQNs all resolve, so the check compares classes
            rather than strings.

    Raises:
        SignatureExpired: the token is older than ``STATE_MAX_AGE``.
        StateMismatch: the envelope was issued for another class.
        BadSignature: anything else (tampered, truncated, malformed).
    """
    envelope = _signer().unsign_object(value, serializer=_JSONStringSerializer, max_age=settings.STATE_MAX_AGE)

    if not isinstance(envelope, dict) or envelope.get("v") != ENVELOPE_VERSION:
        raise BadSignature(f"Unsupported state envelope: {envelope!r:.80}")
    signed_name = envelope.get("n")
    state = envelope.get("d")
    if not isinstance(signed_name, str) or not isinstance(state, dict):
        raise BadSignature("Malformed state envelope")
    live_session = envelope.get("s", "")
    auth = envelope.get("a")
    if not isinstance(live_session, str) or not (auth is None or isinstance(auth, str)):
        raise BadSignature("Malformed state envelope")

    _check_class(signed_name, name, state)
    return StatePayload(state=state, live_session=live_session, auth=auth)


def unsign_state(value: str, name: str) -> dict[str, t.Any]:
    """The state inside :func:`unsign_envelope`, for call sites with no boundary to check."""
    return unsign_envelope(value, name).state


def _check_class(signed_name: str, asked_name: str, state: dict[str, t.Any]) -> None:
    """Fail unless both names resolve to the same component class."""
    from .component import Component

    component_id = state.get("id", "") if isinstance(state.get("id"), str) else ""
    try:
        signed_class = Component._resolve(signed_name)
        asked_class = Component._resolve(asked_name)
    except LookupError as e:
        raise StateMismatch(
            f"State signed for '{signed_name}' presented as '{asked_name}': {e}",
            component_id=component_id,
            signed_name=signed_name,
            asked_name=asked_name,
        ) from e
    if signed_class is not asked_class:
        raise StateMismatch(
            f"State signed for '{signed_class._fqn}' presented as '{asked_class._fqn}'",
            component_id=component_id,
            signed_name=signed_class._fqn,
            asked_name=asked_class._fqn,
        )
