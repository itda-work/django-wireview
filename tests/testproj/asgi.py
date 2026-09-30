import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testproj.settings")

import django

django.setup()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application

from wireview.urls import websocket_urlpatterns

# Django's own ASGI handler, as README and every deployment guide tell a project to use.
# Wrapping the WSGI handler in asgiref's WsgiToAsgi instead sent each response through
# async_to_sync, and Uvicorn starts the next request on a keep-alive connection from
# inside that call -- so the request inherited an executor that had already quit and
# died with "CurrentThreadExecutor already quit or is broken" (#129).
#
# Static files through Django's ASGI static handler -- what daphne's and Channels'
# runserver put in front of an ASGI app -- because this is a development server, not a
# deployment (production serves them from a proxy or CDN). WhiteNoise's middleware is
# sync-only, so under the ASGI handler every file it served was a sync iterator Django
# had to drain, with a warning each time; the static handler streams them async.
application = ProtocolTypeRouter(
    {
        "http": ASGIStaticFilesHandler(get_asgi_application()),
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
