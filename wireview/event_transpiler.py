"""The attribute ``{% on %}`` renders for an event binding.

The client reads it (``static/wireview/events.mjs``); nothing here generates
script. The inline ``onclick`` transpiler this module was named for went with
#90 and its last callers with #119.
"""

from __future__ import annotations

import json
import re
import typing as t

from django.core.serializers.json import DjangoJSONEncoder

if t.TYPE_CHECKING:
    from .js import JS

#: Every event binding is an attribute ``wire-on-<event>[.<modifier>...]``. The
#: client delegates from the document root, so no script lives in the markup and
#: a CSP without ``'unsafe-inline'`` holds (#90, docs/design/csp-event-binding.md).
BINDING_PREFIX = "wire-on-"
# What may follow the prefix: safe in an attribute name, and a template value
# cannot close the attribute or open another one.
_BINDING_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_:-]*(\.[A-Za-z0-9_:-]+)*")


#: The modifiers the client understands, and what each does. ``events.mjs`` is
#: the implementation; ``tests/test_event_binding.py`` holds the two together.
#: The ones in ``MODIFIER_ARGUMENTS`` take the next dotted token as their argument.
MODIFIERS: dict[str, str] = {
    "prevent": "Calls event.preventDefault()",
    "stop": "Calls event.stopPropagation()",
    "debounce": "Debounce the event handler (requires delay in ms)",
    "throttle": "Throttle the event handler (requires delay in ms)",
    "ctrl": "Only trigger if Ctrl key is pressed",
    "alt": "Only trigger if Alt key is pressed",
    "shift": "Only trigger if Shift key is pressed",
    "meta": "Only trigger if Meta (Cmd/Win) key is pressed",
    "key": "Only trigger for specific key (requires key name)",
    "key_code": "Only trigger for specific keyCode (requires code)",
    "enter": "Only trigger on Enter key",
    "tab": "Only trigger on Tab key",
    "delete": "Only trigger on Delete key",
    "backspace": "Only trigger on Backspace key",
    "esc": "Only trigger on Escape key",
    "space": "Only trigger on Space key",
    "up": "Only trigger on Arrow Up key",
    "down": "Only trigger on Arrow Down key",
    "left": "Only trigger on Arrow Left key",
    "right": "Only trigger on Arrow Right key",
}
MODIFIER_ARGUMENTS = frozenset({"debounce", "throttle", "key", "key_code"})
# The client reads these arguments as numbers; anything else is NaN there.
_NUMBER_ARGUMENTS = frozenset({"debounce", "throttle", "key_code"})


def binding(event_and_modifiers: str, command: str | JS, kwargs: dict[str, t.Any]) -> tuple[str, str]:
    """The attribute ``{% on %}`` renders: its name and its JSON value.

    The name carries the event and its modifiers exactly as written in the
    template, so ``keyup.enter`` and ``keyup.esc`` on one element are two
    attributes (as inline ``onkeyup`` they were one, and the browser kept the
    first). The value says what to run: ``{"h": handler, "a": args, "t":
    target}`` for a server handler, ``{"js": commands}`` for a ``JS()`` chain.
    """
    from .js import JS

    if not _BINDING_NAME.fullmatch(event_and_modifiers):
        raise ValueError(
            f"{event_and_modifiers!r} is not an event binding: use letters, digits, '_', ':' and '-', "
            "with modifiers after dots, like 'keyup.enter' or 'input.debounce.300'"
        )
    tokens = event_and_modifiers.split(".")[1:]
    if "inlinejs" in tokens:
        raise ValueError(
            "the inlinejs modifier is not supported: inline JavaScript cannot run under a Content Security "
            "Policy. Use a JS() command chain, or a hook for anything JS() cannot express"
        )
    # The client skips a modifier it does not know, so click.away would bind a plain click
    tokens.reverse()
    while tokens:
        token = tokens.pop()
        if token not in MODIFIERS:
            raise ValueError(
                f"{token!r} in {event_and_modifiers!r} is not a modifier. The modifiers are: "
                f"{', '.join(MODIFIERS)}. A key by its name is key.<name>, like keydown.key.Escape"
            )
        if token not in MODIFIER_ARGUMENTS:
            continue
        # Without its argument keydown.key never fires and a debounce or throttle waits for nothing
        example = {"key": "keydown.key.Escape", "key_code": "keydown.key_code.27"}.get(token, f"input.{token}.300")
        if not tokens:
            raise ValueError(f"{token!r} in {event_and_modifiers!r} needs an argument, like {example}")
        argument = tokens.pop()
        if token in _NUMBER_ARGUMENTS and not (argument.isascii() and argument.isdigit()):
            raise ValueError(
                f"{token!r} in {event_and_modifiers!r} takes a whole number, and {argument!r} is not a whole number. "
                f"The argument comes right after it, like {example}"
            )

    if isinstance(command, JS):
        value: dict[str, t.Any] = {"js": command._commands}
    else:
        args = dict(kwargs)
        value = {"h": command}
        target = args.pop("_target", None)
        if args:
            value["a"] = args
        if target:
            value["t"] = target
    return BINDING_PREFIX + event_and_modifiers, json.dumps(value, cls=DjangoJSONEncoder, separators=(",", ":"))
