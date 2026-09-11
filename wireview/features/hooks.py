"""Finding the JavaScript hook files an app ships (GAP-032, #71).

A hook lives in three places -- ``wire-hook="X"`` in a template, a handler on
the component, and ``window.wireview.hooks.X = {...}`` somewhere in the browser.
The third is the one that goes missing, and it goes missing silently: the client
warns to the console and the page looks fine.

So an app can put its hook files where this module will find them, and
``{% wireview_header %}`` loads them. What goes away is the wiring -- which
script tag, in which template, after which other one. The file still names its
own hook, because taking that away needs ES modules, and an async import puts
the "registered before the first join" guarantee back onto a race
(``docs/design/colocated-hooks.md`` §4).

**The list is a property of the project, not of the page.** Boosted navigation
replaces the body, so a script in a fetched page's ``<head>`` never runs: a page
that loaded only its own hooks would leave the next page inside the same
live_session without them -- fine on first load, broken after a move. Every page
therefore carries all of them, which also means this is settled once at startup
and never reads the request.
"""

from __future__ import annotations

import re
import typing as t
from functools import lru_cache
from pathlib import Path

#: Directory inside an app's static namespace that holds its hook files:
#: ``<app>/static/<app_label>/hooks/*.js``.
HOOK_DIRECTORY = "hooks"

#: ``window.wireview.hooks.Name = ...`` and ``wireview.hooks["Name"] = ...``.
#: A heuristic, and deliberately a loose one: it exists to tell ``manage.py
#: check`` what a file provides, and a name it cannot see becomes a warning
#: about a hook that does work, never a refusal to load one.
_REGISTRATION = re.compile(r"""hooks\s*(?:\.\s*([A-Za-z_$][\w$]*)|\[\s*["']([^"']+)["']\s*\])\s*=""")


@lru_cache(maxsize=1)
def hook_files() -> tuple[str, ...]:
    """Every app's hook files, as static paths, in a stable order.

    The paths are logical (``myapp/hooks/chart.js``), not filesystem ones, so
    ``{% static %}`` resolves them -- which is what keeps ``collectstatic`` and
    hashed storage working without this module knowing anything about either.

    Ordered by the app registry and then by filename, so the tags a page renders
    do not move between deploys.
    """
    from django.apps import apps

    found: list[str] = []
    for config in apps.get_app_configs():
        directory = Path(config.path) / "static" / config.label / HOOK_DIRECTORY
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.js")):
            found.append(f"{config.label}/{HOOK_DIRECTORY}/{path.name}")
    return tuple(found)


@lru_cache(maxsize=1)
def hook_names() -> dict[str, str]:
    """Hook name -> the static path of the file that registers it.

    Read out of the files themselves, because the name is written there and
    nowhere else. Only ``manage.py check`` needs this; nothing at request time
    does, and nothing here decides whether a file is loaded.
    """
    from django.apps import apps

    names: dict[str, str] = {}
    by_label = {config.label: Path(config.path) for config in apps.get_app_configs()}
    for static_path in hook_files():
        label, _, tail = static_path.partition("/")
        source = by_label[label] / "static" / static_path
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable file, nothing to say about it
            continue
        for dotted, quoted in _REGISTRATION.findall(text):
            names.setdefault(dotted or quoted, static_path)
    return names


def required_hook_names() -> dict[str, list[str]]:
    """Hook name -> the templates that ask for it, by scanning ``wire-hook``.

    A regex over template sources, so a name built at render time is invisible
    here. That is why what uses this is a warning: a check that cannot see every
    name must never be the thing that fails a build.
    """
    wanted: dict[str, list[str]] = {}
    for directory in _template_roots():
        for path in Path(directory).rglob("*.html"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover
                continue
            for value in re.findall(r"""wire-hook\s*=\s*["']([^"']+)["']""", text):
                for name in value.split():
                    if "{" in name:  # ``wire-hook="{{ name }}"`` -- not ours to judge
                        continue
                    wanted.setdefault(name, []).append(str(path))
    return wanted


def _template_roots() -> t.Iterator[Path]:
    """Where a project's templates live: the engines' DIRS, and each app's."""
    from django.conf import settings as django_settings
    from django.template.utils import get_app_template_dirs

    seen: set[Path] = set()
    for engine in getattr(django_settings, "TEMPLATES", []):
        for directory in engine.get("DIRS", []):
            root = Path(directory)
            if root.is_dir() and root not in seen:
                seen.add(root)
                yield root
        if engine.get("APP_DIRS"):
            for directory in get_app_template_dirs("templates"):
                root = Path(directory)
                if root.is_dir() and root not in seen:
                    seen.add(root)
                    yield root


def reset_caches() -> None:
    """Forget what was discovered. For tests that move apps around."""
    hook_files.cache_clear()
    hook_names.cache_clear()
