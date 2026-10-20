from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework.test import APIClient

from apps.fraud.models import AuditLog, FraudCase, OutboxEvent, RiskAssessment
from apps.wallets import services
from apps.wallets.models import Wallet
from apps.wallets.tests.conftest import add_beneficiary, make_user

pytestmark = pytest.mark.django_db
PERM = "review_transaction"


def grant(user):
    user.user_permissions.add(Permission.objects.get(codename=PERM))
    return get_user_model().objects.get(pk=user.pk)  # fresh instance: permission cache is per-object


def make_analyst(name="analyst"):
    return grant(get_user_model().objects.create_user(username=name, email=f"{name}@example.com", password="x"))


def client_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


@pytest.fixture
def flagged(db):
    sender, receiver = make_user("sender", funds=30000), make_user("receiver")
    fresh = add_beneficiary(sender, receiver, aged=False)  # new beneficiary 35 + drain 20 = review
    txn = services.transfer(user=sender, beneficiary=fresh, amount="29000", idempotency_key="k1").txn
    return sender, receiver, txn


def review(user, case, decision="approve", reason="Confirmed with the customer"):
    return client_for(user).post(f"/api/v1/fraud/cases/{case.reference}/review/", {"decision": decision, "reason": reason}, format="json")


def test_clean_transfer_gets_assessment_but_no_case(db):
    sender, receiver = make_user("s", funds=10000), make_user("r")
    txn = services.transfer(user=sender, beneficiary=add_beneficiary(sender, receiver), amount="100", idempotency_key="k").txn
    assert txn.status == "completed"
    assert RiskAssessment.objects.get(transaction=txn).category == "low"
    assert not FraudCase.objects.exists()


def test_review_range_transfer_opens_case_and_events(flagged):
    _, _, txn = flagged
    case = FraudCase.objects.get(transaction=txn)
    assert case.kind == "review" and case.assessment.category == "medium"
    assert OutboxEvent.objects.filter(audience="analyst", event_type="fraud.alert.created").exists()
    customer_event = OutboxEvent.objects.get(audience="customer", event_type="transaction.under_review")
    assert "score" not in customer_event.payload  # customers never see risk details


def test_approve_completes_and_second_review_conflicts(flagged):
    _, _, txn = flagged
    case = FraudCase.objects.get(transaction=txn)
    assert review(make_analyst(), case).status_code == 200
    txn.refresh_from_db()
    assert txn.status == "completed"
    again = review(make_analyst("second"), case, "reject")
    assert again.status_code == 409 and again.data["code"] == "already_reviewed"
    assert AuditLog.objects.filter(action="case.approved", case=case).count() == 1


def test_reject_releases_the_hold(flagged):
    sender, _, txn = flagged
    assert Wallet.objects.get(user=sender).balance == D("1000")  # 30000 - 29000 held
    assert review(make_analyst(), FraudCase.objects.get(transaction=txn), "reject").status_code == 200
    assert Wallet.objects.get(user=sender).balance == D("30000")


def test_reason_is_required(flagged):
    case = FraudCase.objects.get(transaction=flagged[2])
    assert review(make_analyst(), case, reason="  ").status_code == 400


def test_customers_cannot_use_analyst_api(flagged):
    assert client_for(make_user("customer")).get("/api/v1/fraud/cases/").status_code == 403


def test_analyst_cannot_review_own_transfer(flagged):
    sender, _, txn = flagged
    resp = review(grant(sender), FraudCase.objects.get(transaction=txn))
    assert resp.status_code == 403 and resp.data["code"] == "self_review"