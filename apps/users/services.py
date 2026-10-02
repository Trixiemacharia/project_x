import logging

from django.contrib.auth import authenticate, get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


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