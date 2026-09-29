from django.shortcuts import redirect, render
from django.urls import path

app_name = "jsprobe"

urlpatterns = [
    path("", lambda request: render(request, "jsprobe/page.html"), name="index"),
    path("landed/", lambda request: render(request, "jsprobe/landed.html"), name="landed"),
    # A boosted link to here ends on landed/ (#104)
    path("bounce/", lambda request: redirect("/jsprobe/landed/"), name="bounce"),
    path("slow-join/", lambda request: render(request, "jsprobe/slow_join_page.html"), name="slow_join"),
]
