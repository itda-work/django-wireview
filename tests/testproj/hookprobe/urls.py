from django.urls import path

from . import views

app_name = "hookprobe"

urlpatterns = [
    path("", views.index, name="index"),
    path("elsewhere/", views.elsewhere, name="elsewhere"),
]
