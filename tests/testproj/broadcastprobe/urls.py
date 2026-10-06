from django.urls import path

from . import views

app_name = "broadcastprobe"

urlpatterns = [path("", views.index, name="index")]
