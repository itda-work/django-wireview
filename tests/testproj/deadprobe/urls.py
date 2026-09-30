from django.urls import path

from . import views

app_name = "deadprobe"

urlpatterns = [
    path("", views.index, name="index"),
    path("add/", views.add, name="add"),
]
