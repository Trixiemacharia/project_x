"""Replace fintech/asgi.py with this."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fintech.settings")
django_asgi_app = get_asgi_application()  # must run before importing anything that touches models

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from apps.fraud.middleware import TicketAuthMiddleware  # noqa: E402
from apps.fraud.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(TicketAuthMiddleware(URLRouter(websocket_urlpatterns))),
})