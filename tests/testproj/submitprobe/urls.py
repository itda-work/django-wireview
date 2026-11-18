from django.urls import path

from . import views

app_name = "submitprobe"

urlpatterns = [
    path("", views.index, name="index"),
    path("lock/", views.lock, name="lock"),
    path("sign-in/", views.sign_in, name="sign-in"),
    path("welcome/", views.welcome, name="welcome"),
    path("nothing/", views.nothing, name="nothing"),
    path("download/", views.download, name="download"),
    path("popup/", views.popup, name="popup"),
]
