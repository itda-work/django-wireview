"""Signing of component state for the ``data-state`` attribute.

The client never inspects this value. It stores it on the component's root
element and sends it back on (re)connect so the server can rebuild the
component. Since #76 the signed payload is a versioned envelope that binds the
state to the class it was issued for::

    {"v": 1, "n": "<component FQN>", "d": {<state>}}

A ``TimestampSigner`` on the ``wireview.state.v1`` salt signs it, so the token also
carries an issue time and ``unsign_state`` rejects anything older than
``STATE_MAX_AGE``. Binding the class matters because the name travels beside
the token in the ``join`` frame: without it a signature issued for one class
could be presented as another whose fields happen to fit.

``sign_object`` keeps the value base64 (no quotes, so it does not inflate when
HTML-escaped into an attribute) and zlib-compresses it when that is smaller.

Two pre-v1 formats exist and are rejected unless ``STATE_ACCEPT_LEGACY`` is on:

- unversioned compact: ``Signer().sign_object`` of the bare state JSON.
- legacy JSON: ``Signer().sign(json)`` — the raw JSON followed by the
  signature.

Neither carries a class, so neither can be bound; the setting exists only for
a mixed-version rollout window and for the benchmark.
"""

from __future__ import annotations

import json
import logging
import time
import typing as t

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
    "sign_state",
    "unsign_state",
]

#: Version stored in the envelope's ``v`` field.
ENVELOPE_VERSION = 1

#: Salt for the state signer. Namespaced by version so a future envelope
#: cannot be verified with this one's key material.
STATE_SALT = "wireview.state.v1"

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


class LegacyState(BadSignature):
    """A correctly signed pre-v1 state arrived while ``STATE_ACCEPT_LEGACY`` is off.

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


def _envelope_json(name: str, state_json: str) -> str:
    """Wrap an already-serialized state in the v1 envelope, as JSON text.

    Built by hand so ``state_json`` passes through untouched: Pydantic stays
    the only thing that decides how a field serializes.
    """
    return f'{{"v":{ENVELOPE_VERSION},"n":{json.dumps(name)},"d":{state_json}}}'


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

    envelope_json = _envelope_json(type(component)._fqn, state_json)
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


def _accept_legacy(state: dict[str, t.Any]) -> dict[str, t.Any]:
    if not settings.STATE_ACCEPT_LEGACY:
        raise LegacyState("Pre-v1 state format; set WIREVIEW['STATE_ACCEPT_LEGACY'] to accept it")
    log.warning(
        "Accepted a pre-v1 signed state for %s: it is not bound to a component class",
        state.get("id", "<unknown id>"),
    )
    return state


def unsign_state(value: str, name: str) -> dict[str, t.Any]:
    """Decode a ``data-state`` value and check it was issued for ``name``.

    Args:
        value: the signed token the client sent back.
        name: the component name the client sent beside it. Simple names,
            ``app:Name`` and FQNs all resolve, so the check compares classes
            rather than strings.

    Raises:
        SignatureExpired: the token is older than ``STATE_MAX_AGE``.
        StateMismatch: the envelope was issued for another class.
        LegacyState: a valid pre-v1 token while ``STATE_ACCEPT_LEGACY`` is off.
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
    except BadSignature:
        # Not a v1 envelope. It may still be the unversioned compact format;
        # if it is not, the original failure stands.
        legacy = _decode_legacy(value)
        if legacy is None:
            raise
        return _accept_legacy(legacy)

    if not isinstance(envelope, dict) or envelope.get("v") != ENVELOPE_VERSION:
        raise BadSignature(f"Unsupported state envelope: {envelope!r:.80}")
    signed_name = envelope.get("n")
    state = envelope.get("d")
    if not isinstance(signed_name, str) or not isinstance(state, dict):
        raise BadSignature("Malformed state envelope")

    _check_class(signed_name, name, state)
    return state


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
