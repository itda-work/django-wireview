from django.urls import path

from . import views

app_name = "slots"

urlpatterns = [
    path("", views.gallery, name="gallery"),
]
