from django.urls import path

from . import views

app_name = "islandprobe"

urlpatterns = [
    path("", views.index, name="index"),
    path("again/", views.again, name="again"),
    path("elsewhere/", views.elsewhere, name="elsewhere"),
    path("holder/<str:which>/", views.holder, name="holder"),
    path("sides/<str:which>/", views.sides, name="sides"),
]
