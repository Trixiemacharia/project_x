import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from .models import OutboxEvent

logger = logging.getLogger(__name__)
ANALYST_GROUP = "fraud_analysts"


def customer_group(user_id) -> str:
    return f"cust_{user_id}"  # server-side name only; access is enforced in the consumer


def emit(*, audience, event_type, payload, user_id=None) -> OutboxEvent:
    """Persist now, push after commit. A failed push never breaks the money flow: clients recover via REST."""
    event = OutboxEvent.objects.create(audience=audience, user_id=user_id, event_type=event_type, payload=payload)
    transaction.on_commit(lambda: _push(event))
    return event


def _push(event: OutboxEvent) -> None:
    group = ANALYST_GROUP if event.audience == OutboxEvent.Audience.ANALYST else customer_group(event.user_id)
    try:
        async_to_sync(get_channel_layer().group_send)(group, {"type": "push", "data": event.as_message()})
    except Exception:  # noqa: BLE001
        logger.exception("WebSocket push failed event_id=%s", event.pk)