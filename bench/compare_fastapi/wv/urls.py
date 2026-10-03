from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("", include("wireview.urls")),
    path("", TemplateView.as_view(template_name="board/index.html")),
]
