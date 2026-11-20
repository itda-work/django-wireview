"""
Phoenix LiveView.JS style client-side command builder.

This module provides a fluent API for building client-side JavaScript commands
that can be executed in the browser without a server round-trip, or combined
with server events.

A template cannot call with arguments, so a component builds the chain in a
property and the template names it:

    class Editor(Component):
        @property
        def save_js(self) -> JS:
            return JS().toggle("#modal").push("save")

    <button {% on "click" this.save_js %}>Save</button>
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import typing as t

from django.core.serializers.json import DjangoJSONEncoder

__all__ = ("JS",)


class TransitionConfig(t.TypedDict, total=False):
    """Configuration for CSS transitions.

    ``transition`` alone: its classes stay on for ``time`` ms (for good without a time).
    With ``start`` and ``end`` it runs in three phases, as Phoenix's ``{transition, start, end}``:
    ``start`` first, then ``transition`` (the classes that animate), then ``end`` in ``start``'s
    place; ``time`` ms later ``transition`` and ``end`` come off. Its time is 200 ms when not
    given (#239).
    """

    transition: str
    start: str
    end: str
    time: int


#: ``"fade"``, ``("fade", 300)``, ``("ease-out duration-300", "opacity-0", "opacity-100")``
#: with an optional time as a fourth item, or a ``TransitionConfig``
Transition = str | tuple[str, int] | tuple[str, str, str] | tuple[str, str, str, int] | TransitionConfig | None

#: A three-phase transition's time when none is given (Phoenix's default)
DEFAULT_TRANSITION_TIME = 200

_DURATION = re.compile(r"\d+(\.\d+)?(ms|s)?")
# What exec reads: an attribute name, as an element's getAttribute takes it
_ATTRIBUTE = re.compile(r"[^\s\"'>/=]+")


@dataclasses.dataclass(frozen=True)
class RelativeSelector:
    """A command's elements found from the element its binding is on (#237): ``JS.inner(sel)``, ``JS.closest(sel)``."""

    kind: t.Literal["inner", "closest"]
    selector: str

    def to_json(self) -> dict[str, str]:
        return {self.kind: self.selector}


#: A CSS selector over the document, or one relative to the binding's element
Selector = str | RelativeSelector

#: The commands and target forms a page's script runs only from a protocol version
#: on (#237, #238); an older script logs them as unknown and skips them, or fails
#: on a target it cannot read.
_CHAINS_COMMANDS = frozenset({"toggle_attr", "exec"})
_FOCUS_COMMANDS = frozenset({"push_focus", "pop_focus"})


def _new_transition(command: t.Mapping[str, t.Any]) -> bool:
    """A command an older script would run wrong: not waiting, or a three-phase transition (#239)."""
    if command.get("wait") is False or "start" in command or "end" in command:
        return True
    return any(
        isinstance(command.get(key), dict) and ("start" in command[key] or "end" in command[key])
        for key in ("transition", "show", "hide")
    )


def protocol_needed(commands: t.Iterable[t.Mapping[str, t.Any]]) -> int:
    """The protocol version a page's script needs to run ``commands``: 0 or the first that knows them all."""
    from .core.rendered import FOCUS_SINCE, JS_CHAINS_SINCE, TRANSITIONS_SINCE

    needed = 0
    for command in commands:
        if _new_transition(command):
            needed = max(needed, TRANSITIONS_SINCE)
        if command.get("cmd") in _FOCUS_COMMANDS:
            needed = max(needed, FOCUS_SINCE)
        elif command.get("cmd") in _CHAINS_COMMANDS or isinstance(command.get("to"), dict):
            needed = max(needed, JS_CHAINS_SINCE)
    return needed


class _Encoder(DjangoJSONEncoder):
    """JSON for a chain and for what carries one: a ``JS`` is its list of commands (#237)."""

    def default(self, o: t.Any) -> t.Any:
        if isinstance(o, JS):
            return o._commands
        if isinstance(o, RelativeSelector):
            return o.to_json()
        return super().default(o)


def dumps(value: t.Any) -> str:
    """``value`` as JSON, a ``JS()`` in it as its commands: an event's arguments, a pushed value."""
    return json.dumps(value, cls=_Encoder, separators=(",", ":"))


_PHASES = ("transition", "start", "end")


def _class_names(classes: t.Any, part: str) -> str:
    if not isinstance(classes, str):
        raise ValueError(f"a transition's {part} is a string of class names, not {classes!r}")
    # "fade-in 200ms" reads like CSS shorthand, but the string is only class names:
    # "200ms" would stay on the element as a class and nothing would wait (#110).
    durations = [token for token in classes.split() if _DURATION.fullmatch(token)]
    if durations:
        raise ValueError(
            f"transition={classes!r}: {durations[0]!r} is not a class name. "
            f"Pass the duration separately: ({classes.split()[0]!r}, 200)"
        )
    return classes


def _wait(wait: bool) -> bool | None:
    """Sent only when False: a chain waits for a transition unless told not to."""
    return None if wait else False


def _selector(selector: str) -> str:
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError(f"a relative target takes a CSS selector, not {selector!r}")
    return selector


class JS:
    """
    Client-side command builder for Wireview components.

    Provides a fluent interface for building JavaScript commands that execute
    in the browser. Commands can be chained and are serialized to JSON for
    transmission to the client.

    What a command changes on an element -- its classes, attributes and
    visibility -- stays through the renders that patch the element, as in
    Phoenix LiveView (#219). The server's next render of the element does not
    undo it; a later command, or a render that replaces the element, does.

    Example:
        >>> js = JS()
        >>> js.show("#modal").add_class("#btn", "active").push("save")
        >>> str(js)  # Returns JSON array of commands
    """

    def __init__(self) -> None:
        self._commands: list[dict[str, t.Any]] = []

    def _add_command(self, name: str, **kwargs: t.Any) -> JS:
        """Add a command to the chain."""
        cmd: dict[str, t.Any] = {"cmd": name}
        # Filter out None values
        cmd.update({k: v for k, v in kwargs.items() if v is not None})
        if isinstance(cmd.get("to"), RelativeSelector):
            cmd["to"] = cmd["to"].to_json()
        self._commands.append(cmd)
        return self

    # === Targets ===

    @staticmethod
    def inner(selector: str) -> RelativeSelector:
        """The elements inside the binding's element that ``selector`` matches, as a command's target (#237).

        Example:
            JS().toggle_class(JS.inner(".chevron"), "open")
        """
        return RelativeSelector("inner", _selector(selector))

    @staticmethod
    def closest(selector: str) -> RelativeSelector:
        """The nearest of the binding's element and its ancestors that ``selector`` matches (#237).

        Example:
            JS().hide(JS.closest(".card"))
        """
        return RelativeSelector("closest", _selector(selector))

    # === Combining ===

    def concat(self, *others: JS) -> JS:
        """A new chain: this one's commands, then each of ``others``'. None of them changes (#237).

        Example:
            close = JS().hide("#modal")
            save = close.concat(JS().push("save"))   # close is still only the hide
        """
        joined = JS()
        for chain in (self, *others):
            if not isinstance(chain, JS):
                raise TypeError(f"concat() joins JS() chains, not {type(chain).__name__}")
            joined._commands.extend(copy.deepcopy(chain._commands))
        return joined

    def __add__(self, other: JS) -> JS:
        """``a + b`` is ``a.concat(b)``: a new chain, neither of them changed."""
        if not isinstance(other, JS):
            return NotImplemented
        return self.concat(other)

    @staticmethod
    def _normalize_transition(
        transition: Transition,
    ) -> dict[str, t.Any] | None:
        """Normalize transition config to a dictionary."""
        if transition is None:
            return None
        if isinstance(transition, str):
            return {"transition": _class_names(transition, "transition")}
        if isinstance(transition, tuple):
            if len(transition) == 2 and isinstance(transition[0], str) and isinstance(transition[1], int):
                return {"transition": _class_names(transition[0], "transition"), "time": transition[1]}
            if len(transition) in (3, 4) and all(isinstance(part, str) for part in transition[:3]):
                run, start, end = (_class_names(t.cast(str, part), name) for part, name in zip(transition, _PHASES))
                time = transition[3] if len(transition) == 4 else DEFAULT_TRANSITION_TIME
                if not isinstance(time, int):
                    raise ValueError(f"transition={transition!r}: the fourth item is the time in ms")
                return {"transition": run, "start": start, "end": end, "time": time}
            raise ValueError(
                f"transition={transition!r}: a pair (classes, ms) or three class strings "
                "(transition, start, end) with an optional time"
            )
        config: dict[str, t.Any] = dict(transition)
        unknown = set(config) - set(TransitionConfig.__annotations__)
        if unknown:
            raise ValueError(f"transition={transition!r}: unknown keys {sorted(unknown)}")
        for name in _PHASES:
            if name in config:
                config[name] = _class_names(config[name], name)
        if ("start" in config or "end" in config) and "time" not in config:
            config["time"] = DEFAULT_TRANSITION_TIME
        return config

    # === Visibility Commands ===

    def show(
        self,
        selector: Selector | None = None,
        *,
        transition: Transition = None,
        display: str | None = None,
        wait: bool = True,
    ) -> JS:
        """
        Show an element.

        Args:
            selector: CSS selector; the command acts on every element it matches.
                     If None, targets the event's current element.
            transition: CSS transition class(es) to apply during show.
                       Can be a string, tuple of (class, duration_ms),
                       or TransitionConfig dict.
            display: CSS display value to set (default: block).
            wait: False runs the rest of the chain at once, the transition going on (#239).

        Example:
            JS().show("#modal")
            JS().show("#panel", transition="fade-in")
            JS().show("#toast", transition=("slide-in", 300))
        """
        return self._add_command(
            "show",
            to=selector,
            transition=self._normalize_transition(transition),
            display=display,
            wait=_wait(wait),
        )

    def hide(
        self,
        selector: Selector | None = None,
        *,
        transition: Transition = None,
        wait: bool = True,
    ) -> JS:
        """
        Hide an element.

        Args:
            selector: CSS selector; the command acts on every element it matches.
                     If None, targets the event's current element.
            transition: CSS transition class(es) to apply during hide.

        Example:
            JS().hide("#modal")
            JS().hide(transition="fade-out")
        """
        return self._add_command(
            "hide",
            to=selector,
            transition=self._normalize_transition(transition),
            wait=_wait(wait),
        )

    def toggle(
        self,
        selector: Selector | None = None,
        *,
        show_transition: Transition = None,
        hide_transition: Transition = None,
        display: str | None = None,
        wait: bool = True,
    ) -> JS:
        """
        Toggle element visibility.

        Args:
            selector: CSS selector; the command acts on every element it matches.
            show_transition: Transition for showing the element.
            hide_transition: Transition for hiding the element.
            display: CSS display value when shown.

        Example:
            JS().toggle("#dropdown")
            JS().toggle("#menu", show_transition="slide-down",
                        hide_transition="slide-up")
        """
        return self._add_command(
            "toggle",
            to=selector,
            show=self._normalize_transition(show_transition),
            hide=self._normalize_transition(hide_transition),
            display=display,
            wait=_wait(wait),
        )

    # === CSS Class Commands ===

    def add_class(
        self,
        selector: Selector | None = None,
        classes: str | list[str] | None = None,
        *,
        transition: Transition = None,
        wait: bool = True,
    ) -> JS:
        """
        Add CSS class(es) to an element.

        Args:
            selector: CSS selector; the command acts on every element it matches.
            classes: Class name(s) to add. Can be a string or list.
            transition: Transition to apply while adding classes.

        Example:
            JS().add_class("#btn", "active")
            JS().add_class("#card", ["highlighted", "shadow"])
        """
        if isinstance(classes, list):
            classes = " ".join(classes)
        return self._add_command(
            "add_class",
            to=selector,
            classes=classes,
            transition=self._normalize_transition(transition),
            wait=_wait(wait),
        )

    def remove_class(
        self,
        selector: Selector | None = None,
        classes: str | list[str] | None = None,
        *,
        transition: Transition = None,
        wait: bool = True,
    ) -> JS:
        """
        Remove CSS class(es) from an element.

        Args:
            selector: CSS selector; the command acts on every element it matches.
            classes: Class name(s) to remove. Can be a string or list.
            transition: Transition to apply while removing classes.

        Example:
            JS().remove_class("#btn", "loading")
        """
        if isinstance(classes, list):
            classes = " ".join(classes)
        return self._add_command(
            "remove_class",
            to=selector,
            classes=classes,
            transition=self._normalize_transition(transition),
            wait=_wait(wait),
        )

    def toggle_class(
        self,
        selector: Selector | None = None,
        classes: str | list[str] | None = None,
        *,
        transition: Transition = None,
        wait: bool = True,
    ) -> JS:
        """
        Toggle CSS class(es) on an element.

        Args:
            selector: CSS selector; the command acts on every element it matches.
            classes: Class name(s) to toggle. Can be a string or list.
            transition: Transition to apply.

        Example:
            JS().toggle_class("#menu", "open")
        """
        if isinstance(classes, list):
            classes = " ".join(classes)
        return self._add_command(
            "toggle_class",
            to=selector,
            classes=classes,
            transition=self._normalize_transition(transition),
            wait=_wait(wait),
        )

    # === Transition Commands ===

    def transition(
        self,
        selector: Selector | None = None,
        transition: Transition = None,
        *,
        time: int | None = None,
        wait: bool = True,
    ) -> JS:
        """
        Apply a CSS transition to an element.

        The transition classes are applied, then removed after the duration.

        Args:
            selector: CSS selector; the command acts on every element it matches.
            transition: CSS class(es) to apply during the transition.
                       Can be a string, tuple of (class, duration_ms),
                       or TransitionConfig dict.
            time: Duration in milliseconds (overrides transition tuple).
            wait: False runs the rest of the chain at once, the transition going on (#239).

        Example:
            JS().transition("#card", "shake")
            JS().transition("#btn", "pulse", time=500)
            JS().transition("#modal", ("fade-in", 300))
            JS().transition("#card", ("ease-out duration-300", "opacity-0", "opacity-100"))
            JS().transition("#card", "shake", time=400, wait=False).push("save")
        """
        config = self._normalize_transition(transition) or {}
        if time is not None:
            config["time"] = time
        return self._add_command(
            "transition",
            to=selector,
            transition=config.get("transition"),
            start=config.get("start"),
            end=config.get("end"),
            time=config.get("time"),
            wait=_wait(wait),
        )

    # === Attribute Commands ===

    def set_attr(
        self,
        selector: Selector | None = None,
        attr: str | None = None,
        value: str | None = None,
    ) -> JS:
        """
        Set an attribute on an element.

        Args:
            selector: CSS selector; the command acts on every element it matches.
            attr: Attribute name.
            value: Attribute value.

        Example:
            JS().set_attr("#input", "disabled", "true")
        """
        return self._add_command("set_attr", to=selector, attr=attr, val=value)

    def remove_attr(
        self,
        selector: Selector | None = None,
        attr: str | None = None,
    ) -> JS:
        """
        Remove an attribute from an element.

        Args:
            selector: CSS selector; the command acts on every element it matches.
            attr: Attribute name to remove.

        Example:
            JS().remove_attr("#input", "disabled")
        """
        return self._add_command("remove_attr", to=selector, attr=attr)

    def toggle_attr(
        self,
        selector: Selector | None = None,
        attr: str | None = None,
        value: str | tuple[str, str] | list[str] = "",
    ) -> JS:
        """
        Toggle an attribute (#237). What it changes outlasts the renders after it, as ``set_attr``'s does.

        Args:
            selector: CSS selector, or ``JS.inner()``/``JS.closest()``; the command acts on every element it matches.
            attr: Attribute name.
            value: A ``str``: the attribute is removed when there, set to this when not. A pair ``(a, b)``:
                set to ``b`` when it is ``a``, else to ``a``.

        Example:
            JS().toggle_attr("#menu", "open")
            JS().toggle_attr(JS.closest("[aria-expanded]"), "aria-expanded", ("true", "false"))
        """
        if not attr:
            raise ValueError("toggle_attr() needs the attribute's name")
        if isinstance(value, (tuple, list)):
            if len(value) != 2 or not all(isinstance(v, str) for v in value):
                raise ValueError(f"toggle_attr() takes a value or a pair of values, not {value!r}")
            return self._add_command("toggle_attr", to=selector, attr=attr, vals=list(value))
        if not isinstance(value, str):
            raise ValueError(f"toggle_attr() takes a str value or a pair of them, not {value!r}")
        return self._add_command("toggle_attr", to=selector, attr=attr, val=value)

    def exec(self, attr: str, *, to: Selector | None = None) -> JS:
        """
        Run the chain an attribute holds, on each element ``to`` matches -- the binding's element by default (#237).

        The attribute holds a ``JS()`` chain as the page renders it: ``data-cancel="{{ this.cancel_js }}"``,
        or a binding's own attribute. Each runs from the element that holds it. A chain that, through
        ``exec``, comes back to an attribute it is already running is stopped there, with an error in
        the console.

        Example:
            JS().exec("data-cancel", to="#modal")
        """
        if not isinstance(attr, str) or not _ATTRIBUTE.fullmatch(attr):
            raise ValueError(f"exec() takes an attribute's name, not {attr!r}")
        return self._add_command("exec", attr=attr, to=to)

    def set_value(
        self,
        selector: Selector | None = None,
        value: str = "",
    ) -> JS:
        """
        Set the value property of an input element.

        Unlike set_attr, this sets the element's value property directly,
        which is necessary for input, textarea, and select elements.

        Args:
            selector: CSS selector; the command acts on every element it matches.
                     If None, targets the event's current element.
            value: Value to set (default: empty string to clear).

        Example:
            JS().set_value("#search", "")  # Clear input
            JS().set_value("#name", "John")  # Set value
        """
        return self._add_command("set_value", to=selector, value=value)

    # === Focus Commands ===

    def focus(self, selector: Selector | None = None) -> JS:
        """
        Focus an element.

        Args:
            selector: CSS selector; the first element it matches takes the focus.
                     If None, focuses the event's current element.

        Example:
            JS().focus("#search-input")
        """
        return self._add_command("focus", to=selector)

    def push_focus(self, selector: Selector | None = None) -> JS:
        """
        Remember an element to give the focus back to with ``pop_focus()`` (#238).

        Args:
            selector: CSS selector, or ``JS.inner()``/``JS.closest()``; the first element it
                matches. If None, the binding's element (for ``push_js``, the component's).

        Example:
            JS().push_focus().show("#modal").focus_first("#modal")
        """
        return self._add_command("push_focus", to=selector)

    def pop_focus(self) -> JS:
        """
        Focus the element ``push_focus()`` remembered last, and forget it (#238).

        An element that is gone, cannot take the focus now, or was remembered on the
        page before a boosted navigation (unless it is in a sticky component) is
        skipped: the one remembered before it is tried.

        Example:
            JS().hide("#modal").pop_focus()
        """
        return self._add_command("pop_focus")

    def focus_first(
        self,
        selector: Selector | None = None,
        *,
        input_only: bool = False,
    ) -> JS:
        """
        Focus the first element within a container that can take the focus from the keyboard:
        a link with an href, a button, a field, a ``[tabindex]`` that is not -1, ``contenteditable``,
        ``summary`` -- not one that is disabled, ``inert``, hidden or inside something hidden (#238).

        Args:
            selector: CSS selector for the container (the first it matches).
            input_only: If True, only an ``input`` (not hidden) or ``textarea``.

        Example:
            JS().focus_first("#form")
            JS().focus_first("#modal", input_only=True)
        """
        return self._add_command(
            "focus_first",
            to=selector,
            input_only=input_only if input_only else None,
        )

    # === Server Communication ===

    def push(
        self,
        event: str,
        *,
        value: dict[str, t.Any] | None = None,
        target: str | None = None,
    ) -> JS:
        """
        Push an event to the server.

        This triggers a server-side event handler on the component.

        Args:
            event: Name of the server event handler to call.
            value: Additional data to send with the event.
            target: CSS selector of the component to target.
                   Defaults to the current component.

        Example:
            JS().push("save")
            JS().push("delete", value={"id": 123})
            JS().push("submit", target="#other-component")
        """
        if isinstance(target, RelativeSelector):
            raise TypeError("push(target=) names a component by a CSS selector over the document, not JS.inner/closest")
        return self._add_command(
            "push",
            event=event,
            value=value,
            target=target,
        )

    # === Browser Commands ===

    def navigate(
        self,
        url: str,
        *,
        replace: bool = False,
    ) -> JS:
        """
        Navigate to a URL.

        Args:
            url: The URL to navigate to.
            replace: If True, replace the current history entry
                    instead of pushing a new one.

        Example:
            JS().navigate("/dashboard")
            JS().navigate("/login", replace=True)

        Raises:
            DisallowedRedirect: the URL is not one a navigation may go to --
                a scheme other than http or https, a control character, a
                backslash, whitespace around it (#262).
        """
        from .core.destinations import check_destination

        check_destination(url)
        return self._add_command(
            "navigate",
            url=url,
            replace=replace if replace else None,
        )

    def dispatch(
        self,
        event: str,
        *,
        to: Selector | None = None,
        detail: dict[str, t.Any] | None = None,
        bubbles: bool = True,
    ) -> JS:
        """
        Dispatch a custom DOM event.

        Args:
            event: Name of the custom event to dispatch.
            to: CSS selector; the event goes to every element it matches.
                Defaults to the current element.
            detail: Custom data to include in the event.
            bubbles: Whether the event bubbles up through the DOM.

        Example:
            JS().dispatch("my-event")
            JS().dispatch("notify", detail={"message": "Done!"})
        """
        return self._add_command(
            "dispatch",
            event=event,
            to=to,
            detail=detail,
            bubbles=bubbles if not bubbles else None,  # Only include if False
        )

    # === Serialization ===

    def to_json(self) -> str:
        """
        Serialize commands to JSON string.

        Returns:
            JSON string representation of all commands.
        """
        return json.dumps(self._commands, cls=_Encoder)

    def __str__(self) -> str:
        """Return JSON representation for template use."""
        return self.to_json()

    def __repr__(self) -> str:
        return f"JS({self._commands!r})"

    def __bool__(self) -> bool:
        """Return True if there are any commands."""
        return bool(self._commands)

    def __len__(self) -> int:
        """Return the number of commands."""
        return len(self._commands)
