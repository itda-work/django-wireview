from django.urls import path

from . import views

app_name = "domprobe"

urlpatterns = [
    path("", views.index, name="index"),
    path("streams/", views.streams, name="streams"),
    path("table/", views.table, name="table"),
    path("payload/", views.payload, name="payload"),
    path("family/", views.family, name="family"),
    path("http/", views.http, name="http"),
    path("received/", views.received, name="received"),
]
