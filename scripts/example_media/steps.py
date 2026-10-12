"""What a scene is made of: plain data, so more than one thing can play it (#213).

The generator plays a scene in a recorded browser (record.py); the demo site (#214)
can play the same steps against its own server as a smoke check, with nothing
recorded. A step names an action, what it acts on and, in a scene with more than one
window, whose window it happens in. Nothing here imports Django or Playwright.

Targets are Playwright selectors -- CSS, ``role=button[name="Vote"]``, ``text=...``
-- because they are strings and any Playwright client reads them the same way.
A ``{name}`` in a path or a text is filled from what the scene's seed returned
(a room's URL, which has an id the seed only learns when it creates the room).
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field

#: What a step may do. ``live`` waits until the components matching the target have
#: joined; ``see`` waits until the target contains the text; ``pause`` waits ``ms``.
ACTIONS = ("visit", "live", "click", "type", "press", "select", "hover", "scroll", "see", "pause")


@dataclass(frozen=True)
class Step:
    action: str
    target: str = ""
    text: str = ""
    #: The window it happens in: one of the scene's actors, or the first when empty.
    who: str = ""
    ms: int = 0

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unknown action {self.action!r}; one of {ACTIONS}")


def visit(path: str, who: str = "") -> Step:
    return Step("visit", path, who=who)


def live(selector: str = '[data-is-live="true"]', who: str = "") -> Step:
    return Step("live", selector, who=who)


def click(selector: str, who: str = "") -> Step:
    return Step("click", selector, who=who)


def type_(selector: str, text: str, who: str = "") -> Step:
    """Empty a field and type into it key by key, at the pace of a person."""
    return Step("type", selector, text, who=who)


def press(selector: str, key: str, who: str = "") -> Step:
    return Step("press", selector, key, who=who)


def select(selector: str, value: str, who: str = "") -> Step:
    """Choose an option of a ``<select>`` by its value or its label."""
    return Step("select", selector, value, who=who)


def hover(selector: str, who: str = "") -> Step:
    return Step("hover", selector, who=who)


def scroll(selector: str, pixels: int, who: str = "") -> Step:
    """Scroll the element (or the window, for ``"window"``) by ``pixels``; negative is up."""
    return Step("scroll", selector, str(pixels), who=who)


def see(selector: str, text: str = "", who: str = "") -> Step:
    """Wait until the target is visible and, when given, contains the text."""
    return Step("see", selector, text, who=who)


def pause(ms: int) -> Step:
    return Step("pause", ms=ms)


@dataclass(frozen=True)
class Actor:
    """A window of its own: its own cookies, so its own signed-in user."""

    name: str
    #: Shown in the corner of the window when the scene has more than one.
    label: str = ""
    color: str = "#2563eb"


@dataclass(frozen=True)
class Scene:
    #: The example directory, and the name of its media files.
    example: str
    #: One line saying what the clip shows, for the reader and the report.
    caption: str
    #: Not recorded: signing in, going to the page the clip starts on.
    prepare: tuple[Step, ...]
    #: Recorded, and ended by the screenshot.
    play: tuple[Step, ...]
    actors: tuple[Actor, ...] = (Actor("visitor"),)
    #: Called with the database empty; returns the values the steps' ``{name}`` refer to.
    seed: t.Callable[[], dict[str, str]] | None = None
    #: One window's size. Two windows are put side by side, each this size.
    viewport: tuple[int, int] = (960, 600)
    #: Anything else a player needs to know, by name.
    extra: dict[str, t.Any] = field(default_factory=dict)

    @property
    def shared(self) -> bool:
        """More than one window: what one does reaches the other."""
        return len(self.actors) > 1

    def actor(self, name: str) -> Actor:
        return next(a for a in self.actors if a.name == name) if name else self.actors[0]
