from django.urls import path

from . import views

app_name = "domprobe"

urlpatterns = [
    path("", views.index, name="index"),
    path("streams/", views.streams, name="streams"),
]
