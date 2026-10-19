
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class MpesaPayment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"  # row created, STK not yet acknowledged by Daraja
        PROCESSING = "processing", "Processing"  # STK accepted, waiting for the customer's PIN
        SUCCESSFUL = "successful", "Successful"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled by user"
        TIMED_OUT = "timed_out", "Timed out"
        NEEDS_REVIEW = "needs_review", "Needs review"  # money moved but we did not credit

    FINAL_STATUSES = frozenset(
        {Status.SUCCESSFUL, Status.FAILED, Status.CANCELLED, Status.NEEDS_REVIEW}
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="mpesa_payments")
    wallet = models.ForeignKey("wallets.Wallet", on_delete=models.PROTECT, related_name="mpesa_payments")
    idempotency_key = models.CharField(max_length=64)
    request_fingerprint = models.CharField(max_length=64)
    phone_number = models.CharField(max_length=12)  # 2547XXXXXXXX
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    merchant_request_id = models.CharField(max_length=64, blank=True)
    checkout_request_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    mpesa_receipt_number = models.CharField(max_length=20, null=True, blank=True, unique=True)
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.CharField(max_length=255, blank=True)
    transaction = models.OneToOneField(
        "wallets.Transaction", null=True, blank=True, on_delete=models.PROTECT, related_name="mpesa_payment"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "idempotency_key"], name="mpesa_unique_idempotency_per_user"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="mpesa_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.id} {self.amount} [{self.status}]"


class MpesaCallback(models.Model):
    """Audit log of every callback received, whatever happened to it."""

    checkout_request_id = models.CharField(max_length=64, blank=True, db_index=True)
    payment = models.ForeignKey(MpesaPayment, null=True, blank=True, on_delete=models.SET_NULL, related_name="callbacks")
    payload = models.JSONField()
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    outcome = models.CharField(max_length=30, blank=True)  # applied / duplicate / unmatched / malformed / ...
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]