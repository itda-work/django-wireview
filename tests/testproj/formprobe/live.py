"""A form validated with a Django form, and the input modifiers, in a real browser.

tests/test_forms_e2e.py drives it. ``save`` is the pattern docs/features/form-feedback.md
teaches -- a Django form in the handler, what was typed and its errors in state,
each error under a ``wire-feedback-for`` -- so the documentation's example is the code that runs here.
"""

from django import forms

from wireview import Component


class SignupForm(forms.Form):
    email = forms.EmailField()
    name = forms.CharField(max_length=5)


class FormProbe(Component):
    class Meta:
        template_name = "formprobe/probe.html"

    values: dict[str, str] = {}
    errors: dict[str, list[str]] = {}
    saved: str = ""
    typed: int = 0
    throttled: int = 0

    async def save(self, **data):
        form = SignupForm(data)
        # What was typed, for the form's own fields only: the render that answers a
        # submit writes the server's value into each input (static/wireview/values.mjs),
        # so an input without value= comes back empty and the user's text is gone (#267).
        self.values = {name: str(data.get(name, "")) for name in form.fields}
        if form.is_valid():
            self.errors = {}
            self.saved = form.cleaned_data["email"]
        else:
            self.errors = {field: list(errors) for field, errors in form.errors.items()}

    async def debounced(self, **_rest):
        self.typed += 1

    async def throttle(self, **_rest):
        self.throttled += 1
