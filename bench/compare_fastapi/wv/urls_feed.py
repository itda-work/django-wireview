from django.urls import include, path
from django.views.generic import TemplateView


class FeedPage(TemplateView):
    template_name = "feed/index.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "mode": self.request.GET.get("mode", "")}


urlpatterns = [
    path("", include("wireview.urls")),
    path("feed/", FeedPage.as_view()),
]
