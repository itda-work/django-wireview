from django.urls import path

from . import views

app_name = "chat_lv2"

urlpatterns = [
    path("", views.index, name="index"),
    path("room/<uuid:room_id>/", views.room, name="room"),
    path("room/<uuid:room_id>/join/", views.join_room, name="join_room"),
    path("room/<uuid:room_id>/leave/", views.leave_room, name="leave_room"),
    path("create/", views.create_room, name="create_room"),
    path("sign-in/<str:username>/", views.sign_in, name="sign_in"),
    path("sign-out/", views.sign_out, name="sign_out"),
]
