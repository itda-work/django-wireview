from django.urls import path

from . import views

app_name = "shareprobe"

urlpatterns = [
    path("", views.index, name="index"),
    path("sign-in/<str:name>/", views.sign_in, name="sign-in"),
]
