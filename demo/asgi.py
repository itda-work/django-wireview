"""
ASGI entry point of the demo: HTTP to Django, WebSocket to wireview behind the demo's limits.

No static files here: Caddy serves /static/ (docs/DEPLOYMENT.md, "정적 파일").
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo.settings")

import django

# Before wireview.urls: it imports the consumer, which imports Django models
django.setup()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from demo.limits import SocketLimits
from wireview.urls import websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": get_asgi_application(),
        "websocket": SocketLimits(AuthMiddlewareStack(URLRouter(websocket_urlpatterns))),
    }
)
