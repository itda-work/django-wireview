# Form Feedback

django-wireview provides a form feedback system that controls when validation
error messages are displayed to users. This prevents showing errors for fields
that the user hasn't interacted with yet.

## Overview

The feedback system:

1. Tracks which form fields have been "touched" (focused and blurred, or changed)
2. Only shows error messages for touched fields
3. Hides errors for untouched fields using CSS

This provides a better user experience by not overwhelming users with error
messages for fields they haven't filled in yet.

## Basic Usage

### HTML Structure

```html
<form {% on "submit" "save" %}>
  <div class="field">
    <label for="email">Email</label>
    <input type="email" name="email" id="email" value="{{ this.email }}">

    <!-- Error message with wire-feedback-for -->
    {% if this.errors.email %}
      <span wire-feedback-for="email" class="error wire-no-feedback">
        {{ this.errors.email }}
      </span>
    {% endif %}
  </div>

  <div class="field">
    <label for="password">Password</label>
    <input type="password" name="password" id="password">

    {% if this.errors.password %}
      <span wire-feedback-for="password" class="error wire-no-feedback">
        {{ this.errors.password }}
      </span>
    {% endif %}
  </div>

  <button type="submit">Register</button>
</form>
```

### Required CSS

Add this CSS to hide untouched field errors:

```css
/* Hide feedback elements until field is touched */
.wire-no-feedback {
  display: none !important;
}
```

Or with more sophisticated styling:

```css
/* Smooth transition for feedback elements */
[wire-feedback-for] {
  opacity: 1;
  max-height: 100px;
  transition: opacity 0.2s, max-height 0.2s;
}

.wire-no-feedback {
  opacity: 0;
  max-height: 0;
  overflow: hidden;
}
```

## How It Works

### Field Tracking

A field is marked as "touched" when:

- The user focuses on the field and then blurs (leaves) it
- The user changes the field value (for select, checkbox, radio)
- You programmatically mark it as touched

### Feedback Display

Elements with `wire-feedback-for="field_name"`:

1. Initially have `wire-no-feedback` class (hidden)
2. When the matching field is touched, the class is removed (visible)
3. After DOM updates, new feedback elements respect touched state

### Field Name Matching

The `wire-feedback-for` attribute matches against:

- The field's `name` attribute (primary)
- The field's `id` attribute (fallback)

## Component Example

```python
from wireview import Component
from pydantic import EmailStr, field_validator


class XRegistrationForm(Component):
    _template_name = "registration/form.html"

    email: str = ""
    password: str = ""
    errors: dict[str, str] = {}

    @field_validator("email")
    def validate_email(cls, v):
        if v and "@" not in v:
            raise ValueError("Invalid email address")
        return v

    async def validate_field(self, field: str, value: str):
        """Real-time field validation on input."""
        errors = {}

        if field == "email":
            if not value:
                errors["email"] = "Email is required"
            elif "@" not in value:
                errors["email"] = "Invalid email address"
        elif field == "password":
            if len(value) < 8:
                errors["password"] = "Password must be at least 8 characters"

        self.errors = {**self.errors, **errors}
        if not errors.get(field):
            self.errors.pop(field, None)

    async def save(self, email: str, password: str):
        """Handle form submission."""
        # Validate all fields
        self.errors = {}

        if not email:
            self.errors["email"] = "Email is required"
        if not password:
            self.errors["password"] = "Password is required"
        elif len(password) < 8:
            self.errors["password"] = "Password must be at least 8 characters"

        if self.errors:
            return

        # Save user...
        await self.wire.redirect_to("/welcome")
```

Template:

```html
{% load wireview %}

<form {% on "submit" "save" %} class="registration-form">
  <div class="field">
    <label for="email">Email</label>
    <input
      type="email"
      name="email"
      id="email"
      value="{{ this.email }}"
      {% on "input.debounce.300" "validate_field" field="email" %}
    >
    {% if this.errors.email %}
      <span wire-feedback-for="email" class="error wire-no-feedback">
        {{ this.errors.email }}
      </span>
    {% endif %}
  </div>

  <div class="field">
    <label for="password">Password</label>
    <input
      type="password"
      name="password"
      id="password"
      {% on "input.debounce.300" "validate_field" field="password" %}
    >
    {% if this.errors.password %}
      <span wire-feedback-for="password" class="error wire-no-feedback">
        {{ this.errors.password }}
      </span>
    {% endif %}
  </div>

  <button type="submit">Register</button>
</form>
```

