import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from .utils import mask_name


def generate_account_number() -> str:
    return str(secrets.randbelow(9 * 10**9) + 10**9)  # 10 digits, never leading zero


def generate_reference() -> str:
    return "TX" + secrets.token_hex(5).upper()


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wallet")
    account_number = models.CharField(max_length=20, unique=True, default=generate_account_number, editable=False)
    currency = models.CharField(max_length=3, default="KES")
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    is_frozen = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(balance__gte=0), name="wallet_balance_non_negative"),
        ]

    def __str__(self):
        return f"{self.account_number} ({self.currency} {self.balance})"


class Beneficiary(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="beneficiaries")
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="saved_by")
    nickname = models.CharField(max_length=60)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nickname"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "wallet"], name="uniq_beneficiary_per_owner_wallet"),
        ]

    @property
    def account_number(self) -> str:
        return self.wallet.account_number

    @property
    def account_name(self) -> str:
        return mask_name(self.wallet.user)

    def __str__(self):
        return f"{self.nickname} -> {self.wallet.account_number}"


class Transaction(models.Model):
    class Type(models.TextChoices):
        TRANSFER = "transfer", "Transfer"
        DEPOSIT = "deposit", "Deposit"

    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        PENDING_REVIEW = "pending_review", "Pending review"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=20, unique=True, default=generate_reference, editable=False)
    type = models.CharField(max_length=10, choices=Type.choices)
    sender_wallet = models.ForeignKey(Wallet, null=True, blank=True, on_delete=models.PROTECT, related_name="sent_transactions")
    receiver_wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="received_transactions")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    description = models.CharField(max_length=140, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)

    # Internal only: never serialised to end users.
    fraud_score = models.PositiveSmallIntegerField(default=0)
    fraud_flags = models.JSONField(default=list, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("review_transaction", "Can approve or reject flagged transactions")]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="txn_amount_positive"),
            models.UniqueConstraint(fields=["sender_wallet", "idempotency_key"], name="txn_unique_idempotency_per_sender"),
        ]
        indexes = [
            models.Index(fields=["sender_wallet", "-created_at"]),
            models.Index(fields=["receiver_wallet", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.reference} {self.amount} [{self.status}]"