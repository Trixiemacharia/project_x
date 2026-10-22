from django.db import transaction
from django.utils import timezone

from apps.wallets import services as wallet_services

from . import events, recorder
from .models import AuditLog, FraudCase, FraudReview, OutboxEvent

Aud = OutboxEvent.Audience


class ReviewError(Exception):
    def __init__(self, code: str, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.code, self.detail, self.status_code = code, detail, status_code


@transaction.atomic
def review_case(*, case_id, analyst, decision: str, reason: str) -> FraudCase:
    """Lock order: case -> transaction -> wallets (wallets in pk order, inside the wallet service)."""
    reason = (reason or "").strip()
    if decision not in (FraudReview.Decision.APPROVE, FraudReview.Decision.REJECT) or len(reason) < 5:
        raise ReviewError("invalid_review", "A decision and a reason (min 5 characters) are required.")

    try:
        case = FraudCase.objects.select_for_update().get(pk=case_id)
    except FraudCase.DoesNotExist:
        raise ReviewError("not_found", "Case not found.", 404)
    if case.kind != FraudCase.Kind.REVIEW:
        raise ReviewError("not_reviewable", "Blocked incidents cannot be approved through review.", 409)
    if case.status != FraudCase.Status.OPEN:  # a concurrent analyst already decided
        raise ReviewError("already_reviewed", "This case has already been decided.", 409)
    if case.assigned_to_id not in (None, analyst.pk) and not analyst.has_perm("fraud.assign_case"):
        raise ReviewError("not_assigned", "This case is assigned to another analyst.", 403)

    approve = decision == FraudReview.Decision.APPROVE
    try:
        if approve:
            txn = wallet_services.approve_pending_transfer(case.transaction_id, analyst)
        else:
            txn = wallet_services.reject_pending_transfer(case.transaction_id, analyst)
    except wallet_services.TransferError as exc:
        raise ReviewError(exc.code, exc.detail, exc.status_code) from exc

    FraudReview.objects.create(case=case, analyst=analyst, decision=decision, reason=reason)
    case.status = FraudCase.Status.APPROVED if approve else FraudCase.Status.REJECTED
    case.resolved_at = timezone.now()
    case.save(update_fields=["status", "resolved_at", "updated_at"])
    AuditLog.objects.create(
        actor=analyst, actor_role="analyst", action=f"case.{decision}d", case=case, transaction=txn,
        metadata={"from": "open", "to": case.status},
    )
    if approve:
        recorder.notify_completed(txn)  # sender balance update + recipient "money received"
    else:
        events.emit(
            audience=Aud.CUSTOMER, user_id=txn.sender_wallet.user_id, event_type="transaction.rejected",
            payload={"reference": txn.reference, "status": txn.status, "balance": format(txn.sender_wallet.balance, ".2f")},
        )
    events.emit(
        audience=Aud.ANALYST, event_type="fraud.review.completed",
        payload={"case": case.reference, "transaction": txn.reference, "decision": decision},
    )
    return case