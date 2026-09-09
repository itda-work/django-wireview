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
import os
import typing as t
from pathlib import Path

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


def check_live_sessions(app_configs, **kwargs) -> list[CheckMessage]:
    """W010: a component's ``_live_sessions`` does not line up with what the project declares.

    Two mistakes, both silent, both only visible once somebody looks at two files
    at once.

    The first is a name that does not resolve. A page declaring the boundary is
    refused at join with "unknown live_session" and reloads, which reads as a
    signing problem rather than as a typo.

    The second is the boundary being opt-in. A component guarded only by its own
    ``_on_mount`` hooks mounts anywhere, including on a page with no policy: the
    hooks run, but nothing says the component belongs behind the boundary the
    project drew, so a state signed on a public page mounts it there. That is
    reported only once the project declares a live_session at all -- before that
    there is nothing to belong to, and every component is where it always was.
    """
    from . import settings as wireview_settings
    from .core.live_session import all_live_sessions

    declared = all_live_sessions()
    messages: list[CheckMessage] = []

    if declared:
        messages.extend(_check_request_context_processor())

    if declared and wireview_settings.STATE_ACCEPT_LEGACY:
        messages.append(
            Warning(
                "WIREVIEW['STATE_ACCEPT_LEGACY'] is on while a live_session is declared, "
                "so old state tokens are refused anyway.",
                hint=(
                    "A pre-v2 token names no boundary, and nothing in it says whether its "
                    "page had one, so accepting it could let a page-level policy be skipped "
                    "entirely. The rollout window and the boundary cannot both be open: open "
                    "tabs reload once when the boundary ships. Drop the setting to say so on "
                    "purpose, or land it in a deploy before the first live_session."
                ),
                id="wireview.W010",
            )
        )

    for cls in iter_component_classes():
        for name in sorted(cls._live_sessions):
            if name in declared:
                continue
            messages.append(
                Warning(
                    f"{cls._fqn}._live_sessions names '{name}', which no live_session declares.",
                    hint=(
                        "A page carrying that name is refused at join and the browser reloads, "
                        f"which looks like a signing failure. Declared: {sorted(declared) or 'none'}. "
                        "Check the spelling, or make sure the module calling live_session() is "
                        "imported (wireview autodiscovers 'live_sessions.py' in each app)."
                    ),
                    obj=cls,
                    id="wireview.W010",
                )
            )
        if declared and cls._on_mount and not cls._live_sessions:
            messages.append(
                Warning(
                    f"{cls._fqn} guards itself with _on_mount but declares no _live_sessions.",
                    hint=(
                        "Its hooks run wherever it is mounted, including on a page outside "
                        "every boundary this project draws -- so a state signed on such a page "
                        'mounts it there. Add _live_sessions = {"<name>"} to say where it '
                        "belongs, or leave it empty on purpose if it really is mountable anywhere."
                    ),
                    obj=cls,
                    id="wireview.W010",
                )
            )

    return messages


def _check_request_context_processor() -> list[CheckMessage]:
    """W010: a boundary is declared but templates cannot see the request.

    ``{% wireview_header %}`` and ``{% component %}`` both read the page's
    boundary off ``context["request"]``, which is only there when
    ``django.template.context_processors.request`` is enabled. Without it the
    whole feature turns itself off without a word: the header publishes an empty
    name so the browser stops treating any navigation as a boundary crossing,
    every component signs a state that names no boundary, and a component that
    declared ``_live_sessions`` disappears from the page it belongs on.

    The view decorator still refuses unauthorized requests, so this is not an
    open door -- it is the rest of the boundary quietly missing.
    """
    from django.conf import settings as django_settings

    processor = "django.template.context_processors.request"
    offenders = [
        engine.get("NAME") or engine.get("BACKEND", "<unnamed>")
        for engine in getattr(django_settings, "TEMPLATES", [])
        if engine.get("BACKEND") == "django.template.backends.django.DjangoTemplates"
        and processor not in engine.get("OPTIONS", {}).get("context_processors", [])
    ]
    if not offenders:
        return []
    return [
        Warning(
            f"A live_session is declared, but {processor} is not enabled for: {', '.join(offenders)}.",
            hint=(
                "The template tags read the page's boundary off the request, so without this "
                "processor the header publishes an empty name, every state is signed with no "
                "boundary, and a component that declared _live_sessions vanishes from the page "
                "it belongs on. Nothing raises. Add it to the engine's "
                "OPTIONS['context_processors']."
            ),
            id="wireview.W010",
        )
    ]


