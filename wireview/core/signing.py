"""Key material for everything wireview signs.

Two values travel to the client and come back as the only proof of what they
claim to be: the ``data-state`` envelope (``core/state.py``) and the chunked
upload token (``features/uploads.py``). Both are verified by whichever worker
receives them, so the key behind them has to be a value every worker shares.

Django's ``SECRET_KEY`` is such a value, and it is what wireview used until
#83. The problem is lifetime, not strength: a stateless upload endpoint makes
the token the sole authority for a chunk, so rotating ``SECRET_KEY`` kills
every upload in flight and the ``data-state`` of every open page (14 days by
default) along with the sessions it was rotated for. ``WIREVIEW["SIGNING_KEY"]``
separates the two lifetimes in both directions.

``SIGNING_KEY_FALLBACKS`` is not optional decoration. ``SECRET_KEY_FALLBACKS``
is what makes a ``SECRET_KEY`` rotation survivable, and it comes for free while
wireview rides on Django's key; a dedicated key without its own fallbacks would
be a step backwards.

Both are read on every call, so a rotation takes effect without a restart and a
test can override them.
"""

from __future__ import annotations

import typing as t

from django.conf import settings as django_settings
from django.core.signing import Signer, TimestampSigner

from .. import settings

__all__ = ["get_signer", "signing_key", "signing_key_fallbacks"]


def signing_key() -> str:
    """The key wireview signs with.

    ``WIREVIEW["SIGNING_KEY"]`` when set, Django's ``SECRET_KEY`` otherwise. An
    empty string is treated as unset, the way Django itself treats a falsy key;
    ``wireview.W009`` reports it so the fallback is not silent.
    """
    return settings.SIGNING_KEY or django_settings.SECRET_KEY


def signing_key_fallbacks() -> list[bytes | str]:
    """Keys accepted for verification but never used to sign.

    ``None`` means "follow Django", so a project that only rotates
    ``SECRET_KEY`` keeps the behaviour it had before this setting existed.
    """
    fallbacks: t.Iterable[bytes | str] | None = settings.SIGNING_KEY_FALLBACKS
    if fallbacks is None:
        fallbacks = getattr(django_settings, "SECRET_KEY_FALLBACKS", [])
    return list(fallbacks or [])


@t.overload
def get_signer(salt: str) -> TimestampSigner: ...


@t.overload
def get_signer(salt: str, *, timestamp: t.Literal[True]) -> TimestampSigner: ...


@t.overload
def get_signer(salt: str, *, timestamp: t.Literal[False]) -> Signer: ...


def get_signer(salt: str, *, timestamp: bool = True) -> Signer:
    """A signer for one salt, on wireview's key.

    Args:
        salt: Namespace for the token. Salts are separated per purpose, so a
            state token cannot be presented as an upload token or vice versa.
        timestamp: Whether the token carries its issue time. ``True`` (the
            default) gives a ``TimestampSigner`` and lets the verifier apply a
            ``max_age``; ``False`` is for the pre-v1 state formats, which have
            no timestamp to check.

    Returns:
        A signer bound to wireview's key and fallbacks.
    """
    factory = TimestampSigner if timestamp else Signer
    return factory(key=signing_key(), fallback_keys=signing_key_fallbacks(), salt=salt)
