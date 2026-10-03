from django.shortcuts import render
from django.urls import path

app_name = "imeprobe"

urlpatterns = [
    path("", lambda request: render(request, "imeprobe/page.html"), name="index"),
]
