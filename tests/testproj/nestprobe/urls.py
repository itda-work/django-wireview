from django.urls import path

from . import views

app_name = "nestprobe"

urlpatterns = [
    path("", views.index, name="index"),
]
