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
    path("", include("testproj.todo.urls")),
    path("chat/", include("testproj.chat.urls")),
    path("dashboard/", include("testproj.dashboard.urls")),
    path("poll/", include("testproj.poll.urls")),
    path("rating/", include("testproj.rating.urls")),
    path("search/", include("testproj.search.urls")),
    path("quiz/", include("testproj.quiz.urls")),
    path("notifications/", include("testproj.notifications.urls")),
    path("admin/", admin.site.urls),
]

websocket_urlpatterns = [
    path("__wireview__", WireviewConsumer.as_asgi()),
]
