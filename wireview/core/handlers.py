"""Which methods of a component a client may call: the one rule.

The dispatcher (``ComponentRepository.dispatch_event``), the checks and the
tooling ask it, and so does ``_validate_handlers`` when it decides what to wrap
in ``validate_call``. Wrapping by a rule of its own once wrapped framework
methods no client can call, and an annotation pydantic rejects there broke the
import of the whole package (#127).
"""

from __future__ import annotations

import typing as t

# Packages whose classes are framework surface, never client-callable handlers.
_FRAMEWORK_ROOTS = ("wireview", "pydantic")


def is_framework_class(cls: type) -> bool:
    """Whether a class comes from the framework rather than from user code."""
    if cls is object:
        return True
    module = getattr(cls, "__module__", "") or ""
    root = module.partition(".")[0]
    return root in _FRAMEWORK_ROOTS


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
