from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.wallets import services
from apps.wallets.models import Transaction

from .conftest import add_beneficiary, balance, make_user

pytestmark = pytest.mark.django_db


def pay(client, beneficiary, amount, key="key-1", **extra):
    return client.post(
        reverse("wallet-transfer"),
        {"beneficiary": str(beneficiary.id), "amount": str(amount), **extra},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )


def test_successful_transfer_moves_money(api, alice, bob):
    b = add_beneficiary(alice, bob)
    response = pay(api, b, "5000.00", description="rent")
    assert response.status_code == 201
    assert response.data["transaction"]["direction"] == "debit"
    assert response.data["transaction"]["status"] == "completed"
    assert response.data["balance"] == "95000.00"
    assert balance(alice) == Decimal("95000.00")
    assert balance(bob) == Decimal("5000.00")
    assert "fraud_score" not in response.data["transaction"]


def test_idempotency_key_is_required(api, alice, bob):
    b = add_beneficiary(alice, bob)
    response = api.post(reverse("wallet-transfer"), {"beneficiary": str(b.id), "amount": "100"}, format="json")
    assert response.status_code == 400
    assert response.data["code"] == "idempotency_key_required"


def test_replaying_a_request_does_not_double_charge(api, alice, bob):
    b = add_beneficiary(alice, bob)
    first = pay(api, b, "5000", key="same")
    second = pay(api, b, "5000", key="same")
    assert first.status_code == 201
    assert second.status_code == 200
    assert second["Idempotent-Replayed"] == "true"
    assert second.data["transaction"]["id"] == first.data["transaction"]["id"]
    assert balance(alice) == Decimal("95000.00")
    assert Transaction.objects.filter(type="transfer").count() == 1


def test_same_key_with_different_amount_conflicts(api, alice, bob):
    b = add_beneficiary(alice, bob)
    pay(api, b, "5000", key="same")
    assert pay(api, b, "6000", key="same").status_code == 409


def test_insufficient_funds(api, alice, bob):
    b = add_beneficiary(alice, bob)
    response = pay(api, b, "200000")
    assert response.status_code == 400
    assert response.data["code"] == "insufficient_funds"
    assert balance(alice) == Decimal("100000.00")
    assert not Transaction.objects.filter(type="transfer").exists()


def test_cannot_pay_through_someone_elses_beneficiary(api, alice, bob):
    carol = make_user("carol")
    foreign = add_beneficiary(bob, carol)
    assert pay(api, foreign, "100").status_code == 400
    assert balance(carol) == 0


def test_minimum_amount_and_decimal_places(api, alice, bob):
    b = add_beneficiary(alice, bob)
    assert pay(api, b, "1").status_code == 400
    assert pay(api, b, "100.555", key="k2").status_code == 400
    assert pay(api, b, "-50", key="k3").status_code == 400


def test_frozen_sender_cannot_transfer(api, alice, bob):
    from apps.wallets.models import Wallet

    b = add_beneficiary(alice, bob)
    Wallet.objects.filter(user=alice).update(is_frozen=True)
    response = pay(api, b, "100")
    assert response.status_code == 403
    assert response.data["code"] == "wallet_frozen"


def test_transfer_requires_authentication(alice, bob):
    b = add_beneficiary(alice, bob)
    assert pay(APIClient(), b, "100").status_code in (401, 403)


# --- fraud outcomes through the API / services -------------------------------

def test_limit_breach_is_rejected_audited_and_explained(api, alice, bob):
    services.deposit(wallet=alice.wallet, amount=300000)  # 400k total
    b = add_beneficiary(alice, bob)
    response = pay(api, b, "260000")
    assert response.status_code == 403
    assert response.data["code"] == "limit_exceeded"
    assert balance(alice) == Decimal("400000.00")
    txn = Transaction.objects.get(reference=response.data["reference"])
    assert txn.status == "rejected"
    assert txn.fraud_flags[0]["code"] == "SINGLE_LIMIT"


def test_daily_limit_across_several_transfers(api, alice, bob):
    services.deposit(wallet=alice.wallet, amount=900000)  # 1M total
    b = add_beneficiary(alice, bob)
    assert pay(api, b, "200000", key="a").status_code == 201
    assert pay(api, b, "200000", key="b").status_code == 201
    third = pay(api, b, "200000", key="c")  # 600k in 24h > 500k
    assert third.status_code == 403
    assert third.data["code"] == "limit_exceeded"


def test_high_risk_score_is_blocked_without_leaking_the_rules(api, alice, bob):
    b = add_beneficiary(alice, bob, aged=False)  # brand-new beneficiary
    for i in range(5):
        assert pay(api, b, "10", key=f"small-{i}").status_code == 201
    response = pay(api, b, "25000", key="big")  # velocity + anomaly + new beneficiary
    assert response.status_code == 403
    assert response.data["code"] == "transfer_blocked"
    body = str(response.data).lower()
    assert "velocity" not in body and "score" not in body
    txn = Transaction.objects.get(reference=response.data["reference"])
    assert {f["code"] for f in txn.fraud_flags} >= {"VELOCITY", "AMOUNT_ANOMALY", "NEW_BENEFICIARY"}
    assert balance(alice) == Decimal("99950.00")


@pytest.fixture
def pending(alice, bob):
    """A transfer that scores 'review': large + new beneficiary + drains the wallet."""
    from apps.wallets.models import Wallet

    Wallet.objects.filter(user=alice).update(balance=Decimal("30000.00"))
    b = add_beneficiary(alice, bob, aged=False)
    result = services.transfer(user=alice, beneficiary=b, amount=Decimal("29000"), idempotency_key="rev-1")
    return result.txn


def test_suspicious_transfer_is_held_for_review(alice, bob, pending):
    assert pending.status == "pending_review"
    assert balance(alice) == Decimal("1000.00")  # funds held
    assert balance(bob) == 0  # not delivered yet


def test_receiver_does_not_see_pending_transfer(bob, pending):
    client = APIClient()
    client.force_authenticate(bob)
    assert client.get(reverse("wallet-transactions")).data["count"] == 0
    assert client.get(reverse("wallet-transaction-detail", args=[pending.id])).status_code == 404


def test_staff_approval_delivers_funds(alice, bob, pending):
    staff = make_user("staff")
    services.approve_pending_transfer(pending.id, reviewer=staff)
    pending.refresh_from_db()
    assert pending.status == "completed" and pending.reviewed_by == staff
    assert balance(bob) == Decimal("29000.00")


def test_staff_rejection_refunds_sender(alice, bob, pending):
    staff = make_user("staff")
    services.reject_pending_transfer(pending.id, reviewer=staff)
    pending.refresh_from_db()
    assert pending.status == "rejected"
    assert balance(alice) == Decimal("30000.00")
    assert balance(bob) == 0


def test_a_transfer_can_only_be_reviewed_once(alice, pending):
    staff = make_user("staff")
    services.approve_pending_transfer(pending.id, reviewer=staff)
    with pytest.raises(services.TransferError) as exc:
        services.reject_pending_transfer(pending.id, reviewer=staff)
    assert exc.value.code == "not_pending"


def test_sender_cannot_review_their_own_transfer(alice, pending):
    with pytest.raises(services.TransferError) as exc:
        services.approve_pending_transfer(pending.id, reviewer=alice)
    assert exc.value.code == "self_review"