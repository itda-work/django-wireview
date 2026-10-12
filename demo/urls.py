"""The demo's URLs: the list, the examples under their testproj prefixes, the guest sign-in.

No upload endpoint (wireview.urls): no example uploads, so the demo takes no files at all.
"""

from django.urls import include, path
from django.views.generic import RedirectView

from . import guests, views

demo_patterns = (
    [
        path("", views.index, name="index"),
    ],
    "demo",
)

# Before the examples' own: the demo answers their sign-in URLs with a guest (demo/guests.py)
guest_patterns = [
    path(f"{prefix}/sign-in/<str:username>/", guests.sign_in, {"example": example})
    for prefix, example in (
        ("chat-lv2", "chat_lv2"),
        ("chat-lv3", "chat_lv3"),
        ("chat-lv4", "chat_lv4"),
        ("notifications", "notifications"),
    )
]

urlpatterns = [
    path("health/", views.health),
    path("ready/", views.ready),
    *guest_patterns,
    path("", include(demo_patterns)),
    # todo's index is testproj's hub of examples ("Back to Examples" links name it); here the
    # demo's list is that hub
    path("todo/", RedirectView.as_view(url="/")),
    path("todo/", include("examples.todo.urls")),
    path("chat/", include("examples.chat.urls")),
    path("chat-lv2/", include("examples.chat_lv2.urls")),
    path("chat-lv3/", include("examples.chat_lv3.urls")),
    path("chat-lv4/", include("examples.chat_lv4.urls")),
    path("dashboard/", include("examples.dashboard.urls")),
    path("poll/", include("examples.poll.urls")),
    path("rating/", include("examples.rating.urls")),
    path("search/", include("examples.search.urls")),
    path("quiz/", include("examples.quiz.urls")),
    path("notifications/", include("examples.notifications.urls")),
    path("slots/", include("examples.slots.urls")),
    path("livecomp/", include("examples.livecomp.urls")),
    path("hooks/", include("examples.hooks.urls")),
]
