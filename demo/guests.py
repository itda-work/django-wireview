"""Guest accounts: the examples that need a signed-in user get one per visitor, in one click.

The examples sign in as a fixed cast (alice, bob, carol), which on a public site would put
every visitor in the same account. The demo answers their sign-in URLs itself (demo/urls.py)
and gives each browser a guest of its own with a made-up name, and it shows the examples'
sign-in forms a single "guest" button (``DemoConfig.ready``). A private window is another
browser, so it is another guest -- the way the examples ask to be tried.

Guests are ordinary users named ``guest-<adjective>-<animal>-<number>``. The janitor deletes
them after ``DEMO_GUEST_MAX_AGE_HOURS`` and at the daily reset (demo/management/commands). A
visitor makes ``GUESTS_PER_HOUR`` of them at most, and the demo holds ``GUESTS_MAX``.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .limits import over_limit, plain, request_visitor

PREFIX = "guest-"

#: What the examples' sign-in forms list in place of their cast: one button, "Sign in as guest"
BUTTON = ("guest",)

#: The examples whose views define DEMO_USERS, and where each one's index is
EXAMPLES = {
    "chat_lv2": "chat_lv2:index",
    "chat_lv3": "chat_lv3:index",
    "chat_lv4": "chat_lv4:index",
    "notifications": "notifications:index",
}

ADJECTIVES = (
    "amber", "brave", "calm", "clever", "cosmic", "eager", "gentle", "happy", "jolly", "lucky",
    "mellow", "nimble", "quiet", "rapid", "shiny", "sunny", "swift", "tidy", "witty", "zesty",
)  # fmt: skip
ANIMALS = (
    "badger", "crane", "dolphin", "falcon", "fox", "gecko", "heron", "koala", "lynx", "marten",
    "otter", "panda", "puffin", "quokka", "raven", "seal", "tapir", "tiger", "walrus", "wren",
)  # fmt: skip


def is_guest(user) -> bool:
    return bool(getattr(user, "is_authenticated", False)) and user.get_username().startswith(PREFIX)


def new_guest():
    users = get_user_model()
    while True:
        name = f"{PREFIX}{secrets.choice(ADJECTIVES)}-{secrets.choice(ANIMALS)}-{secrets.randbelow(1000):03d}"
        if not users.objects.filter(username=name).exists():
            user = users(username=name)
            user.set_unusable_password()
            user.save()
            return user


@require_POST
def sign_in(request: HttpRequest, example: str, username: str = "") -> HttpResponse:
    """Sign the browser in as a guest of its own, then go where the example would have gone.

    ``username`` is the example's button (``guest``) and is not read: no visitor picks who
    they are. A browser already signed in as a guest stays that guest.
    """
    index = EXAMPLES[example]
    if not is_guest(request.user):
        budget = settings.DEMO_LIMITS
        if over_limit("guest", request_visitor(request), budget["GUESTS_PER_HOUR"], 3600):
            return plain("Too many guest accounts from here. Try again in an hour.", 429)
        if get_user_model().objects.filter(username__startswith=PREFIX).count() >= budget["GUESTS_MAX"]:
            return plain("The demo has as many guests as it can hold. Try again later.", 503)
        login(request, new_guest(), backend="django.contrib.auth.backends.ModelBackend")
    target = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(index)
