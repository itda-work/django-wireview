from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.index, name="index"),
    path("room/<uuid:room_id>/", views.room, name="room"),
    path("create/", views.create_room, name="create_room"),
]