def check_upload_temp_dir(app_configs, **kwargs) -> list[CheckMessage]:
    """W008: ``UPLOAD_TEMP_DIR`` points somewhere uploads cannot be written.

    Nothing touches the directory until the first chunk arrives, so a typo or a
    volume that was never mounted looks fine at deploy time and then fails per
    upload, on a code path the user only sees as an upload that never finishes.

    The check has no side effects: a directory that does not exist yet is judged
    by its nearest existing ancestor rather than by creating it.
    """
    from . import settings as wireview_settings

    configured = wireview_settings.UPLOAD_TEMP_DIR
    if configured is None:
        return []

    def flag(reason: str, hint: str | None = None) -> list[CheckMessage]:
        return [
            Warning(
                f"WIREVIEW['UPLOAD_TEMP_DIR'] = {configured!r} {reason}.",
                hint=hint
                or (
                    "wireview creates every chunked upload's temp file there. Nothing reads "
                    "the setting until the first upload, so the misconfiguration surfaces as "
                    "uploads that fail one by one rather than at startup. Point it at a "
                    "writable directory, or unset it to use the system temp dir."
                ),
                id="wireview.W008",
            )
        ]

    if not configured:
        return flag(
            "is empty, so uploads go to the system temp dir",
            hint=(
                "An empty value usually means an environment variable nobody set "
                "(os.environ.get('UPLOAD_TEMP_DIR', '')). wireview treats it as unset rather "
                "than as the working directory, so nothing breaks, but the shared volume the "
                "setting was meant to name is not being used. Set a path, or drop the key."
            ),
        )

    path = Path(configured)
    if path.exists():
        if not path.is_dir():
            return flag("exists but is not a directory")
        if not os.access(path, os.W_OK | os.X_OK):
            return flag("is a directory wireview cannot write to")
        return []

    # The chunk store is created on first use, so the question is whether the
    # nearest existing ancestor lets it.
    ancestor = path.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK | os.X_OK):
        return flag("does not exist and cannot be created")
    return []


def check_signing_key(app_configs, **kwargs) -> list[CheckMessage]:
    """W009: ``SIGNING_KEY`` is set to something that silently means "unset".

    ``Signer(key=...)`` treats a falsy key as absent and reaches for
    ``SECRET_KEY``, so an environment variable nobody set leaves wireview signing
    with Django's key while the settings file says otherwise. Nothing breaks --
    which is the problem: rotating ``SECRET_KEY`` then invalidates every upload
    in flight and the ``data-state`` of every open page, exactly what the
    dedicated key was meant to prevent (#83).

    Fallbacks without a key are the same mistake read the other way: they only
    take effect on the key wireview actually signs with.
    """
    from . import settings as wireview_settings

    configured = wireview_settings.SIGNING_KEY
    fallbacks = wireview_settings.SIGNING_KEY_FALLBACKS
    messages: list[CheckMessage] = []

    if configured is not None and not configured:
        messages.append(
            Warning(
                f"WIREVIEW['SIGNING_KEY'] = {configured!r} is empty, so wireview signs with SECRET_KEY.",
                hint=(
                    "An empty value usually means an environment variable nobody set. Django's "
                    "signers treat a falsy key as absent, so this reads as 'use SECRET_KEY' "
                    "rather than as an error. Set a key, or drop the setting to say so on purpose."
                ),
                id="wireview.W009",
            )
        )

    if configured is None and fallbacks:
        messages.append(
            Warning(
                "WIREVIEW['SIGNING_KEY_FALLBACKS'] is set while WIREVIEW['SIGNING_KEY'] is not.",
                hint=(
                    "Fallbacks are checked against the key wireview signs with, which without "
                    "SIGNING_KEY is Django's SECRET_KEY. Set SIGNING_KEY too, or use "
                    "SECRET_KEY_FALLBACKS instead."
                ),
                id="wireview.W009",
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
    register(check_live_sessions, WIREVIEW_TAG)
    register(check_upload_temp_dir, WIREVIEW_TAG)
    register(check_signing_key, WIREVIEW_TAG)
    register(check_channel_layer, WIREVIEW_TAG, deploy=True)
