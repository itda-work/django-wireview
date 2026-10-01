from django.shortcuts import render
from django.urls import path

from . import views

app_name = "fileprobe"

urlpatterns = [
    path("", lambda request: render(request, "fileprobe/page.html"), name="index"),
    path("nested/", lambda request: render(request, "fileprobe/nested.html"), name="nested"),
    path("shelf/", lambda request: render(request, "fileprobe/shelfpage.html"), name="shelf"),
    path("other/", lambda request: render(request, "fileprobe/other.html"), name="other"),
    path("put/<str:ref>/", views.put_target, name="put"),
]
