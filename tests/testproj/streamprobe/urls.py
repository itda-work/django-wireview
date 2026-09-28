from django.shortcuts import render
from django.urls import path

app_name = "streamprobe"

urlpatterns = [path("", lambda request: render(request, "streamprobe/page.html"), name="index")]
