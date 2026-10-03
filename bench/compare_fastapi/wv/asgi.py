"""The wireview side's ASGI application, as the starter template's asgi.py builds it.

One difference: static files are served by the application itself whatever DEBUG says.
uvicorn serves nothing else, and FastAPI's StaticFiles does the same job on that side.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.compare_fastapi.wv.settings")

import django

django.setup()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application

from wireview.urls import websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": ASGIStaticFilesHandler(get_asgi_application()),
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
