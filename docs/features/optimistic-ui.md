# Optimistic UI

django-wireview provides several features for creating responsive, optimistic user interfaces that give immediate feedback while server operations are in progress.

## Loading Classes

When an event is triggered, loading classes are automatically added to the triggering element:

```html
<button {% on "click" "save" %} class="btn">Save</button>
```

During the server round-trip, the button will have these classes:
- `wireview-loading` - Always added
- `wireview-click-loading` - Added for click events
- `wireview-submit-loading` - Added for form submissions
- `wireview-change-loading` - Added for change events

### Styling Loading States

```css
/* Show spinner during loading */
.wireview-loading {
  opacity: 0.7;
  cursor: wait;
}

/* Add spinner icon */
.wireview-click-loading::after {
  content: "";
  display: inline-block;
  width: 1em;
  height: 1em;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.75s linear infinite;
  margin-left: 0.5em;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
```

## wire-disabled-with

The `wire-disabled-with` attribute provides instant feedback by:
1. Disabling the element immediately on click
2. Replacing the button text with a loading message
3. Restoring the original state when the operation completes

### Basic Usage

```html
<button
  {% on "click" "save" %}
  wire-disabled-with="Saving..."
>
  Save
</button>
```

When clicked:
1. Button becomes disabled (`disabled="true"`)
2. Text changes from "Save" to "Saving..."
3. After server response, text returns to "Save" and button is re-enabled

### Form Submission

```html
<form {% on "submit" "create_post" %}>
  <input type="text" name="title" placeholder="Post title">
  <textarea name="content"></textarea>

  <button
    type="submit"
    wire-disabled-with="Creating post..."
  >
    Create Post
  </button>
</form>
```

### With Icons (using Tailwind/Heroicons)

```html
<button
  {% on "click" "delete_item" id=item.id %}
  wire-disabled-with="Deleting..."
  class="flex items-center gap-2"
>
  <svg class="w-4 h-4"><!-- trash icon --></svg>
  Delete
</button>
```

Note: When using `wire-disabled-with`, only the text content is replaced. If you need to preserve icons during loading, use CSS-based loading indicators instead.

### Combined with Loading Classes

You can combine `wire-disabled-with` with CSS loading styles:

```html
<button
  {% on "click" "process" %}
  wire-disabled-with="Processing..."
  class="btn"
>
  Process Data
</button>
```

```css
/* Dim the button and show cursor change */
.btn.wireview-loading {
  opacity: 0.6;
  cursor: wait;
}
```

## JS Commands for Immediate Feedback

For instant UI updates without waiting for the server, use JS commands:

```html
<button
  {% on "click" "toggle_menu" %}
  wire-disabled-with="Opening..."
  onclick="{{ JS().toggle_class(target='#menu', names='hidden') }}"
>
  Toggle Menu
</button>
```

The `JS()` command executes immediately, while `wire-disabled-with` handles the server round-trip feedback.

## Best Practices

1. **Use meaningful loading text**: "Saving..." is better than "Loading..."
2. **Keep it short**: Long text may cause layout shifts
3. **Indicate the action**: Match the loading text to the operation
4. **Provide visual feedback**: Combine with CSS styles for opacity/cursor changes

### Examples of Good Loading Text

| Button Text | Loading Text |
|-------------|--------------|
| Save | Saving... |
| Delete | Deleting... |
| Submit | Submitting... |
| Create Account | Creating account... |
| Send Message | Sending... |
| Add to Cart | Adding... |

## Phoenix LiveView Comparison

| Phoenix LiveView | django-wireview |
|-----------------|-----------------|
| `phx-disable-with` | `wire-disabled-with` |
| `phx-click-loading` | `wireview-click-loading` |
| `phx-submit-loading` | `wireview-submit-loading` |
| `phx-change-loading` | `wireview-change-loading` |

## See Also

- [JS Commands](../reference/js-commands.md)
- [Event Handling](../tutorials/02-counter-component.md)
