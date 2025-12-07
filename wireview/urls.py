from django.urls import path

from .consumer import WireviewConsumer

websocket_urlpatterns = [
    path("__wireview__", WireviewConsumer.as_asgi()),  # type: ignore
]
