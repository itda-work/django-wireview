from django.urls import path

from .consumer import WireviewConsumer
from .views import UploadView

websocket_urlpatterns = [
    path("__wireview__", WireviewConsumer.as_asgi()),  # type: ignore
]

# HTTP URL patterns for upload endpoint
# Include these in your project's urls.py:
#   path("", include("wireview.urls"))
urlpatterns = [
    path(
        "__wireview_upload__/<str:component_id>/<str:upload_name>/",
        UploadView.as_view(),
        name="wireview_upload",
    ),
]
