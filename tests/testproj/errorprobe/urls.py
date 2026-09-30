from django.urls import path

from . import views

app_name = "errorprobe"

urlpatterns = [
    path("", views.index, name="index"),
    path("late/", views.late, name="late"),
]
