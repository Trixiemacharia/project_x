from celery import shared_task

from .events import push
from .models import OutboxEvent


@shared_task(autoretry_for=(Exception,), retry_backoff=2, retry_backoff_max=60, max_retries=5)
def deliver_event(event_id: int) -> None:
    """Celery worker -> WebSocket gateway. Safe to repeat: clients dedupe on event id."""
    event = OutboxEvent.objects.filter(pk=event_id).first()
    if event is not None:
        push(event)