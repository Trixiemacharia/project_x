import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def user_group(user_id) -> str:
    return f"user_{user_id}"


def publish_payment_event(payment) -> None:
    """Pushes a status update to the owner's group. Must never break a payment path."""
    layer = get_channel_layer()
    if layer is None:
        return
    event = {
        "type": "payment.update",  # consumer method will be payment_update
        "payment": {
            "id": str(payment.id),
            "status": payment.status,
            "amount": str(payment.amount),
            "receipt": payment.mpesa_receipt_number,
        },
    }
    try:
        async_to_sync(layer.group_send)(user_group(payment.user_id), event)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to publish payment event payment_id=%s", payment.id)