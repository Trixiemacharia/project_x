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
    """Store the notification now (same DB transaction as the change), queue delivery only after COMMIT."""
    event = OutboxEvent.objects.create(audience=audience, user_id=user_id, event_type=event_type, payload=payload)
    transaction.on_commit(lambda: _dispatch(event))
    return event


def _dispatch(event: OutboxEvent) -> None:
    """'Queue notification' -> Celery worker -> WebSocket gateway. Falls back to an inline push if the broker is down."""
    from .tasks import deliver_event  # local import: tasks imports this module

    try:
        deliver_event.delay(event.pk)
    except Exception:  # noqa: BLE001
        logger.warning("Celery unavailable, pushing inline event_id=%s", event.pk)
        try:
            push(event)
        except Exception:  # noqa: BLE001
            logger.exception("WebSocket push failed event_id=%s (clients can recover via REST)", event.pk)


def push(event: OutboxEvent) -> None:
    group = ANALYST_GROUP if event.audience == OutboxEvent.Audience.ANALYST else customer_group(event.user_id)
    async_to_sync(get_channel_layer().group_send)(group, {"type": "push", "data": event.as_message()})