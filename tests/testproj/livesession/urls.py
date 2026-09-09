from django.urls import path

from . import views

app_name = "livesession"

urlpatterns = [
    path("public/", views.public, name="public"),
    path("public2/", views.public_two, name="public2"),
    path("staff/", views.staff_page, name="staff"),
    path("members/", views.members_page, name="members"),
    path("bounce/", views.redirect_to_public, name="bounce"),
    path("sign-in/", views.sign_in, name="sign-in"),
    path("sign-out/", views.sign_out, name="sign-out"),
]
