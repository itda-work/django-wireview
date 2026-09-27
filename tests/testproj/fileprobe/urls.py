from django.shortcuts import render
from django.urls import path

from . import views

app_name = "fileprobe"

urlpatterns = [
    path("", lambda request: render(request, "fileprobe/page.html"), name="index"),
    path("put/<str:ref>/", views.put_target, name="put"),
]
