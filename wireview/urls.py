from django.db import connections
from django.db.transaction import non_atomic_requests
from django.urls import path, register_converter

from .consumer import WireviewConsumer
from .features.upload_store import CONNECTION_ID_RE
from .views import UploadView


class ConnectionIdConverter:
    """The connection segment of an upload URL.

    It becomes a directory name in the chunk store, so the URLconf is the first
    place that holds it to the alphabet ``secrets.token_urlsafe`` produces. The
    signed token has to agree with it as well; this only keeps a malformed value
    from reaching the filesystem code at all.
    """

    regex = CONNECTION_ID_RE.pattern

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value


register_converter(ConnectionIdConverter, "wireview_connection")

# ``UploadView.post`` is async, and Django's handler refuses to wrap an async view
# in ATOMIC_REQUESTS -- it raises before the view runs, so a project with
# ``ATOMIC_REQUESTS = True`` got a 500 on every single chunk. The endpoint opens no
# database connection at all, so it is exempt on every alias rather than only the
# default one.
upload_endpoint = UploadView.as_view()
for _alias in connections:
    upload_endpoint = non_atomic_requests(_alias)(upload_endpoint)

websocket_urlpatterns = [
    path("__wireview__", WireviewConsumer.as_asgi()),  # type: ignore
]

# HTTP URL patterns for upload endpoint
# Include these in your project's urls.py:
#   path("", include("wireview.urls"))
urlpatterns = [
    path(
        "__wireview_upload__/<wireview_connection:connection_id>/<str:component_id>/<str:upload_name>/",
        upload_endpoint,
        name="wireview_upload",
    ),
]
