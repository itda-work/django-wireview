from django.urls import path

from . import views

app_name = "csrfprobe"

urlpatterns = [
    path("", views.index, name="index"),
    path("post/", views.post, name="post"),
    path("sign-in/<str:name>/", views.sign_in, name="sign-in"),
]
