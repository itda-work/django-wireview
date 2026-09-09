from django.urls import path

from . import views

app_name = "uploadprobe"

urlpatterns = [
    path("", views.index, name="index"),
]
