"""
Phoenix LiveView.JS style client-side command builder.

This module provides a fluent API for building client-side JavaScript commands
that can be executed in the browser without a server round-trip, or combined
with server events.

Example usage in templates:
    <button {% on "click" JS().toggle("#modal").push("save") %}>Save</button>
    <div {% on "click" JS().hide(transition=("fade-out", 300)) %}></div>
"""

from __future__ import annotations

import json
import typing as t

from django.core.serializers.json import DjangoJSONEncoder

__all__ = ("JS",)


class TransitionConfig(t.TypedDict, total=False):
    """Configuration for CSS transitions."""

    transition: str
    time: int
    to: str


Transition = str | tuple[str, int] | TransitionConfig | None
Selector = str


class JS:
    """
    Client-side command builder for Wireview components.

    Provides a fluent interface for building JavaScript commands that execute
    in the browser. Commands can be chained and are serialized to JSON for
    transmission to the client.

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
        self._commands.append(cmd)
        return self

    @staticmethod
    def _normalize_transition(
        transition: Transition,
    ) -> dict[str, t.Any] | None:
        """Normalize transition config to a dictionary."""
        if transition is None:
            return None
        if isinstance(transition, str):
            return {"transition": transition}
        if isinstance(transition, tuple):
            return {"transition": transition[0], "time": transition[1]}
        return dict(transition)

    # === Visibility Commands ===

    def show(
        self,
        selector: Selector | None = None,
        *,
        transition: Transition = None,
        display: str | None = None,
    ) -> JS:
        """
        Show an element.

        Args:
            selector: CSS selector for the target element.
                     If None, targets the event's current element.
            transition: CSS transition class(es) to apply during show.
                       Can be a string, tuple of (class, duration_ms),
                       or TransitionConfig dict.
            display: CSS display value to set (default: block).

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
        )

    def hide(
        self,
        selector: Selector | None = None,
        *,
        transition: Transition = None,
    ) -> JS:
        """
        Hide an element.

        Args:
            selector: CSS selector for the target element.
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
        )

    def toggle(
        self,
        selector: Selector | None = None,
        *,
        show_transition: Transition = None,
        hide_transition: Transition = None,
        display: str | None = None,
    ) -> JS:
        """
        Toggle element visibility.

        Args:
            selector: CSS selector for the target element.
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
        )

    # === CSS Class Commands ===

    def add_class(
        self,
        selector: Selector | None = None,
        classes: str | list[str] | None = None,
        *,
        transition: Transition = None,
    ) -> JS:
        """
        Add CSS class(es) to an element.

        Args:
            selector: CSS selector for the target element.
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
        )

    def remove_class(
        self,
        selector: Selector | None = None,
        classes: str | list[str] | None = None,
        *,
        transition: Transition = None,
    ) -> JS:
        """
        Remove CSS class(es) from an element.

        Args:
            selector: CSS selector for the target element.
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
        )

    def toggle_class(
        self,
        selector: Selector | None = None,
        classes: str | list[str] | None = None,
        *,
        transition: Transition = None,
    ) -> JS:
        """
        Toggle CSS class(es) on an element.

        Args:
            selector: CSS selector for the target element.
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
            selector: CSS selector for the target element.
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
            selector: CSS selector for the target element.
            attr: Attribute name to remove.

        Example:
            JS().remove_attr("#input", "disabled")
        """
        return self._add_command("remove_attr", to=selector, attr=attr)

    # === Focus Commands ===

    def focus(self, selector: Selector | None = None) -> JS:
        """
        Focus an element.

        Args:
            selector: CSS selector for the element to focus.
                     If None, focuses the event's current element.

        Example:
            JS().focus("#search-input")
        """
        return self._add_command("focus", to=selector)

    def focus_first(
        self,
        selector: Selector | None = None,
        *,
        input_only: bool = False,
    ) -> JS:
        """
        Focus the first focusable element within a container.

        Args:
            selector: CSS selector for the container.
            input_only: If True, only focus input elements.

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
        target: Selector | None = None,
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
        """
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
            to: CSS selector of the target element.
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
        return json.dumps(self._commands, cls=DjangoJSONEncoder)

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
