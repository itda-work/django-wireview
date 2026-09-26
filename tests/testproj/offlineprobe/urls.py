from django.urls import path

from . import views

app_name = "offlineprobe"

urlpatterns = [
    path("", views.index, name="index"),
]
