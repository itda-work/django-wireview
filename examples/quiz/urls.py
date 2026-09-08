from django.urls import path

from . import views

app_name = "quiz"

urlpatterns = [
    path("", views.index, name="index"),
    path("<int:quiz_id>/", views.quiz_detail, name="detail"),
]
