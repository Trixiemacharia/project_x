import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect

logger = logging.getLogger(__name__)
User = get_user_model()


class GoogleSocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing:
            return

        email = sociallogin.account.extra_data.get("email")
        email_verified = bool(sociallogin.account.extra_data.get("email_verified"))
        if not email:
            return

        try:
            existing_user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return

        if not email_verified:
            logger.warning(
                "Refused Google auto-link: email=%s matches an existing account "
                "but Google did not report it as verified",
                email,
            )
            raise ImmediateHttpResponse(
                HttpResponseRedirect(
                    f"{settings.FRONTEND_URL}/auth/error?reason=unverified_email_conflict"
                )
            )

        sociallogin.connect(request, existing_user)


class NoSignupAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return False