## JavaScript API

### Touch a Field Programmatically

```javascript
// Mark a field as touched
wireview.feedback.touch("email");

// Check if a field is touched
if (wireview.feedback.isTouched("email")) {
  console.log("Email field has been touched");
}
```

### Get All Touched Fields

```javascript
const touched = wireview.feedback.getTouched();
console.log("Touched fields:", touched);
```

### Reset Feedback State

After form submission or reset, you may want to clear the touched state:

```javascript
// Reset all feedback (hide all error messages)
wireview.feedback.reset();
```

This can be called from a Hook:

```javascript
window.wireview.hooks.FormReset = {
  mounted() {
    this.el.addEventListener("reset", () => {
      wireview.feedback.reset();
    });
  }
};
```

## Integration with Django Forms

### Using Django Form Errors

```python
from django import forms
from wireview import Component


class ContactForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)


class XContactPage(Component):
    _template_name = "contact/page.html"

    form_data: dict = {}
    errors: dict[str, list[str]] = {}

    async def save(self, **data):
        form = ContactForm(data)

        if form.is_valid():
            # Process form...
            await self.wire.redirect_to("/thank-you")
        else:
            # Convert Django errors to dict
            self.errors = {
                field: list(errors)
                for field, errors in form.errors.items()
            }
```

Template:

```html
{% load wireview %}

<form {% on "submit" "save" %}>
  {% for field in form %}
    <div class="field">
      {{ field.label_tag }}
      {{ field }}

      {% if field.name in this.errors %}
        <ul wire-feedback-for="{{ field.name }}" class="errorlist wire-no-feedback">
          {% for error in this.errors|get_item:field.name %}
            <li>{{ error }}</li>
          {% endfor %}
        </ul>
      {% endif %}
    </div>
  {% endfor %}

  <button type="submit">Send</button>
</form>
```

## Best Practices

### 1. Always Include wire-no-feedback Initially

```html
<!-- Good: Class present initially -->
<span wire-feedback-for="email" class="error wire-no-feedback">
  Error message
</span>

<!-- Bad: Missing class -->
<span wire-feedback-for="email" class="error">
  Error message
</span>
```

### 2. Use Consistent Field Names

```html
<!-- Input name matches feedback-for -->
<input name="user_email" ...>
<span wire-feedback-for="user_email">...</span>
```

### 3. Reset Feedback on Successful Submit

```python
async def save(self, **data):
    if not self.errors:
        # Clear feedback state on success
        await self.wire.push_event("form:success", {})
```

```javascript
window.wireview.hooks.FormHandler = {
  mounted() {
    this.handleEvent("form:success", () => {
      wireview.feedback.reset();
    });
  }
};
```

### 4. Show All Errors on Submit

On form submission, you may want to show all errors regardless of touched state:

```html
<button
  type="submit"
  onclick="['email', 'password', 'name'].forEach(f => wireview.feedback.touch(f))"
>
  Submit
</button>
```

Or handle in the component:

```python
async def save(self, **data):
    # Validate all fields - server will return errors
    # and re-render will show them all
    self.validate_all()
    # After validation, all feedback elements will be visible
    # because they exist in DOM (server rendered with errors)
```

## CSS Examples

### Bootstrap-Style Errors

```css
.wire-no-feedback {
  display: none !important;
}

.invalid-feedback {
  color: #dc3545;
  font-size: 0.875em;
  margin-top: 0.25rem;
}

input.is-invalid {
  border-color: #dc3545;
}
```

### Tailwind CSS

```html
<span
  wire-feedback-for="email"
  class="text-red-500 text-sm mt-1 wire-no-feedback"
>
  {{ this.errors.email }}
</span>
```

```css
.wire-no-feedback {
  @apply hidden;
}
```

## Comparison with Phoenix LiveView

| Feature | Phoenix LiveView | django-wireview |
|---------|------------------|-----------------|
| Attribute | `phx-feedback-for` | `wire-feedback-for` |
| Hide class | `phx-no-feedback` | `wire-no-feedback` |
| Trigger | blur, change | blur, change |
| JavaScript API | None | `wireview.feedback.*` |
