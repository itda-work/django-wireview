from django.urls import path

from . import views

app_name = "bookmarks"

urlpatterns = [
    path("", views.index, name="index"),
]
