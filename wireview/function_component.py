"""Function Components - Stateless reusable template functions.

Function components provide a simple way to create reusable UI elements
without the overhead of stateful components. They are ideal for:
- UI primitives (buttons, icons, badges)
- Layout helpers (cards, grids, containers)
- Any element that doesn't need WebSocket state

Quick Start
===========

1. Define a function component:

    from wireview import function_component

    @function_component
    def button(text: str, variant: str = "primary"):
        return f'<button class="btn btn-{variant}">{text}</button>'

2. Use in template:

    {% load wireview %}
    {% func "button" text="Click me" variant="danger" %}


Template-based Components
=========================

For complex components, use a template:

    @function_component(template="components/alert.html")
    def alert(message: str, type: str = "info", dismissible: bool = False):
        return {
            "message": message,
            "type": type,
            "dismissible": dismissible,
        }

The function returns a context dict that will be passed to the template.


Slots Support
=============

Function components can have slots using func_block:

    @function_component(
        template="components/card.html",
        slots={"header": {"required": False}, "footer": {"required": False}}
    )
    def card(title: str = "", variant: str = "default"):
        return {"title": title, "variant": variant}

Usage:

    {% func_block "card" title="Welcome" %}
        {% fill header %}<h2>Custom Header</h2>{% endfill %}
        <p>Card content here</p>
        {% fill footer %}<button>Save</button>{% endfill %}
    {% endfunc %}


Naming Conventions
==================

Components are registered by function name. Use unique names to avoid conflicts.
You can also use qualified names with the `name` parameter:

    @function_component(name="myapp.button")
    def my_button(text: str):
        return f'<button>{text}</button>'

    # In template:
    {% func "myapp.button" text="Click" %}


Type Validation
===============

Function parameters are validated using pydantic when available:

    @function_component
    def avatar(src: str, size: int = 40, alt: str = ""):
        if size < 16:
            size = 16
        elif size > 256:
            size = 256
        return f'<img src="{src}" width="{size}" height="{size}" alt="{alt}">'

Invalid types will raise clear error messages.
"""

from __future__ import annotations

import inspect
import typing as t
from dataclasses import dataclass, field

from django.template import TemplateSyntaxError, loader
from django.utils.safestring import SafeString, mark_safe

if t.TYPE_CHECKING:
    from .slots import SlotContainer

__all__ = (
    "function_component",
    "FunctionComponent",
    "get_function_component",
    "list_function_components",
)


# Global registry for function components
_registry: dict[str, "FunctionComponent"] = {}


