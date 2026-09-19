from django.urls import path

from . import views

app_name = "valueprobe"

urlpatterns = [
    path("", views.index, name="index"),
]
