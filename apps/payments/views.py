import hmac
import logging
import re

from django.conf import settings
from django.http import Http404
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import MpesaPayment
from .serializers import MpesaPaymentSerializer, StkPushRequestSerializer
from .throttles import StkPushThrottle

logger = logging.getLogger(__name__)
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


class MpesaStkPushView(APIView):
    """
    POST /api/v1/payments/mpesa/stk/   (JWT + Idempotency-Key header)
    Body: {"amount": 500, "phone_number": "0712345678"}
    -> 202 new payment | 200 replay of an existing one (no second STK push)
    -> 400 bad key/body or no wallet | 403 frozen wallet | 422 key reused with different body
    -> 429 throttled (5/min)
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [StkPushThrottle]

    def post(self, request):
        key = request.headers.get("Idempotency-Key", "")
        if not IDEMPOTENCY_KEY_RE.match(key):
            return Response(
                {"detail": "A valid Idempotency-Key header is required (8-64 chars: letters, digits, - and _)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = StkPushRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            payment, created = services.initiate_stk_push(
                user=request.user,
                amount=serializer.validated_data["amount"],
                phone=serializer.validated_data["phone_number"],
                idempotency_key=key,
            )
        except services.WalletUnavailable as exc:
            if exc.reason == "frozen":
                return Response({"detail": "Your wallet is frozen."}, status=status.HTTP_403_FORBIDDEN)
            return Response({"detail": "No wallet found for this account."}, status=status.HTTP_400_BAD_REQUEST)
        except services.IdempotencyConflict:
            return Response(
                {"detail": "This Idempotency-Key was already used with a different amount or phone number."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(
            MpesaPaymentSerializer(payment).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class MpesaPaymentListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = MpesaPaymentSerializer

    def get_queryset(self):
        return MpesaPayment.objects.filter(user=self.request.user)


class MpesaPaymentDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = MpesaPaymentSerializer

    def get_queryset(self):
        # Filtering by owner means someone else's id is a plain 404 (no IDOR, no existence leak).
        return MpesaPayment.objects.filter(user=self.request.user)


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class MpesaCallbackView(APIView):
    """
    POST /api/v1/payments/mpesa/callback/<secret>/
    Called by Safaricom. Unauthenticated by design: protected by the secret in the URL,
    an optional source-IP allowlist, and strict matching inside services.handle_callback.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request, secret):
        expected = settings.MPESA_CALLBACK_SECRET
        if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
            raise Http404

        ip = _client_ip(request)
        allowed = settings.MPESA_CALLBACK_ALLOWED_IPS
        if allowed and ip not in allowed:
            logger.warning("Callback from non-allowlisted IP %s", ip)
            raise Http404

        try:
            outcome = services.handle_callback(request.data, ip)
        except ValueError:
            return Response({"ResultCode": 1, "ResultDesc": "Malformed"}, status=status.HTTP_400_BAD_REQUEST)

        logger.info("M-Pesa callback handled outcome=%s", outcome)
        return Response({"ResultCode": 0, "ResultDesc": "Accepted"})