import logging
import re

from django.conf import settings
from django.db import IntegrityError, transaction

from django.contrib.auth import authenticate, get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


class InvalidGoogleToken(Exception):
    """The submitted Google ID token cannot be trusted."""


class UnverifiedGoogleEmailConflict(Exception):
    """An unverified Google email matched an existing local account."""


def verify_google_id_token(token: str) -> dict:
    """Verify a Google ID token and return its claims.

    Imports are intentionally local so password-only installations do not need
    Google's client package merely to start Django.
    """
    if not token:
        raise InvalidGoogleToken("Invalid or expired Google token.")

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        audience = settings.SOCIALACCOUNT_PROVIDERS["google"]["APP"]["client_id"]
        payload = id_token.verify_oauth2_token(token, google_requests.Request(), audience)
    except Exception as exc:
        logger.info("Google ID token verification failed: %s", exc)
        raise InvalidGoogleToken("Invalid or expired Google token.") from exc

    if payload.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise InvalidGoogleToken("Invalid or expired Google token.")
    if not payload.get("sub") or not payload.get("email"):
        raise InvalidGoogleToken("Invalid or expired Google token.")
    return payload


def _google_username(email: str) -> str:
    """Produce a unique, model-valid username for a first-time Google user."""
    base = re.sub(r"[^\w.@+-]", "-", email.split("@", 1)[0]).strip(".-") or "google-user"
    base = base[:140]
    username = base
    suffix = 1
    while User.objects.filter(username__iexact=username).exists():
        suffix += 1
        username = f"{base[:150 - len(str(suffix)) - 1]}-{suffix}"
    return username


def get_or_create_google_user(payload: dict):
    """Resolve a verified Google identity without allowing unsafe auto-linking."""
    subject = payload.get("sub")
    email = payload.get("email", "").strip().lower()
    if not subject or not email:
        raise InvalidGoogleToken("Invalid or expired Google token.")

    with transaction.atomic():
        user = User.objects.filter(google_sub=subject).first()
        if user:
            return user, False

        existing = User.objects.filter(email__iexact=email).first()
        if existing:
            if not payload.get("email_verified"):
                raise UnverifiedGoogleEmailConflict(
                    "Google did not verify the email address for this existing account."
                )
            if existing.google_sub:
                raise InvalidGoogleToken("This Google account is linked to another user.")
            existing.google_sub = subject
            existing.save(update_fields=["google_sub"])
            return existing, False

        if not payload.get("email_verified"):
            raise InvalidGoogleToken("Google did not verify the email address.")
        try:
            user = User.objects.create(
                username=_google_username(email),
                email=email,
                google_sub=subject,
                first_name=payload.get("given_name", ""),
                last_name=payload.get("family_name", ""),
            )
        except IntegrityError as exc:
            raise InvalidGoogleToken("Unable to create Google account.") from exc
        user.set_unusable_password()
        user.save(update_fields=["password"])
        return user, True


def authenticate_identifier(identifier: str, password: str):
    """
    Resolves `identifier` (username OR email) to the underlying username
    and runs it through Django's normal authenticate(). Returns None on
    any failure — unknown identifier, wrong password, or an inactive/
    Google-only (unusable-password) account — without distinguishing
    which, so the caller can return one generic response.
    """
    username = identifier
    if "@" in identifier:
        match = User.objects.filter(email__iexact=identifier).first()
        if match:
            username = match.username

    user = authenticate(username=username, password=password)
    if user is None or not user.is_active:
        return None
    return user
