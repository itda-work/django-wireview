from django.urls import path

from . import views

app_name = "rating"

urlpatterns = [
    path("", views.index, name="index"),
    path("<int:product_id>/", views.product_detail, name="detail"),
]
