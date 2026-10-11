"""WebSocket stacks for wireview.W020's tests (#209): which ones carry a session to the consumer."""

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from channels.sessions import SessionMiddlewareStack
from django.urls import path

from wireview.urls import websocket_urlpatterns

#: What the docs tell a project to write
AUTH = ProtocolTypeRouter(
    {"websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns)))}
)
#: A session and no user is enough for the handshake to say who it is for
SESSION_ONLY = ProtocolTypeRouter({"websocket": SessionMiddlewareStack(URLRouter(websocket_urlpatterns))})
#: The consumer straight under the router: no session, no user
BARE = ProtocolTypeRouter({"websocket": AllowedHostsOriginValidator(URLRouter(websocket_urlpatterns))})
#: The same, one router deeper
NESTED_BARE = ProtocolTypeRouter({"websocket": URLRouter([path("ws/", URLRouter(websocket_urlpatterns))])})


class ProjectSessionMiddleware:
    """A middleware of the project's own, which the check cannot see through."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        return await self.app(scope, receive, send)


#: Unknown: the walk stops at the project's middleware and says nothing
OPAQUE = ProtocolTypeRouter({"websocket": ProjectSessionMiddleware(URLRouter(websocket_urlpatterns))})
#: No WebSocket at all
HTTP_ONLY = ProtocolTypeRouter({})