@dataclass
class FunctionComponent:
    """
    Wrapper class for function components.

    Attributes:
        func: The decorated function
        name: Component name for template lookup
        template: Optional template path for rendering
        slots: Slot definitions (like Component._slots)
    """

    func: t.Callable[..., t.Any]
    name: str
    template: str | None = None
    slots: dict[str, dict[str, t.Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Copy metadata from the original function
        self.__doc__ = self.func.__doc__
        self.__name__ = self.name
        self.__module__ = self.func.__module__

        # Extract parameter info for validation
        self._signature = inspect.signature(self.func)
        self._param_info = self._extract_param_info()

    def _extract_param_info(self) -> dict[str, dict[str, t.Any]]:
        """Extract parameter information from function signature."""
        params = {}
        for name, param in self._signature.parameters.items():
            info: dict[str, t.Any] = {"required": param.default is inspect.Parameter.empty}
            if param.default is not inspect.Parameter.empty:
                info["default"] = param.default
            if param.annotation is not inspect.Parameter.empty:
                info["type"] = param.annotation
            params[name] = info
        return params

    def validate_args(self, kwargs: dict[str, t.Any]) -> dict[str, t.Any]:
        """
        Validate and coerce arguments.

        Args:
            kwargs: Arguments passed to the component

        Returns:
            Validated arguments dict

        Raises:
            TypeError: If required arguments are missing
            ValueError: If type coercion fails
        """
        validated = {}

        for name, info in self._param_info.items():
            if name in kwargs:
                value = kwargs[name]
                # Type coercion for basic types
                if "type" in info and value is not None:
                    target_type = info["type"]
                    if target_type in (int, float, str, bool):
                        try:
                            if target_type is bool and isinstance(value, str):
                                value = value.lower() not in ("false", "0", "no", "")
                            else:
                                value = target_type(value)
                        except (ValueError, TypeError) as e:
                            raise ValueError(
                                f"Function component '{self.name}': "
                                f"Cannot convert '{name}' to {target_type.__name__}: {e}"
                            ) from e
                validated[name] = value
            elif info["required"]:
                raise TypeError(f"Function component '{self.name}' " f"missing required argument: '{name}'")
            elif "default" in info:
                validated[name] = info["default"]

        # Check for unexpected arguments
        unexpected = set(kwargs.keys()) - set(self._param_info.keys())
        if unexpected:
            raise TypeError(
                f"Function component '{self.name}' " f"got unexpected arguments: {', '.join(sorted(unexpected))}"
            )

        return validated

    def render(
        self,
        kwargs: dict[str, t.Any],
        slots: "SlotContainer | None" = None,
        context: t.Any = None,
    ) -> SafeString:
        """
        Render the function component.

        Args:
            kwargs: Arguments for the component function
            slots: Optional slot container for block-style usage
            context: Django template context (for slot rendering)

        Returns:
            Rendered HTML as SafeString
        """
        # Validate arguments
        validated = self.validate_args(kwargs)

        # Call the function
        result = self.func(**validated)

        # Handle different return types
        if self.template:
            # Template-based: result should be a dict (context)
            if not isinstance(result, dict):
                raise TypeError(
                    f"Function component '{self.name}' with template must return a dict, "
                    f"got {type(result).__name__}"
                )
            template = loader.get_template(self.template)
            template_context = {**result}

            # Add slots to context if provided
            if slots is not None:
                template_context["slots"] = slots

            html = template.render(template_context)
            return mark_safe(html)
        else:
            # Inline: result should be a string
            if not isinstance(result, str):
                raise TypeError(
                    f"Function component '{self.name}' without template must return a str, "
                    f"got {type(result).__name__}"
                )
            return mark_safe(result)

    def __call__(self, **kwargs: t.Any) -> SafeString:
        """Allow calling the component directly in Python code."""
        return self.render(kwargs)

    def __repr__(self) -> str:
        return f"FunctionComponent({self.name!r}, template={self.template!r})"


def function_component(
    func: t.Callable[..., t.Any] | None = None,
    *,
    name: str | None = None,
    template: str | None = None,
    slots: dict[str, dict[str, t.Any]] | None = None,
) -> t.Any:
    """
    Decorator to create a function component.

    Can be used with or without arguments:

        @function_component
        def button(text: str):
            return f'<button>{text}</button>'

        @function_component(template="components/card.html")
        def card(title: str = ""):
            return {"title": title}

    Args:
        func: The function to wrap (when used without arguments)
        name: Custom component name (default: function name)
        template: Template path for rendering (optional)
        slots: Slot definitions for block-style components

    Returns:
        FunctionComponent wrapper
    """

    def decorator(f: t.Callable[..., t.Any]) -> FunctionComponent:
        component_name = name or f.__name__

        fc = FunctionComponent(
            func=f,
            name=component_name,
            template=template,
            slots=slots or {},
        )

        # Register globally
        _registry[component_name] = fc

        # Also register with module prefix for disambiguation
        fqn = f"{f.__module__}.{component_name}"
        _registry[fqn] = fc

        return fc

    if func is not None:
        # Called without arguments: @function_component
        return decorator(func)

    # Called with arguments: @function_component(...)
    return decorator


def get_function_component(name: str) -> FunctionComponent:
    """
    Get a function component by name.

    Args:
        name: Component name or fully qualified name

    Returns:
        The FunctionComponent instance

    Raises:
        TemplateSyntaxError: If component not found
    """
    if name in _registry:
        return _registry[name]

    raise TemplateSyntaxError(
        f"Function component '{name}' not found. " f"Available: {', '.join(sorted(_registry.keys()))}"
    )


def list_function_components() -> list[str]:
    """
    List all registered function component names.

    Returns:
        Sorted list of component names
    """
    # Return only short names (not FQN duplicates)
    return sorted(name for name in _registry.keys() if "." not in name or name.count(".") == 1)
