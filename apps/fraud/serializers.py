from rest_framework import serializers

from apps.wallets.models import Transaction

from .models import FraudCase, FraudReview


def txn_summary(txn) -> dict:
    return {"reference": txn.reference, "type": txn.type, "amount": format(txn.amount, ".2f"),
            "currency": txn.currency, "status": txn.status, "created_at": txn.created_at}


class CaseListSerializer(serializers.ModelSerializer):
    transaction = serializers.SerializerMethodField()
    risk = serializers.SerializerMethodField()

    class Meta:
        model = FraudCase
        fields = ["reference", "kind", "status", "priority", "created_at", "resolved_at", "transaction", "risk"]

    def get_transaction(self, obj):
        return txn_summary(obj.transaction)

    def get_risk(self, obj):
        a = obj.assessment
        return {"score": a.score, "category": a.category, "recommended_action": a.recommended_action,
                "triggered_rules": a.triggered_rules}


class CaseDetailSerializer(CaseListSerializer):
    reasons = serializers.SerializerMethodField()
    reviews = serializers.SerializerMethodField()
    recent_history = serializers.SerializerMethodField()

    class Meta(CaseListSerializer.Meta):
        fields = CaseListSerializer.Meta.fields + ["reasons", "reviews", "recent_history"]

    def get_reasons(self, obj):
        return obj.assessment.reasons

    def get_reviews(self, obj):
        return [{"analyst": r.analyst.get_username(), "decision": r.decision, "reason": r.reason,
                 "created_at": r.created_at} for r in obj.reviews.select_related("analyst")]

    def get_recent_history(self, obj):
        qs = (Transaction.objects.filter(sender_wallet_id=obj.transaction.sender_wallet_id)
              .exclude(pk=obj.transaction_id).select_related("receiver_wallet")[:10])
        return [{**txn_summary(t), "to_account": t.receiver_wallet.account_number} for t in qs]


class ReviewSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=FraudReview.Decision.choices)
    reason = serializers.CharField(min_length=5, max_length=500, trim_whitespace=True)