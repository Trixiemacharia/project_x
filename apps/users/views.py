import logging
import secrets

from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.core.cache import cache
from django.http import HttpResponseRedirect
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.serializers import LoginSerializer, RegisterSerializer
from apps.users.throttles import AuthRateThrottle

logger = logging.getLogger(__name__)

GOOGLE_HANDOFF_CACHE_PREFIX = "google_handoff:"
GOOGLE_HANDOFF_TTL_SECONDS = 60


def _user_payload(user) -> dict:
    return {"id": user.id, "username": user.username, "email": user.email}


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        path=settings.REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )


def _issue_tokens(response: Response, user) -> Response:
    refresh = RefreshToken.for_user(user)
    response.data["access"] = str(refresh.access_token)
    _set_refresh_cookie(response, str(refresh))
    return response


class RegisterView(APIView):
    """POST /api/v1/auth/register/ — {"username", "email", "password"}"""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        logger.info("New user registered: user_id=%s", user.id)
        response = Response({"user": _user_payload(user)}, status=status.HTTP_201_CREATED)
        return _issue_tokens(response, user)


class LoginView(APIView):
    """POST /api/v1/auth/login/ — {"identifier", "password"}"""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        response = Response({"user": _user_payload(user)}, status=status.HTTP_200_OK)
        return _issue_tokens(response, user)


def google_login_finish(request):
    if not request.user.is_authenticated:
        return HttpResponseRedirect(f"{settings.FRONTEND_URL}/auth/error?reason=google_login_failed")

    user_id = request.user.id
    code = secrets.token_urlsafe(32)
    cache.set(f"{GOOGLE_HANDOFF_CACHE_PREFIX}{code}", user_id, timeout=GOOGLE_HANDOFF_TTL_SECONDS)
    django_logout(request)

    logger.info("Google login handoff issued for user_id=%s", user_id)
    return HttpResponseRedirect(f"{settings.FRONTEND_URL}/auth/callback?code={code}")


class GoogleExchangeView(APIView):
    """
    POST /api/v1/auth/google/exchange/ — {"code": "<from the /auth/callback redirect>"}

    Exchanges the short-lived handoff code for a real access/refresh pair,
    exactly like /auth/login/ or /auth/register/ do. The code is deleted
    from cache on first use — a replayed code always fails, even within
    its TTL.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        code = request.data.get("code")
        if not code:
            return Response({"detail": "code is required."}, status=status.HTTP_400_BAD_REQUEST)

        cache_key = f"{GOOGLE_HANDOFF_CACHE_PREFIX}{code}"
        user_id = cache.get(cache_key)
        if user_id is None:
            return Response({"detail": "Invalid or expired code."}, status=status.HTTP_401_UNAUTHORIZED)
        cache.delete(cache_key)

        from apps.users.models import User  # local import avoids app-loading order issues

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User no longer exists."}, status=status.HTTP_401_UNAUTHORIZED)

        response = Response({"user": _user_payload(user)}, status=status.HTTP_200_OK)
        return _issue_tokens(response, user)


class RefreshView(APIView):
    """POST /api/v1/auth/token/refresh/ — no body, reads the refresh cookie."""

    permission_classes = [AllowAny]

    def post(self, request):
        raw_token = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
        if not raw_token:
            return Response(
                {"detail": "No refresh token cookie present."}, status=status.HTTP_401_UNAUTHORIZED
            )

        serializer = TokenRefreshSerializer(data={"refresh": raw_token})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

        data = serializer.validated_data
        response = Response({"access": data["access"]}, status=status.HTTP_200_OK)
        new_refresh = data.get("refresh")
        if new_refresh:
            _set_refresh_cookie(response, new_refresh)
        return response


class LogoutView(APIView):
    """POST /api/v1/auth/logout/ — requires a valid access token."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw_token = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
        if raw_token:
            try:
                RefreshToken(raw_token).blacklist()
            except TokenError:
                logger.info("Logout: refresh token already invalid or blacklisted")

        response = Response({"detail": "Logged out."}, status=status.HTTP_200_OK)
        response.delete_cookie(settings.REFRESH_COOKIE_NAME, path=settings.REFRESH_COOKIE_PATH)
        return response