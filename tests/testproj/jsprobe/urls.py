from django.shortcuts import render
from django.urls import path

app_name = "jsprobe"

urlpatterns = [
    path("", lambda request: render(request, "jsprobe/page.html"), name="index"),
    path("landed/", lambda request: render(request, "jsprobe/landed.html"), name="landed"),
]
