from django.urls import path

from . import views

app_name = "poll"

urlpatterns = [
    path("", views.index, name="index"),
    path("<int:poll_id>/", views.poll_detail, name="detail"),
]
