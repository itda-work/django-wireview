"""Django system checks for wireview's silent failure modes.

Most of wireview's traps do not raise: a sync handler only breaks when a client
calls it, a missing ``wireview.min.js`` just leaves the page inert, ``USE_HMIN``
quietly degrades partial diffs. Registering them here means ``manage.py check``,
``runserver`` and CI report them without anyone remembering a new command.

Every check reuses the dispatcher's own rules (``ComponentRepository``) rather
than reimplementing them, so the checks cannot disagree with what the server
actually does. False positives are the real risk here: a check nobody trusts is
worse than no check, so everything starts at ``Warning`` level and the
production-only check is registered as a deploy check.
"""

import asyncio
import typing as t

from django.core.checks import CheckMessage, Warning, register

if t.TYPE_CHECKING:
    from .core.component import Component

WIREVIEW_TAG = "wireview"

#: Framework callbacks that wireview awaits. A sync override never runs.
LIFECYCLE_METHODS = ("joined", "update", "destroy")

#: The client bundle referenced by ``{% wireview_header %}``.
BUNDLE_STATIC_PATH = "wireview/wireview.min.js"


def iter_component_classes() -> t.Iterator[type["Component"]]:
    """Every publicly registered component class, each yielded once."""
    from .component import Component

    seen: set[int] = set()
    for cls in Component._by_fqn.values():
        if id(cls) in seen:
            continue
        seen.add(id(cls))
        yield cls


def iter_exposed_handlers(cls: type["Component"]) -> t.Iterator[tuple[str, t.Callable[..., t.Any]]]:
    """Yield the methods a client can call on ``cls``, newest rule first.

    This is the dispatcher's rule, not a copy of it: ``dispatch_event`` accepts
    exactly the names that pass both predicates.
    """
    from .repository import ComponentRepository

    for name in sorted(dir(cls)):
        if not ComponentRepository._is_valid_event_handler(name):
            continue
        attr = getattr(cls, name, None)
        if not callable(attr):
            continue
        if not ComponentRepository._is_user_defined_method(cls, name):
            continue
        yield name, attr


