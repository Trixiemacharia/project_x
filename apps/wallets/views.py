from django.db.models import Q
from django.utils.dateparse import parse_date
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Beneficiary, Transaction, Wallet
from .serializers import (
    BeneficiarySerializer, BeneficiaryUpdateSerializer, TransactionSerializer, TransferSerializer, WalletSerializer,
)
from .throttles import BeneficiaryThrottle, TransferThrottle


def visible_transactions(wallet):
    """Senders see every attempt; receivers only see money that actually arrived."""
    return Transaction.objects.filter(
        Q(sender_wallet=wallet) | Q(receiver_wallet=wallet, status=Transaction.Status.COMPLETED)
    ).select_related("sender_wallet__user", "receiver_wallet__user")


class WalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(WalletSerializer(services.ensure_wallet(request.user)).data)


class BeneficiaryListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [BeneficiaryThrottle]

    def get_queryset(self):
        return Beneficiary.objects.filter(owner=self.request.user).select_related("wallet__user")

    def get_serializer_class(self):
        return BeneficiarySerializer


class BeneficiaryDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [BeneficiaryThrottle]
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return Beneficiary.objects.filter(owner=self.request.user).select_related("wallet__user")

    def get_serializer_class(self):
        return BeneficiaryUpdateSerializer if self.request.method == "PATCH" else BeneficiarySerializer


class TransferView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [TransferThrottle]

    def post(self, request):
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key or len(key) > 64:
            return Response(
                {"detail": "A valid Idempotency-Key header (max 64 characters) is required.", "code": "idempotency_key_required"},
                status=400,
            )
        serializer = TransferSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            result = services.transfer(
                user=request.user, beneficiary=data["beneficiary"], amount=data["amount"],
                description=data["description"], idempotency_key=key,
            )
        except services.TransferError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)

        txn = result.txn
        if txn.status == Transaction.Status.REJECTED:
            if services.is_limit_breach(txn):
                code, detail = "limit_exceeded", "This transfer exceeds your transaction limits."
            else:  # never reveal which rule fired
                code = "transfer_blocked"
                detail = "We couldn't complete this transfer for security reasons. Contact support if you think this is a mistake."
            return Response({"detail": detail, "code": code, "reference": txn.reference}, status=403)

        wallet = Wallet.objects.get(user=request.user)
        body = {
            "transaction": TransactionSerializer(txn, context={"wallet": wallet}).data,
            "balance": format(wallet.balance, ".2f"),
        }
        if txn.status == Transaction.Status.PENDING_REVIEW:
            body["detail"] = "Your transfer is being reviewed and will complete shortly."
            http_status = 202
        else:
            http_status = 200 if result.replayed else 201
        response = Response(body, status=http_status)
        if result.replayed:
            response["Idempotent-Replayed"] = "true"
        return response


class TransactionPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class _WalletScopedMixin:
    permission_classes = [IsAuthenticated]
    serializer_class = TransactionSerializer

    def get_wallet(self):
        if not hasattr(self, "_wallet"):
            self._wallet = services.ensure_wallet(self.request.user)
        return self._wallet

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "wallet": self.get_wallet()}


class TransactionListView(_WalletScopedMixin, generics.ListAPIView):
    """GET /transactions/?direction=sent|received&status=&type=&from=YYYY-MM-DD&to=YYYY-MM-DD"""

    pagination_class = TransactionPagination

    def get_queryset(self):
        wallet = self.get_wallet()
        params = self.request.query_params
        qs = visible_transactions(wallet)

        direction = params.get("direction")
        if direction == "sent":
            qs = qs.filter(sender_wallet=wallet)
        elif direction == "received":
            qs = qs.filter(receiver_wallet=wallet)
        elif direction:
            raise ValidationError({"direction": "Use 'sent' or 'received'."})

        for param in ("status", "type"):
            if params.get(param):
                qs = qs.filter(**{param: params[param]})

        for param, lookup in (("from", "created_at__date__gte"), ("to", "created_at__date__lte")):
            if params.get(param):
                try:
                    day = parse_date(params[param])
                except ValueError:
                    day = None
                if day is None:
                    raise ValidationError({param: "Invalid date. Use YYYY-MM-DD."})
                qs = qs.filter(**{lookup: day})
        return qs


class TransactionDetailView(_WalletScopedMixin, generics.RetrieveAPIView):
    def get_queryset(self):
        return visible_transactions(self.get_wallet())  # others' transactions -> 404