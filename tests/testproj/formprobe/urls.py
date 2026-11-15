from django.shortcuts import render
from django.urls import path

app_name = "formprobe"

urlpatterns = [
    path("", lambda request: render(request, "formprobe/page.html"), name="index"),
    path("feedback/", lambda request: render(request, "formprobe/feedback_page.html"), name="feedback"),
]
