"""How django-wireview retires public API.

Every deprecation goes through :func:`warn_deprecated`, so its warning names
what replaces the old spelling and the release that removes it, and a
project can silence or escalate all of them with one filter::

    import warnings
    from wireview import WireviewDeprecationWarning

    warnings.simplefilter("error", WireviewDeprecationWarning)

The policy (what may be removed, and when) is in docs/COMPATIBILITY.md.
"""

import warnings

__all__ = ("WireviewDeprecationWarning", "warn_deprecated")


class WireviewDeprecationWarning(DeprecationWarning):
    """Public API that still works and is removed in the next major release."""


def warn_deprecated(old: str, new: str, *, removed_in: str = "2.0", stacklevel: int = 2) -> None:
    """Warn that ``old`` is deprecated in favour of ``new``.

    ``stacklevel`` counts from the caller of this function, as in
    :func:`warnings.warn`: the default points at whoever called the
    deprecated API.
    """
    warnings.warn(
        f"{old} is deprecated and will be removed in django-wireview {removed_in}; use {new}.",
        WireviewDeprecationWarning,
        stacklevel=stacklevel + 1,
    )
