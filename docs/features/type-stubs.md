# Type Stubs (.pyi) Auto-Generation

django-wireview supports automatic generation of Python type stub files (`.pyi`) for components.
This enables better IDE support and static type checking with tools like mypy and pyright.

## Quick Start

Generate type stubs for all components:

```bash
python manage.py wireview_stubs
```

## CLI Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview without writing files |
| `--check` | CI mode: exit 1 if stubs are outdated |
| `--output-dir=DIR` | Custom output directory (default: next to source) |
| `--app=NAME` | Filter by app name (can be repeated) |
| `-v 2` | Verbose output |

### Examples

```bash
# Preview what would be generated
python manage.py wireview_stubs --dry-run

# Check if stubs are up-to-date (for CI)
python manage.py wireview_stubs --check

# Generate stubs for specific app only
python manage.py wireview_stubs --app myapp

# Generate to custom directory
python manage.py wireview_stubs --output-dir=./stubs

# Verbose output
python manage.py wireview_stubs -v 2
```

## Auto-Generation

When `DEBUG=True`, type stubs are automatically regenerated on server start.

### Configuration

```python
# settings.py
WIREVIEW = {
    "AUTO_GENERATE_STUBS": True,  # Default: True (in DEBUG mode)
}
```

Set to `False` to disable auto-generation:

```python
WIREVIEW = {
    "AUTO_GENERATE_STUBS": False,
}
```

## Generated Stub Example

For a component like this:

```python
# myapp/live.py
from wireview import Component

class Counter(Component):
    """A simple counter component."""
    _template_name = "counter.html"

    count: int = 0
    step: int = 1

    async def increment(self, amount: int = 1) -> None:
        """Increment the counter."""
        self.count += amount
```

The generated stub file (`myapp/live.pyi`):

```python
"""Auto-generated type stubs for wireview components.

DO NOT EDIT - regenerate with: python manage.py wireview_stubs
"""

from typing import Any, ClassVar
from wireview.component import Component

class Counter(Component):
    """A simple counter component."""

    _template_name: ClassVar[str]

    count: int
    step: int

    async def increment(self, amount: int = ...) -> None: ...

    # Wireview metadata for IDE support
    __wireview_attrs__: ClassVar[dict[str, dict[str, Any]]] = {
        'count': {'type': 'int', 'required': False, 'default': 0},
        'step': {'type': 'int', 'required': False, 'default': 1}
    }
    __wireview_handlers__: ClassVar[list[str]] = ['increment']
```

## Metadata Attributes

Generated stubs include metadata for IDE/LSP support:

### `__wireview_attrs__`

Dictionary of component fields with metadata:

```python
__wireview_attrs__ = {
    'count': {
        'type': 'int',       # Type annotation as string
        'required': False,   # Whether field is required
        'default': 0,        # Default value
    }
}
```

### `__wireview_handlers__`

List of event handler method names:

```python
__wireview_handlers__ = ['increment', 'decrement', 'reset']
```

## Component Types

The stub generator supports all component types:

| Type | Description |
|------|-------------|
| `Component` | Standard stateful component |
| `LiveComponent` | Nested component with independent state |
| `FunctionComponent` | Stateless template function |

### LiveComponent Example

```python
class Counter(LiveComponent):
    """LiveComponent stub with proper inheritance."""

    _template_name: ClassVar[str]

    count: int

    async def increment(self) -> None: ...
    async def update(self, **assigns: Any) -> None: ...
```

### FunctionComponent Example

```python
button: FunctionComponent
"""Simple button component."""
```

## Dynamic Subscriptions

If a component uses `@property` for `_subscriptions`, this is noted in the stub:

```python
class XTodoItem(Component):
    _template_name: ClassVar[str]
    # Note: _subscriptions is a dynamic property
    @property
    def _subscriptions(self) -> set[str]: ...
```

## CI/CD Integration

Use `--check` mode in CI pipelines to verify stubs are up-to-date:

```yaml
# GitHub Actions example
- name: Check type stubs
  run: python manage.py wireview_stubs --check
```

Exit codes:
- `0`: Stubs are up-to-date
- `1`: Stubs need regeneration

## Pre-commit Hook

Add to `.pre-commit-config.yaml`:

```yaml
- repo: local
  hooks:
    - id: wireview-stubs
      name: Generate wireview type stubs
      entry: python manage.py wireview_stubs
      language: system
      pass_filenames: false
      files: '.*live\.py$'
```

## Troubleshooting

### Stubs not generated for some components

Components are only discovered if they:
1. Are registered in `Component._all` or `LiveComponent._live_all`
2. Are not in library paths (site-packages, venv)
3. Have a valid source file path

### Local types show as comments

Local types (defined in the same module) are shown as comments:

```python
# Local types (may need manual import)
# from . import Item
# from . import ModelAction
```

You can manually uncomment and adjust these imports as needed.

### Parameter types showing as `None`

Methods without explicit type hints will show `None` for parameter types.
Add type annotations to your component methods for better stub generation:

```python
async def mutation(
    self,
    channel: str,  # Add type hints
    action: ModelAction,
    instance: Item,
) -> None:
    ...
```
