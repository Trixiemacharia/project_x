import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_KEY_PREFIX = "failed_login:"


class AccountLocked(Exception):
    """Raised when an identifier has exceeded the failed-login threshold."""


def _key(identifier: str) -> str:
    return f"{_KEY_PREFIX}{identifier.strip().lower()}"


def check_not_locked(identifier: str) -> None:
    attempts = cache.get(_key(identifier), 0)
    if attempts >= settings.FAILED_LOGIN_MAX_ATTEMPTS:
        raise AccountLocked(
            "Too many failed login attempts. Please try again in a few minutes."
        )


def record_failure(identifier: str) -> int:
    key = _key(identifier)
    try:
        attempts = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=settings.FAILED_LOGIN_LOCKOUT_SECONDS)
        attempts = 1

    if attempts >= settings.FAILED_LOGIN_MAX_ATTEMPTS:
        logger.warning("Lockout threshold reached for identifier=%s", identifier)
    return attempts


def reset(identifier: str) -> None:
    cache.delete(_key(identifier))