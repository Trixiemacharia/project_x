import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from . import daraja, services
from .models import MpesaPayment

logger = logging.getLogger(__name__)
Status = MpesaPayment.Status

_MESSAGES = {
    Status.SUCCESSFUL: "Your M-Pesa deposit of KES {amount} was successful.",
    Status.FAILED: "Your M-Pesa deposit of KES {amount} did not go through.",
    Status.CANCELLED: "Your M-Pesa deposit of KES {amount} was cancelled.",
    Status.TIMED_OUT: "Your M-Pesa deposit of KES {amount} timed out.",
    Status.NEEDS_REVIEW: "Your M-Pesa deposit of KES {amount} is being reviewed. We'll be in touch.",
}


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_payment_notification(self, payment_id: str):
    try:
        payment = MpesaPayment.objects.select_related("user").get(pk=payment_id)
    except MpesaPayment.DoesNotExist:
        return
    template = _MESSAGES.get(payment.status)
    if template is None:
        return
    try:
        send_mail(
            subject="M-Pesa deposit update",
            message=template.format(amount=int(payment.amount)),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[payment.user.email],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Payment notification failed payment_id=%s: %s", payment_id, exc)
        raise self.retry(exc=exc)


@shared_task
def reconcile_pending_payments() -> int:
    """
    Resolves payments whose callback never arrived: STK Query first, then expire
    anything older than MPESA_EXPIRY_SECONDS. Returns how many were resolved.
    """
    now = timezone.now()
    stale_before = now - timedelta(seconds=settings.MPESA_QUERY_AFTER_SECONDS)
    expire_before = now - timedelta(seconds=settings.MPESA_EXPIRY_SECONDS)

    candidates = MpesaPayment.objects.filter(
        status__in=[Status.PENDING, Status.PROCESSING], created_at__lt=stale_before
    ).order_by("created_at")[:200]

    resolved = 0
    for payment in candidates:
        if payment.checkout_request_id:
            try:
                data = daraja.stk_query(payment.checkout_request_id)
                services.apply_result(
                    payment.pk, result_code=int(data["ResultCode"]), result_desc=data.get("ResultDesc", "")
                )
                resolved += 1
                continue
            except daraja.DarajaRejected as exc:
                if exc.error_code != daraja.STILL_PROCESSING_CODE:
                    logger.warning("STK query rejected payment_id=%s: %s", payment.id, exc)
            except (daraja.DarajaUnavailable, KeyError, ValueError) as exc:
                logger.warning("STK query failed payment_id=%s: %s", payment.id, exc)

        if payment.created_at < expire_before:
            services.apply_result(payment.pk, result_code=1037, result_desc="No result received before expiry.")
            resolved += 1
    return resolved