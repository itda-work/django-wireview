from __future__ import annotations

import json
import typing as t

from django.core.serializers.json import DjangoJSONEncoder
from lru import LRU

from .settings import TRANSPILER_CACHE_SIZE

if t.TYPE_CHECKING:
    from .js import JS

Stack = list[t.Any]

CACHE: t.MutableMapping[str, str | None] = t.cast(t.MutableMapping[str, str | None], LRU(TRANSPILER_CACHE_SIZE))


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
