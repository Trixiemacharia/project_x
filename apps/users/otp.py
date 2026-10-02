import hashlib
import hmac
import logging
import secrets
import time

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_OTP_CACHE_PREFIX = "mfa_otp:"


class InvalidMfaSession(Exception):
    """Missing/expired session, exhausted attempts, or a wrong code — one type for all."""


def _hash_otp(otp_code: str) -> str:
    return hashlib.sha256(otp_code.encode()).hexdigest()


def start_mfa_challenge(user) -> str:
    otp_code = f"{secrets.randbelow(10 ** settings.OTP_LENGTH):0{settings.OTP_LENGTH}d}"
    mfa_token = secrets.token_urlsafe(32)
    session = {
        "user_id": user.id,
        "otp_hash": _hash_otp(otp_code),
        "attempts": 0,
        "expires_at": time.time() + settings.OTP_TTL_SECONDS,
    }
    cache.set(f"{_OTP_CACHE_PREFIX}{mfa_token}", session, timeout=settings.OTP_TTL_SECONDS)

    from apps.users.tasks import send_otp_email

    send_otp_email.delay(user.id, otp_code)
    logger.info("MFA challenge started for user_id=%s", user.id)
    return mfa_token


def verify_otp(mfa_token: str, otp_code: str):
    from apps.users.models import User

    key = f"{_OTP_CACHE_PREFIX}{mfa_token}"
    session = cache.get(key)
    if session is None:
        raise InvalidMfaSession("This MFA session has expired. Please log in again.")

    if session["attempts"] >= settings.OTP_MAX_ATTEMPTS:
        cache.delete(key)
        raise InvalidMfaSession("Too many incorrect codes. Please log in again.")

    if not hmac.compare_digest(_hash_otp(otp_code), session["otp_hash"]):
        session["attempts"] += 1
        if session["attempts"] >= settings.OTP_MAX_ATTEMPTS:
            cache.delete(key)
            logger.warning("MFA attempts exhausted for user_id=%s", session["user_id"])
            raise InvalidMfaSession("Too many incorrect codes. Please log in again.")
        remaining_ttl = max(1, int(session["expires_at"] - time.time()))
        cache.set(key, session, timeout=remaining_ttl)
        raise InvalidMfaSession("Incorrect code.")

    cache.delete(key)
    try:
        return User.objects.get(id=session["user_id"], is_active=True)
    except User.DoesNotExist:
        raise InvalidMfaSession("Account no longer available.")