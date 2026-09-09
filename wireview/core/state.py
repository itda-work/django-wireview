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

Three older formats exist and are rejected unless ``STATE_ACCEPT_LEGACY`` is on:

- v1: the same envelope without ``s`` and ``a``, on the ``wireview.state.v1`` salt.
- unversioned compact: ``Signer().sign_object`` of the bare state JSON.
- legacy JSON: ``Signer().sign(json)`` — the raw JSON followed by the
  signature.

None of them carries a boundary, so all three decode to "no live_session" and a
page under a policy refuses them either way. The setting exists only for a
mixed-version rollout window and for the benchmark; the default is to answer an
old token with ``reload``, which re-renders the page under the current auth
context and issues fresh ones.
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
    "LegacyState",
    "SignatureExpired",
    "StateMismatch",
    "StatePayload",
    "sign_state",
    "unsign_envelope",
    "unsign_state",
]

#: Version stored in the envelope's ``v`` field.
ENVELOPE_VERSION = 2

#: Salt for the state signer. Namespaced by version so one envelope cannot be
#: verified with another's key material -- which is also what makes a v1 token
#: fail the v2 signer instead of decoding into a policy-free payload.
STATE_SALT = "wireview.state.v2"

#: The pre-#58 envelope: same shape without ``s`` and ``a``.
V1_VERSION = 1
V1_SALT = "wireview.state.v1"

#: Salt the pre-v1 formats were signed under: they used a bare ``Signer()``, whose
#: default salt is its own dotted path.
LEGACY_STATE_SALT = "django.core.signing.Signer"


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


class LegacyState(BadSignature):
    """A correctly signed pre-v2 state arrived while ``STATE_ACCEPT_LEGACY`` is off.

    The signature is valid, so this is not tampering: it is a page rendered
    before the upgrade. The class cannot be checked, so the state is refused
    and the client reloads.
    """


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
    state_json = component.model_dump_json(exclude=component._exclude_fields)
    wire = component.wire
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


def _decode_legacy(value: str) -> dict[str, t.Any] | None:
    """Decode one of the two pre-v1 formats, or return ``None`` if it is neither.

    ``None`` means the value is not a validly signed legacy state, so the
    caller reports the original failure (tampering) rather than blaming the
    format.
    """
    try:
        # Pre-v1 tokens carry no salt, but they do use wireview's key: a project
        # that moves to a dedicated SIGNING_KEY moves its old pages with it.
        legacy_signer = get_signer(LEGACY_STATE_SALT, timestamp=False)
        if value.startswith("{"):
            decoded = json.loads(legacy_signer.unsign(value))
        else:
            decoded = legacy_signer.unsign_object(value, serializer=_JSONStringSerializer)
    except (BadSignature, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _accept_legacy(state: dict[str, t.Any]) -> StatePayload:
    if not settings.STATE_ACCEPT_LEGACY:
        raise LegacyState("Pre-v1 state format; set WIREVIEW['STATE_ACCEPT_LEGACY'] to accept it")
    log.warning(
        "Accepted a pre-v1 signed state for %s: it is not bound to a component class",
        state.get("id", "<unknown id>"),
    )
    return StatePayload(state=state, version=0)


def _decode_v1(value: str, name: str) -> StatePayload:
    """Decode a v1 envelope: the class binding without the boundary.

    Raises the same way :func:`unsign_envelope` does, and refuses the token
    outright unless ``STATE_ACCEPT_LEGACY`` is on. Accepting it yields a payload
    with no live_session, so a page under a policy still turns it down -- the
    rollout window widens what decodes, never what a boundary admits.
    """
    envelope = get_signer(V1_SALT).unsign_object(
        value, serializer=_JSONStringSerializer, max_age=settings.STATE_MAX_AGE
    )
    if not isinstance(envelope, dict) or envelope.get("v") != V1_VERSION:
        raise BadSignature(f"Unsupported state envelope: {envelope!r:.80}")
    signed_name = envelope.get("n")
    state = envelope.get("d")
    if not isinstance(signed_name, str) or not isinstance(state, dict):
        raise BadSignature("Malformed state envelope")
    _check_class(signed_name, name, state)
    if not settings.STATE_ACCEPT_LEGACY:
        raise LegacyState("A v1 state carries no live_session; set WIREVIEW['STATE_ACCEPT_LEGACY'] to accept it")
    log.warning(
        "Accepted a v1 signed state for %s: it is not bound to a live_session",
        state.get("id", "<unknown id>"),
    )
    return StatePayload(state=state, version=V1_VERSION)


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
        LegacyState: a valid pre-v2 token while ``STATE_ACCEPT_LEGACY`` is off.
        BadSignature: anything else (tampered, truncated, malformed).
    """
    if value.startswith("{"):
        legacy = _decode_legacy(value)
        if legacy is None:
            raise BadSignature("Invalid signature on the legacy JSON state format")
        return _accept_legacy(legacy)

    try:
        envelope = _signer().unsign_object(value, serializer=_JSONStringSerializer, max_age=settings.STATE_MAX_AGE)
    except SignatureExpired:
        raise
    except BadSignature as current:
        # Not a v2 envelope. Older formats are tried in age order; if none of
        # them verifies either, the original failure stands.
        try:
            return _decode_v1(value, name)
        except (LegacyState, SignatureExpired, StateMismatch):
            # These mean the v1 signature verified: the token is genuinely old,
            # genuinely expired or genuinely for another class, and saying so
            # beats reporting the v2 signature failure that came first.
            raise
        except BadSignature:
            pass
        legacy = _decode_legacy(value)
        if legacy is None:
            raise current
        return _accept_legacy(legacy)

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
