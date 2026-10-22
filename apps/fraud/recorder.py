"""If anything here raises, the whole
branch rolls back: the system fails closed and no money moves without a stored assessment.
"""
from apps.wallets import fraud as rules

from . import events
from .models import AuditLog, FraudCase, OutboxEvent, RiskAssessment

ENGINE_VERSION = "wallet-rules-1"
CATEGORY = {rules.ALLOW: "low", rules.REVIEW: "medium", rules.BLOCK: "high"}
Aud = OutboxEvent.Audience


def record_transfer_decision(*, txn, decision):
    flags = decision.flags_as_json()
    assessment = RiskAssessment.objects.create(
        transaction=txn, score=decision.score, category=CATEGORY[decision.outcome],
        recommended_action=decision.outcome, triggered_rules=[f["code"] for f in flags],
        reasons=flags, engine_version=ENGINE_VERSION,
    )
    AuditLog.objects.create(
        actor_role="system", action="risk.assessed", transaction=txn,
        metadata={"score": decision.score, "category": assessment.category, "engine": ENGINE_VERSION},
    )
    AuditLog.objects.create(  # diagram step 31
        actor_role="system", action=f"transfer.{txn.status}", transaction=txn,
        metadata={"amount": str(txn.amount), "currency": txn.currency},
    )

    # A pure limit breach is a user error, not an investigation; any other block opens an incident.
    incident = decision.outcome == rules.BLOCK and any(f.code not in rules.LIMIT_CODES for f in decision.flags)
    case = None
    if decision.outcome == rules.REVIEW or incident:
        case = FraudCase.objects.create(
            transaction=txn, assessment=assessment,
            kind=FraudCase.Kind.INCIDENT if incident else FraudCase.Kind.REVIEW,
            priority=FraudCase.Priority.HIGH if incident else FraudCase.Priority.MEDIUM,
        )
        AuditLog.objects.create(actor_role="system", action="case.opened", transaction=txn, case=case)
        events.emit(
            audience=Aud.ANALYST, event_type="fraud.alert.created",
            payload={"case": case.reference, "transaction": txn.reference, "kind": case.kind,
                     "priority": case.priority, "category": assessment.category, "score": assessment.score,
                     "amount": format(txn.amount, ".2f"), "currency": txn.currency},
        )

    if txn.status == txn.Status.COMPLETED:
        notify_completed(txn)
    else:
        event_type = "transaction.under_review" if txn.status == txn.Status.PENDING_REVIEW else "transaction.blocked"
        # Customers get status only: no score, no rules.
        events.emit(audience=Aud.CUSTOMER, user_id=txn.sender_wallet.user_id, event_type=event_type,
                    payload={"reference": txn.reference, "status": txn.status})
    return assessment, case


def notify_completed(txn):
    """Diagram steps 34-39: sender's balance update, recipient's 'money received'. Queued after COMMIT."""
    sender, receiver = txn.sender_wallet, txn.receiver_wallet
    events.emit(audience=Aud.CUSTOMER, user_id=sender.user_id, event_type="transaction.completed",
                payload={"reference": txn.reference, "status": txn.status, "balance": format(sender.balance, ".2f")})
    events.emit(audience=Aud.CUSTOMER, user_id=receiver.user_id, event_type="transaction.received",
                payload={"reference": txn.reference, "amount": format(txn.amount, ".2f"),
                         "currency": txn.currency, "balance": format(receiver.balance, ".2f")})