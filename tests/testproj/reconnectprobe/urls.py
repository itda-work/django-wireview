from django.shortcuts import render
from django.urls import path

app_name = "reconnectprobe"

urlpatterns = [
    path("", lambda request: render(request, "reconnectprobe/page.html"), name="index"),
]
