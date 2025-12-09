"""URLs for LiveComponent test app."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="livecomp_dashboard"),
]
