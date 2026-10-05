import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.wallets import services
from apps.wallets.models import Transaction

from .conftest import add_beneficiary, make_user
from .test_transfers import pay

pytestmark = pytest.mark.django_db


def results(response):
    return response.data["results"]


@pytest.fixture
def history(alice, bob, api):
    """alice: +100000 deposit, then sends 1000 and 2000 to bob."""
    b = add_beneficiary(alice, bob)
    pay(api, b, "1000", key="h1")
    pay(api, b, "2000", key="h2")
    return b


def test_history_lists_deposits_and_transfers_newest_first(api, history):
    rows = results(api.get(reverse("wallet-transactions")))
    assert [r["amount"] for r in rows] == ["2000.00", "1000.00", "100000.00"]
    assert rows[0]["direction"] == "debit"
    assert rows[2]["direction"] == "credit" and rows[2]["counterparty"] is None


def test_counterparty_is_masked(api, history):
    row = results(api.get(reverse("wallet-transactions")))[0]
    assert row["counterparty"]["name"] == "b**"
    assert len(row["counterparty"]["account_number"]) == 10


def test_receiver_sees_credits(bob, history):
    client = APIClient()
    client.force_authenticate(bob)
    rows = results(client.get(reverse("wallet-transactions")))
    assert [r["direction"] for r in rows] == ["credit", "credit"]
    assert rows[0]["counterparty"]["name"] == "a****"


def test_filters(api, history):
    url = reverse("wallet-transactions")
    assert len(results(api.get(url, {"direction": "sent"}))) == 2
    assert len(results(api.get(url, {"type": "deposit"}))) == 1
    assert len(results(api.get(url, {"status": "rejected"}))) == 0
    assert api.get(url, {"direction": "sideways"}).status_code == 400
    assert api.get(url, {"from": "not-a-date"}).status_code == 400
    assert api.get(url, {"from": "2026-13-45"}).status_code == 400
    assert len(results(api.get(url, {"from": "2000-01-01", "to": "2999-01-01"}))) == 3
    assert len(results(api.get(url, {"to": "2000-01-01"}))) == 0


def test_pagination(api, history):
    response = api.get(reverse("wallet-transactions"), {"page_size": 2})
    assert response.data["count"] == 3
    assert len(response.data["results"]) == 2
    assert response.data["next"] is not None


def test_cannot_read_another_users_transaction(api, history):
    carol, dave = make_user("carol", funds=500), make_user("dave")
    services.deposit(wallet=dave.wallet, amount=10)
    foreign = Transaction.objects.filter(receiver_wallet=dave.wallet).first()
    assert api.get(reverse("wallet-transaction-detail", args=[foreign.id])).status_code == 404


def test_transaction_detail(api, history):
    txn = Transaction.objects.filter(amount=1000).first()
    response = api.get(reverse("wallet-transaction-detail", args=[txn.id]))
    assert response.status_code == 200
    assert response.data["reference"] == txn.reference