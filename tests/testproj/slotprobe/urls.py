from django.urls import path

from . import views

app_name = "slotprobe"

urlpatterns = [
    path("", views.index, name="index"),
]
