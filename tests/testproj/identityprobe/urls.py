from django.urls import path

from . import views

app_name = "identityprobe"

urlpatterns = [
    path("a/", views.page_a, name="a"),
    path("b/", views.page_b, name="b"),
    path("inside-a/", views.inside_a, name="inside-a"),
    path("inside-b/", views.inside_b, name="inside-b"),
    path("sign-in/<str:name>/", views.sign_in, name="sign-in"),
    path("sign-out/", views.sign_out, name="sign-out"),
    path("rotate/", views.rotate, name="rotate"),
    path("post/", views.post, name="post"),
]
