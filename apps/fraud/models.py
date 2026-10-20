import secrets
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


def case_reference() -> str:
    return "FC" + secrets.token_hex(5).upper()


class AppendOnly:
    """Rows can be inserted but never edited or deleted through the ORM instance API."""

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError(f"{type(self).__name__} rows are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError(f"{type(self).__name__} rows are append-only")


class RiskAssessment(AppendOnly, models.Model):
    class Category(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.ForeignKey("wallets.Transaction", on_delete=models.PROTECT, related_name="assessments")
    score = models.PositiveSmallIntegerField()
    category = models.CharField(max_length=10, choices=Category.choices)
    recommended_action = models.CharField(max_length=10)  # allow | review | block (the engine's advice)
    triggered_rules = models.JSONField(default=list)
    reasons = models.JSONField(default=list)  # [{code, score, detail}] - analysts only
    engine_version = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["category", "-created_at"])]


class FraudCase(models.Model):
    class Kind(models.TextChoices):
        REVIEW = "review", "Manual review"  # funds held, analyst decides
        INCIDENT = "incident", "Blocked incident"  # already blocked, for investigation

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=20, unique=True, default=case_reference, editable=False)
    transaction = models.OneToOneField("wallets.Transaction", on_delete=models.PROTECT, related_name="fraud_case")
    assessment = models.ForeignKey(RiskAssessment, on_delete=models.PROTECT, related_name="cases")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=10, choices=Priority.choices)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("assign_case", "Can assign fraud cases and act on others' cases")]
        indexes = [models.Index(fields=["status", "-created_at"]), models.Index(fields=["kind", "status"])]


class FraudReview(AppendOnly, models.Model):
    class Decision(models.TextChoices):
        APPROVE = "approve", "Approve"
        REJECT = "reject", "Reject"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(FraudCase, on_delete=models.PROTECT, related_name="reviews")
    analyst = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decision = models.CharField(max_length=10, choices=Decision.choices)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            # One final decision per case: a duplicate review fails at the database level too.
            models.UniqueConstraint(fields=["case"], name="one_final_review_per_case"),
        ]


class AuditLog(AppendOnly, models.Model):
    id = models.BigAutoField(primary_key=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor_role = models.CharField(max_length=30)  # system | analyst | customer
    action = models.CharField(max_length=50)
    transaction = models.ForeignKey("wallets.Transaction", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    case = models.ForeignKey(FraudCase, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    metadata = models.JSONField(default=dict)  # never put secrets here
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["action", "-id"])]


class OutboxEvent(models.Model):
    """Written in the same DB transaction as the change; pushed after commit; replayable over REST."""

    class Audience(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        ANALYST = "analyst", "Analyst"

    id = models.BigAutoField(primary_key=True)  # also the client's recovery cursor
    audience = models.CharField(max_length=10, choices=Audience.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    event_type = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["audience", "id"]), models.Index(fields=["user", "id"])]

    def as_message(self) -> dict:
        return {"id": self.pk, "type": self.event_type, "payload": self.payload, "at": self.created_at.isoformat()}