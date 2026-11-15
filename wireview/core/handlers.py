"""Which methods of a component a client may call: the one rule.

The dispatcher (``ComponentRepository.dispatch_event``), the checks and the
tooling ask it, and so does ``_validate_handlers`` when it decides what to wrap
in ``validate_call``. Wrapping by a rule of its own once wrapped framework
methods no client can call, and an annotation pydantic rejects there broke the
import of the whole package (#127).
"""

from __future__ import annotations

import inspect
import typing as t

# Packages whose classes are framework surface, never client-callable handlers.
_FRAMEWORK_ROOTS = ("wireview", "pydantic")


def is_framework_class(cls: type) -> bool:
    """Whether a class comes from the framework rather than from user code."""
    if cls is object:
        return True
    return is_framework_module(getattr(cls, "__module__", "") or "")


def is_framework_module(module: str) -> bool:
    """Whether a module (by its dotted name) is the framework's."""
    return module.partition(".")[0] in _FRAMEWORK_ROOTS


def is_valid_event_handler(command: str) -> bool:
    """Whether ``command`` can name an event handler at all.

    - Must not be empty
    - Must not start with underscore (private/protected methods)
    - Must be a valid Python identifier
    """
    return bool(command) and not command.startswith("_") and command.isidentifier()


def is_user_defined_method(component: t.Any, command: str) -> bool:
    """Whether a method name belongs to the user's own component code.

    Accepts an instance or a class, so tooling can ask the same question
    without building a component.

    A name is exposed only when every class in the MRO that defines it is a
    user class. Any name owned by a framework class blocks the call, even if
    a subclass overrides it, because framework names are API and lifecycle
    surface rather than client events:

    - Pydantic BaseModel methods (model_validate, model_dump, model_post_init)
    - Component internals and lifecycle (new, joined, handle_async, destroy)
    - LiveComponent API and lifecycle (send_to_parent, update, update_many)
    """
    found_on_user_class = False
    component_class = component if isinstance(component, type) else type(component)

    for cls in component_class.__mro__:
        if command not in cls.__dict__:
            continue
        if isinstance(cls.__dict__[command], type):
            # A nested class -- ``class Meta:`` above all -- is callable and
            # not a handler: a client naming it would instantiate it (#99).
            return False
        if is_framework_class(cls):
            # The name is framework surface, wherever it is also overridden.
            # Pydantic-generated methods (model_post_init) land here too:
            # the metaclass injects them into the user class, but BaseModel
            # owns the name.
            return False
        found_on_user_class = True

    return found_on_user_class


def is_client_callable(component: t.Any, command: str) -> bool:
    """Whether a client may call ``command`` on ``component`` (a class or an instance)."""
    return (
        is_valid_event_handler(command)
        and callable(getattr(component, command, None))
        and is_user_defined_method(component, command)
    )


#: What a user_event's ``meta`` carries, by the handler parameter that takes it (#244)
EVENT_META_PARAMETERS = ("_target", "_unused", "_bad_input")


def event_meta(meta: t.Any) -> dict[str, t.Any]:
    """The values a user_event's ``meta`` gives the handler parameters that ask for them.

    ``_target`` is the name of the field the event came from, or ``None`` (a
    click, a submit); ``_unused`` the names of the fields the event sends that
    the user has neither changed nor left yet; ``_bad_input`` the names of the
    fields whose input the browser could not read as a value -- "1e" typed in a
    number field, which it sends as "". A client older than ``EVENT_META_SINCE``
    sends none of it, and the parameters get these defaults. Anything else in
    ``meta`` is dropped: it comes from the page.
    """
    meta = meta if isinstance(meta, dict) else {}
    target = meta.get("target")

    def names(value: t.Any) -> list[str]:
        return [name for name in value if isinstance(name, str)] if isinstance(value, list) else []

    return {
        "_target": target if isinstance(target, str) else None,
        "_unused": names(meta.get("unused")),
        "_bad_input": names(meta.get("bad_input")),
    }


def named_event_meta(handler: t.Callable[..., t.Any], meta: t.Mapping[str, t.Any] | None) -> dict[str, t.Any]:
    """The part of ``meta`` that ``handler`` names a parameter for.

    Only a parameter of that very name takes it, never ``**kwargs``: a handler
    that passes the form on (``Model(**data)``) must not find keys in it that no
    field of the form has. A form field called ``_target`` gives way to it.
    """
    if not meta:
        return {}
    try:
        parameters = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return {}
    return {
        name: meta[name]
        for name in EVENT_META_PARAMETERS
        if name in meta
        and name in parameters
        and parameters[name].kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
