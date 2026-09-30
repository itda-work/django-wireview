from django.urls import path

from . import views

app_name = "stickyprobe"

urlpatterns = [
    path("<str:name>/", views.page, name="page"),
]
