from django.urls import path

from . import views

app_name = "reloadprobe"

urlpatterns = [
    path("", views.index, name="index"),
    path("away/", views.away, name="away"),
]
