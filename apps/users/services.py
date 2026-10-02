import logging
from django.conf import settings
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(name)

class InvalidGoogleToken(Exception):
    """Raised when a Google ID token fails verification."""

    def verify_google_id_token(token: str) -> dict:
        """Verifies a Google-issued ID token server-side and returns its claims."""
        if not settings.GOOGLE_OAUTH_CLIENT_ID:
        # Misconfiguration, not a client error — fail loudly in logs.
        logger.error("Google auth attempted with GOOGLE_OAUTH_CLIENT_ID unset")
        raise InvalidGoogleToken("Google sign-in is not configured.")


        try:
            idinfo = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            settings.GOOGLE_OAUTH_CLIENT_ID,
    )
        except ValueError as exc:
            logger.warning("Google ID token verification failed: %s", exc)
            raise InvalidGoogleToken("Invalid or expired Google token.") from exc

            if idinfo.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
                logger.warning("Google ID token had unexpected issuer: %s", idinfo.get("iss"))
                raise InvalidGoogleToken("Invalid token issuer.")

return idinfo

class UnverifiedGoogleEmailConflict(Exception):
    """Raised when a Google token's email matches an existing local account but Google has not verified that email. Auto-linking in this case would let anyone who controls an unverified Google email take over an existing password account with the same address — so we refuse the link rather than silently proceeding. """
    def get_or_create_user_from_google(idinfo: dict):
        """Resolves a verified Google ID token's claims to a local User.
           Priority order:
             1. An existing user already linked via google_sub -> that user, unchanged.
             2. An existing user with a matching, *unverified-by-us* email but a
            *Google-verified* email claim -> link google_sub to that account.
             3. An existing user with a matching email but Google has NOT verified
            it -> refuse (raises UnverifiedGoogleEmailConflict), no account
            is created or modified.
             4. No match -> create a new user with an unusable password (Google
            is their only sign-in method until/unless they set one).

            Returns (user, created: bool)."""
        from apps.users.models import User

        google_sub = idinfo["sub"]
        email = idinfo["email"]
        email_verified = bool(idinfo.get("email_verified"))

        existing_by_sub = User.objects.filter(google_sub=google_sub).first()
        if existing_by_sub:
            
    return existing_by_sub, False

existing_by_email = User.objects.filter(email__iexact=email).first()
if existing_by_email:
    if not email_verified:
        logger.warning(
            "Refused Google auto-link: email=%s matches an existing account "
            "but Google did not report it as verified",
            email,
        )
        raise UnverifiedGoogleEmailConflict(
            "An account with this email already exists. Log in with your "
            "password first, then link Google from your account settings."
        )
    existing_by_email.google_sub = google_sub
    existing_by_email.is_email_verified = True
    existing_by_email.save(update_fields=["google_sub", "is_email_verified"])
    return existing_by_email, False

username_base = email.split("@")[0]
username = username_base
suffix = 0
while User.objects.filter(username=username).exists():
    suffix += 1
    username = f"{username_base}{suffix}"

user = User.objects.create(
    username=username,
    email=email,
    google_sub=google_sub,
    is_email_verified=email_verified,
    first_name=idinfo.get("given_name", "")[:150],
    last_name=idinfo.get("family_name", "")[:150],
)
user.set_unusable_password()
user.save(update_fields=["password"])
return user, True
