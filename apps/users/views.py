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

from apps.users import lockout
from apps.users.otp import InvalidMfaSession, start_mfa_challenge, verify_otp
from apps.users.serializers import LoginSerializer, MfaVerifySerializer, RegisterSerializer
from apps.users.services import (
    InvalidGoogleToken,
    UnverifiedGoogleEmailConflict,
    authenticate_identifier,
    get_or_create_google_user,
    verify_google_id_token,
)
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


def _mfa_required_response(mfa_token: str) -> Response:
    return Response({"mfa_required": True, "mfa_token": mfa_token}, status=status.HTTP_200_OK)


class RegisterView(APIView):
    """
    POST /api/v1/auth/register/ — {"username", "email", "password"}
    -> 201 {"user": {...}, "access": "..."}  (+ refresh_token HttpOnly cookie)
    -> 400 field errors: duplicate username/email, or password fails validation
    """

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
    """
    POST /api/v1/auth/login/ — {"identifier", "password"}
    -> 200 {"user": {...}, "access": "..."}  (+ refresh cookie) — MFA off
    -> 200 {"mfa_required": true, "mfa_token": "..."}           — MFA on
    -> 401 {"detail": "Invalid username/email or password."}
    -> 429 {"detail": "Too many failed login attempts..."}
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        identifier = serializer.validated_data["identifier"]
        password = serializer.validated_data["password"]

        try:
            lockout.check_not_locked(identifier)
        except lockout.AccountLocked as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        user = authenticate_identifier(identifier, password)
        if user is None:
            attempts = lockout.record_failure(identifier)
            logger.warning("Failed login for identifier=%s (attempt %s)", identifier, attempts)
            return Response(
                {"detail": "Invalid username/email or password."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lockout.reset(identifier)

        if user.mfa_enabled:
            mfa_token = start_mfa_challenge(user)
            return _mfa_required_response(mfa_token)

        response = Response({"user": _user_payload(user)}, status=status.HTTP_200_OK)
        return _issue_tokens(response, user)


class MfaVerifyView(APIView):
    """
    POST /api/v1/auth/mfa/verify/ — {"mfa_token", "otp_code"}
    -> 200 {"user": {...}, "access": "..."} + refresh cookie
    -> 401 expired/invalid session, wrong code, or attempts exhausted
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = MfaVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = verify_otp(
                serializer.validated_data["mfa_token"],
                serializer.validated_data["otp_code"],
            )
        except InvalidMfaSession as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

        response = Response({"user": _user_payload(user)}, status=status.HTTP_200_OK)
        return _issue_tokens(response, user)


class GoogleAuthView(APIView):
    """POST /api/v1/auth/google/ — exchange a verified Google ID token for JWTs."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        token = request.data.get("id_token")
        try:
            payload = verify_google_id_token(token)
            user, created = get_or_create_google_user(payload)
        except InvalidGoogleToken as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        except UnverifiedGoogleEmailConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        response = Response(
            {"user": _user_payload(user)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
        return _issue_tokens(response, user)


def google_login_finish(request):
    """
    Plain Django view — allauth redirects here after finishing the Google
    OAuth2 exchange and logging the user into a Django *session*. Wired up
    via LOGIN_REDIRECT_URL. Mints a one-time handoff code, drops the
    session immediately (this backend is JWT-only), and redirects to the
    frontend with only the code in the URL — never the actual tokens.
    """
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
    POST /api/v1/auth/google/exchange/ — {"code": "<from /auth/callback>"}
    Exchanges the short-lived handoff code for a real access/refresh pair.
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

        from apps.users.models import User

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
