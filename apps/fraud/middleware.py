from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache


@database_sync_to_async
def _user_from_ticket(ticket):
    key = f"wsticket:{ticket}"
    user_id = cache.get(key)
    if not user_id or not cache.delete(key):  # one-time: whoever deletes it first wins
        return AnonymousUser()
    return get_user_model().objects.filter(pk=user_id, is_active=True).first() or AnonymousUser()


class TicketAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        ticket = (parse_qs(scope.get("query_string", b"").decode()).get("ticket") or [""])[0]
        scope["user"] = await _user_from_ticket(ticket) if ticket else AnonymousUser()
        return await super().__call__(scope, receive, send)