def _unwrap(func: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
    """Strip the ``validate_call`` wrapper that ``__init_subclass__`` applies."""
    return getattr(func, "__wrapped__", func)


def check_async_handlers(app_configs, **kwargs) -> list[CheckMessage]:
    """W001: an exposed handler that is not a coroutine function."""
    messages = []
    for cls in iter_component_classes():
        for name, func in iter_exposed_handlers(cls):
            if asyncio.iscoroutinefunction(_unwrap(func)):
                continue
            messages.append(
                Warning(
                    f"Event handler '{cls._fqn}.{name}' is not async.",
                    hint=(
                        f"The client can call '{name}', and wireview awaits the result, "
                        f"so the call fails with TypeError. Make it 'async def', or rename "
                        f"it to '_{name}' if it is an internal helper rather than a handler."
                    ),
                    obj=cls,
                    id="wireview.W001",
                )
            )
    return messages


def check_async_lifecycle(app_configs, **kwargs) -> list[CheckMessage]:
    """W002: a sync override of a lifecycle callback wireview awaits."""
    messages = []
    for cls in iter_component_classes():
        for name in LIFECYCLE_METHODS:
            func = cls.__dict__.get(name)
            if func is None or not callable(func):
                continue
            if asyncio.iscoroutinefunction(_unwrap(func)):
                continue
            messages.append(
                Warning(
                    f"Lifecycle method '{cls._fqn}.{name}' is not async.",
                    hint=f"wireview awaits '{name}()'. Declare it as 'async def {name}'.",
                    obj=cls,
                    id="wireview.W002",
                )
            )
    return messages


def check_component_name_collisions(app_configs, **kwargs) -> list[CheckMessage]:
    """W003: two component classes registered under the same simple name."""
    from .component import Component

    by_name: dict[str, list[type[Component]]] = {}
    for cls in iter_component_classes():
        by_name.setdefault(cls._name, []).append(cls)

    messages = []
    for name, classes in sorted(by_name.items()):
        if len(classes) < 2:
            continue
        fqns = ", ".join(sorted(cls._fqn for cls in classes))
        messages.append(
            Warning(
                f"Component name '{name}' is registered by more than one class: {fqns}.",
                hint=(
                    "Only one of them resolves by simple name. Reference them as "
                    "'app:Name' or by FQN in templates, or rename one class."
                ),
                id="wireview.W003",
            )
        )
    return messages


def check_client_bundle(app_configs, **kwargs) -> list[CheckMessage]:
    """W004: the built client bundle is not on the staticfiles path."""
    from django.apps import apps

    if not apps.is_installed("django.contrib.staticfiles"):
        return []

    from django.contrib.staticfiles import finders

    if finders.find(BUNDLE_STATIC_PATH) is not None:
        return []

    return [
        Warning(
            f"'{BUNDLE_STATIC_PATH}' was not found by the staticfiles finders.",
            hint=(
                "{% wireview_header %} loads it, so without it no wireview JavaScript runs "
                "and the page silently stays static. Run 'make build-js' (the bundle is a "
                "build artifact and is gitignored)."
            ),
            id="wireview.W004",
        )
    ]


def check_hmin(app_configs, **kwargs) -> list[CheckMessage]:
    """W005: django-hmin strips the comment markers partial diffs rely on."""
    from . import settings as wireview_settings

    if not (wireview_settings.USE_HMIN and wireview_settings.USE_HTML_DIFF):
        return []

    return [
        Warning(
            "WIREVIEW['USE_HMIN'] is on, which disables partial HTML diffs.",
            hint=(
                "django-hmin removes the HTML comments wireview uses as diff markers, so "
                "updates degrade to token diffs. Measure the bandwidth trade-off before "
                "keeping it on, or set USE_HMIN to False."
            ),
            id="wireview.W005",
        )
    ]


def check_channel_layer(app_configs, **kwargs) -> list[CheckMessage]:
    """W006 (deploy): the in-memory layer cannot broadcast across processes."""
    from django.conf import settings

    layers = getattr(settings, "CHANNEL_LAYERS", None) or {}
    backend = (layers.get("default") or {}).get("BACKEND", "")
    if "InMemoryChannelLayer" not in backend:
        return []

    return [
        Warning(
            "The default channel layer is InMemoryChannelLayer.",
            hint=(
                "Broadcasts only reach connections in the same process, and nothing raises "
                "when they do not, so multi-process deployments lose messages silently. Use "
                "channels_redis or channels-nats. This is harmless on a single process, which "
                "is why it is only reported by 'manage.py check --deploy'."
            ),
            id="wireview.W006",
        )
    ]


def check_on_mount_hooks(app_configs, **kwargs) -> list[CheckMessage]:
    """W007: an ``_on_mount`` entry wireview cannot call.

    The hooks are an authorization boundary (the documented first example is an
    authentication guard), and a hook wireview cannot call is skipped in silence,
    so the component mounts unprotected.
    """
    messages = []
    for cls in iter_component_classes():
        for hook_class in cls._on_mount:
            hook_name = getattr(hook_class, "__name__", repr(hook_class))
            on_mount = getattr(hook_class, "on_mount", None)
            if isinstance(on_mount, staticmethod):
                on_mount = on_mount.__func__
            if on_mount is None or not callable(on_mount):
                messages.append(
                    Warning(
                        f"'{hook_name}' in {cls._fqn}._on_mount has no 'on_mount' method.",
                        hint=(
                            "wireview skips such an entry without a word, so a hook meant as a "
                            "guard lets the component mount. Define "
                            "'async def on_mount(component, params, session)' on it."
                        ),
                        obj=cls,
                        id="wireview.W007",
                    )
                )
                continue
            if asyncio.iscoroutinefunction(_unwrap(on_mount)):
                continue
            messages.append(
                Warning(
                    f"'{hook_name}.on_mount' in {cls._fqn}._on_mount is not async.",
                    hint=(
                        "wireview awaits every on_mount hook, so a sync one fails with "
                        "TypeError while the component is mounting. Declare it as "
                        "'async def on_mount'."
                    ),
                    obj=cls,
                    id="wireview.W007",
                )
            )
    return messages


def register_checks() -> None:
    """Register every check. Called from ``WireviewConfig.ready()``."""
    register(check_async_handlers, WIREVIEW_TAG)
    register(check_async_lifecycle, WIREVIEW_TAG)
    register(check_component_name_collisions, WIREVIEW_TAG)
    register(check_client_bundle, WIREVIEW_TAG)
    register(check_hmin, WIREVIEW_TAG)
    register(check_on_mount_hooks, WIREVIEW_TAG)
    register(check_channel_layer, WIREVIEW_TAG, deploy=True)
