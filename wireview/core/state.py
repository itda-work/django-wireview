"""Signing of component state for the ``data-state`` attribute.

The client never inspects this value. It stores it on the component's root
element and sends it back on (re)connect so the server can rebuild the
component. Two formats are accepted when decoding:

- compact (current): ``Signer.sign_object`` of the JSON state, zlib-compressed
  when that is smaller. Base64 keeps the value free of quotes, so it does not
  inflate when HTML-escaped into an attribute, and it stays deterministic for
  an unchanged state so the diff engine can skip it.
- legacy: ``Signer().sign(json)`` — the raw JSON followed by the signature.
  Pages rendered before an upgrade still carry this format.
"""

from __future__ import annotations

import json
import typing as t

from django.core.signing import Signer

if t.TYPE_CHECKING:
    from .component import Component


class _JSONStringSerializer:
    """Serializer for ``sign_object`` that carries an already-serialized JSON string.

    Pydantic's ``model_dump_json`` is the single source of truth for how
    component fields serialize, so the signer must not re-serialize the state.
    """

    def dumps(self, obj: str) -> bytes:
        return obj.encode("utf-8")

    def loads(self, data: bytes) -> dict[str, t.Any]:
        return json.loads(data.decode("utf-8"))


def sign_state(component: "Component") -> str:
    """Sign the component state for embedding in ``data-state``."""
    state_json = component.model_dump_json(exclude=component._exclude_fields)
    return Signer().sign_object(state_json, serializer=_JSONStringSerializer, compress=True)


def unsign_state(value: str) -> dict[str, t.Any]:
    """Decode a ``data-state`` value in either the compact or the legacy format.

    Raises ``django.core.signing.BadSignature`` when the signature is invalid.
    """
    if value.startswith("{"):
        # Legacy format: the payload is the raw JSON text.
        return json.loads(Signer().unsign(value))
    return Signer().unsign_object(value, serializer=_JSONStringSerializer)
