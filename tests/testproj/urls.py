"""testproj URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import include, path

from wireview.consumer import WireviewConsumer

urlpatterns = [
    path("", include("examples.todo.urls")),
    path("chat/", include("examples.chat.urls")),
    path("dashboard/", include("examples.dashboard.urls")),
    path("poll/", include("examples.poll.urls")),
    path("rating/", include("examples.rating.urls")),
    path("search/", include("examples.search.urls")),
    path("quiz/", include("examples.quiz.urls")),
    path("notifications/", include("examples.notifications.urls")),
    path("livecomp/", include("examples.livecomp.urls")),
    path("hooks/", include("examples.hooks.urls")),
    path("bookmarks/", include("testproj.bookmarks.urls")),
    path("livesession/", include("testproj.livesession.urls")),
    path("uploadprobe/", include("testproj.uploadprobe.urls")),
    # The chunk endpoint. A project that leaves this out has no uploads at all,
    # so the test project carries it the way a real one would.
    path("", include("wireview.urls")),
    path("admin/", admin.site.urls),
]

websocket_urlpatterns = [
    path("__wireview__", WireviewConsumer.as_asgi()),
]
