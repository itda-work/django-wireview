import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testproj.settings")

import django

django.setup()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from wireview.urls import websocket_urlpatterns

# Django's own ASGI handler, as README and every deployment guide tell a project to use.
# Wrapping the WSGI handler in asgiref's WsgiToAsgi instead sent each response through
# async_to_sync, and Uvicorn starts the next request on a keep-alive connection from
# inside that call -- so the request inherited an executor that had already quit and
# died with "CurrentThreadExecutor already quit or is broken" (#129).
application = ProtocolTypeRouter(
    {"http": get_asgi_application(), "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns))}
)
