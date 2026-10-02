import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def send_otp_email(self, user_id: int, otp_code: str):
    from apps.users.models import User

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.warning("send_otp_email: user_id=%s no longer exists", user_id)
        return

    try:
        send_mail(
            subject="Your verification code",
            message=(
                f"Your verification code is {otp_code}. "
                f"It expires in {settings.OTP_TTL_SECONDS // 60} minutes."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("send_otp_email failed for user_id=%s: %s", user_id, exc)
        raise self.retry(exc=exc)