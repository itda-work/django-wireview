from __future__ import annotations

import json
import re
import typing as t
from collections import OrderedDict

from django.core.serializers.json import DjangoJSONEncoder

from .settings import TRANSPILER_CACHE_SIZE

if t.TYPE_CHECKING:
    from .js import JS

Stack = list[t.Any]


class LRUCache(t.MutableMapping[str, str | None]):
    """Bounded mapping that evicts the least recently used entry.

    Pure Python on purpose: ``lru-dict`` is a C extension without wheels for
    every platform wireview targets (Windows ARM64 has none), and the
    transpiler cache is far too small for the difference to matter.
    """

    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self._data: OrderedDict[str, str | None] = OrderedDict()

    def __getitem__(self, key: str) -> str | None:
        value = self._data[key]
        self._data.move_to_end(key)
        return value

    def __setitem__(self, key: str, value: str | None) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __iter__(self) -> t.Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


CACHE: t.MutableMapping[str, str | None] = LRUCache(TRANSPILER_CACHE_SIZE)

#: Every event binding is an attribute ``wire-on-<event>[.<modifier>...]``. The
#: client delegates from the document root, so no script lives in the markup and
#: a CSP without ``'unsafe-inline'`` holds (#90, docs/design/csp-event-binding.md).
BINDING_PREFIX = "wire-on-"
# What may follow the prefix: safe in an attribute name, and a template value
# cannot close the attribute or open another one.
_BINDING_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_:-]*(\.[A-Za-z0-9_:-]+)*")


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
    if "inlinejs" in event_and_modifiers.split(".")[1:]:
        raise ValueError(
            "the inlinejs modifier is not supported: inline JavaScript cannot run under a Content Security "
            "Policy. Use a JS() command chain, or a hook for anything JS() cannot express"
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


def transpile(
    event_and_modifiers: str,
    command: str | JS,
    kwargs: dict[str, t.Any],
):
    """Translates from the tag `on` in to JavaScript.

    Supports both string commands (legacy) and JS command builder objects.
    """
    from .js import JS

    name, *modifiers = event_and_modifiers.split(".")

    # Handle JS command builder objects
    if isinstance(command, JS):
        return _transpile_js_commands(name, modifiers, command)

    # Legacy string command handling
    cache_key = f"_handler:{name}.{modifiers}.{command}.{kwargs}"
    code: str | None = CACHE.get(cache_key)
    if code is None:
        if not modifiers or modifiers[-1] != "inlinejs":
            modifiers.append("_wireview_code")
        code = command
        # Stack: [kwargs, event_name] - event_name used for loading classes
        stack: Stack = [kwargs, name]
        while modifiers:
            modifier = modifiers.pop()
            handler: t.Callable[[str, Stack], str] | None = getattr(Modifiers, modifier, None)
            if handler:
                code = handler(code, stack)
            else:
                stack.append(modifier)

        CACHE[cache_key] = code
    return "on" + name, code


def _transpile_js_commands(
    event_name: str,
    modifiers: list[str],
    js_commands: JS,
) -> tuple[str, str]:
    """Transpile JS command builder to JavaScript code."""
    # Build the base execution code
    commands_json = js_commands.to_json()
    code = f"wireview.exec(event.target, {commands_json})"

    # Apply modifiers in reverse order
    stack: Stack = []
    while modifiers:
        modifier = modifiers.pop()
        handler: t.Callable[[str, Stack], str] | None = getattr(Modifiers, modifier, None)
        if handler:
            code = handler(code, stack)
        else:
            stack.append(modifier)

    return "on" + event_name, code


class Modifiers:
    @staticmethod
    def _wireview_code(code: str, stack: Stack):
        # Stack order: [kwargs, event_name] - pop gets last item first
        event_type = stack.pop() if stack else None
        kwargs = json.dumps(stack.pop(), cls=DjangoJSONEncoder) if stack else "{}"
        base = f"wireview.send(event.target, '{code}', {kwargs}"
        if event_type:
            return f"{base}, '{event_type}')"
        return f"{base})"

    @staticmethod
    def _add_curly(code: str):
        return "{%s}" % (code,)

    @staticmethod
    def inlinejs(code: str, stack: Stack):
        return code

    # Events

    @classmethod
    def debounce(cls, code: str, stack: Stack):
        delay = int(stack.pop())
        code = cls._add_curly(code)
        return f"wireview.debounce({delay})(() => {code})()"

    @classmethod
    def throttle(cls, code: str, stack: Stack):
        delay = int(stack.pop())
        code = cls._add_curly(code)
        return f"wireview.throttle({delay})(() => {code})()"

    @staticmethod
    def prevent(code: str, stack: Stack):
        return "event.preventDefault(); " + code

    @staticmethod
    def stop(code: str, stack: Stack):
        return "event.stopPropagation(); " + code

    # Key modifiers

    @classmethod
    def ctrl(cls, code: str, stack: Stack):
        code = cls._add_curly(code)
        return f"if (event.ctrlKey) {code}"

    @classmethod
    def alt(cls, code: str, stack: Stack):
        code = cls._add_curly(code)
        return f"if (event.altKey) {code}"

    @classmethod
    def shift(cls, code: str, stack: Stack):
        code = cls._add_curly(code)
        return f"if (event.shiftKey) {code}"

    @classmethod
    def meta(cls, code: str, stack: Stack):
        code = cls._add_curly(code)
        return f"if (event.metaKey) {code}"

    # Key codes

    @classmethod
    def key(cls, code: str, stack: Stack):
        key = stack.pop()
        code = cls._add_curly(code)
        return f"if ((event.key + '').toLowerCase() == '{key}') {code}"

    @classmethod
    def key_code(cls, code: str, stack: Stack):
        keyCode = stack.pop()
        code = cls._add_curly(code)
        return f"if (event.keyCode == {keyCode}) {code}"

    # Key shortcuts
    @classmethod
    def enter(cls, code: str, stack: Stack):
        return cls.key(code, ["enter"])

    @classmethod
    def tab(cls, code: str, stack: Stack):
        return cls.key(code, ["tab"])

    @classmethod
    def delete(cls, code: str, stack: Stack):
        return cls.key(code, ["delete"])

    @classmethod
    def backspace(cls, code: str, stack: Stack):
        return cls.key(code, ["backspace"])

    @classmethod
    def esc(cls, code: str, stack: Stack):
        return cls.key(code, ["escape"])

    @classmethod
    def space(cls, code: str, stack: Stack):
        return cls.key(code, [" "])

    @classmethod
    def up(cls, code: str, stack: Stack):
        return cls.key(code, ["arrowup"])

    @classmethod
    def down(cls, code: str, stack: Stack):
        return cls.key(code, ["arrowdown"])

    @classmethod
    def left(cls, code: str, stack: Stack):
        return cls.key(code, ["arrowleft"])

    @classmethod
    def right(cls, code: str, stack: Stack):
        return cls.key(code, ["arrowright"])
