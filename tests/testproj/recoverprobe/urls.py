from django.urls import path

from . import views

app_name = "recoverprobe"

urlpatterns = [
    path("", views.index, name="index"),
]
