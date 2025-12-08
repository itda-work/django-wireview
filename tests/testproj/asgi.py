import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testproj.settings")

import django

django.setup()

from asgiref.wsgi import WsgiToAsgi
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.wsgi import get_wsgi_application

from wireview.urls import websocket_urlpatterns

application = ProtocolTypeRouter(
    {"http": WsgiToAsgi(get_wsgi_application()), "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns))}
)
