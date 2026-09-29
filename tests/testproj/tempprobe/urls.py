from django.urls import path

from . import views

app_name = "tempprobe"

urlpatterns = [
    path("", views.index, name="index"),
]
