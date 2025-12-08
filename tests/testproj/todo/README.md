# Todo App Example

A minimal wireview example implementing the classic [TodoMVC](https://todomvc.com/) application with real-time updates.

## Features Demonstrated

### Component Basics
- **Component class**: Pydantic-based state management (`live.py`)
- **Template rendering**: Django templates with wireview tags
- **Event handlers**: Async methods for user interactions

### Subscriptions & Real-time Updates
- **Model subscriptions**: `_subscriptions = {"todo.item"}` for ORM change notifications
- **Instance subscriptions**: Dynamic `@property _subscriptions` for per-item updates
- **mutation()**: Lifecycle hook for handling model changes

### Event Handling
- **Basic events**: `{% on 'click' 'delete' %}`
- **Event modifiers**: `{% on 'keypress.enter' 'add' %}`, `{% on 'click.prevent' 'show' %}`
- **Multiple events**: Same element with `blur` and `keypress.enter`

### Render Control
- **skip_render()**: Skip unnecessary re-renders when server already knows state
- **force_render()**: Force full re-render on next cycle
- **destroy()**: Remove component from DOM

### Template Tags
- **{% tag_header %}**: Required component wrapper attribute
- **{% component %}**: Nest child components with unique IDs
- **{% on %}**: Bind events to handlers
- **{% class %}**: Conditional CSS classes
- **{% cond %}**: Conditional HTML attributes

## Structure

```
todo/
├── live.py           # Component definitions
├── models.py         # Django ORM models
├── views.py          # Simple view functions
├── urls.py           # URL routing
├── templates/
│   ├── base.html     # Base layout
│   ├── index.html    # Landing page
│   ├── todo.html     # Todo page wrapper
│   └── todo/
│       ├── list.html     # XTodoList template
│       ├── item.html     # XTodoItem template
│       └── counter.html  # XTodoCounter template
└── static/
    └── todo.css      # TodoMVC CSS styles
```

## Components

### XTodoList
Main container component managing the todo list.

```python
class XTodoList(Component):
    _template_name = "todo/list.html"
    _subscriptions = {"todo.item"}  # Subscribe to all Item changes

    showing: Showing = Showing.ALL

    @property
    def items(self):
        return Item.objects.all()

    async def mutation(self, channel, action, instance):
        if action == ModelAction.CREATED:
            self.force_render()  # Re-render when new item created

    async def add(self, new_item: str):
        await Item.objects.acreate(text=new_item)
        self.skip_render()  # Let mutation() handle the update
```

### XTodoItem
Individual todo item with inline editing.

```python
class XTodoItem(Component):
    _template_name = "todo/item.html"

    @property
    def _subscriptions(self):
        return {f"todo.item.{self.item.id}"}  # Subscribe to this item only

    item: Item
    editing: bool = False

    async def mutation(self, channel, instance, action):
        if action == ModelAction.DELETED:
            await self.destroy()  # Remove from DOM
        else:
            self.item = instance  # Update local state
```

### XTodoCounter
Counter showing active items count.

```python
class XTodoCounter(Component):
    _template_name = "todo/counter.html"
    _subscriptions = {"todo.item"}  # Auto-update on any item change

    @property
    def items(self):
        return Item.objects.all()
```

## Template Patterns

### Component Container
Every component template must wrap content with `{% tag_header %}`:

```html
{% load wireview %}

<div {% tag_header %}>
  <!-- Component content -->
</div>
```

### Nested Components
Use unique IDs when rendering multiple instances:

```html
{% for item in this.items %}
  {% component 'XTodoItem' id="item-"|concat:item.id item=item %}
{% endfor %}
```

### Event Binding
```html
<!-- Simple click -->
<button {% on 'click' 'delete' %}>Delete</button>

<!-- With modifier -->
<input {% on 'keypress.enter' 'add' %} />

<!-- With parameters -->
<a {% on 'click.prevent' 'show' showing='completed' %}>Completed</a>
```

### Conditional Styling
```html
<!-- Conditional classes -->
<li {% class {"completed": item.completed, "editing": editing} %}>

<!-- Conditional attributes -->
<input {% cond {"checked": item.completed} %} />
```

## Running

```bash
cd tests
python manage.py runserver
# Visit http://localhost:8000/todo/
```
