"""Checks that transfer() follows the sequence diagram: decide first, lock later, ledger on every move."""
from decimal import Decimal

import pytest

from apps.fraud.models import OutboxEvent
from apps.wallets import fraud, services
from apps.wallets.ledger_models import LedgerEntry
from apps.wallets.models import Transaction, Wallet

from .conftest import add_beneficiary, make_user

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def pair(db):
    sender, receiver = make_user("sender", funds=100000), make_user("receiver")
    return sender, receiver, add_beneficiary(sender, receiver)


def go(pair, amount, key):
    sender, _, bene = pair
    return services.transfer(user=sender, beneficiary=bene, amount=amount, idempotency_key=key)


def test_approved_transfer_posts_ledger_debit_and_credit(pair):
    sender, receiver, _ = pair
    txn = go(pair, "500", "a").txn
    entries = {e.direction: e for e in txn.ledger_entries.all()}
    assert txn.status == "completed"
    assert entries["debit"].amount == entries["credit"].amount == D("500")
    assert entries["debit"].balance_after == Wallet.objects.get(user=sender).balance
    assert entries["credit"].balance_after == Wallet.objects.get(user=receiver).balance


def test_approved_transfer_queues_balance_and_received_events(pair):
    sender, receiver, _ = pair
    go(pair, "500", "a")
    mine = OutboxEvent.objects.get(audience="customer", user=sender, event_type="transaction.completed")
    theirs = OutboxEvent.objects.get(audience="customer", user=receiver, event_type="transaction.received")
    assert "balance" in mine.payload and theirs.payload["amount"] == "500.00"


def test_blocked_transfer_moves_nothing_and_writes_no_ledger(db):
    sender, receiver = make_user("rich", funds=1000000), make_user("receiver")
    bene = add_beneficiary(sender, receiver)
    before_balance = Wallet.objects.get(user=sender).balance
    before_entries = LedgerEntry.objects.count()
    txn = services.transfer(user=sender, beneficiary=bene, amount="250000.01", idempotency_key="b").txn
    assert txn.status == "rejected"
    assert Wallet.objects.get(user=sender).balance == before_balance
    assert LedgerEntry.objects.count() == before_entries
    assert OutboxEvent.objects.filter(user=sender, event_type="transaction.blocked").exists()


def test_review_holds_funds_with_one_debit_and_reject_credits_back(db):
    sender, receiver, staff = make_user("s", funds=30000), make_user("r"), make_user("staff")
    bene = add_beneficiary(sender, receiver, aged=False)
    txn = services.transfer(user=sender, beneficiary=bene, amount="29000", idempotency_key="r").txn
    assert txn.status == "pending_review"
    assert [(e.direction, e.memo) for e in txn.ledger_entries.all()] == [("debit", "hold")]
    services.reject_pending_transfer(txn.pk, staff)
    assert Wallet.objects.get(user=sender).balance == D("30000")
    assert txn.ledger_entries.filter(direction="credit", memo="hold_release").count() == 1


def test_balance_drained_after_precheck_is_caught_under_the_lock(pair, monkeypatch):
    def drain_then_allow(ctx):
        Wallet.objects.filter(pk=ctx.sender.pk).update(balance=D("10"))
        return fraud.FraudDecision(outcome=fraud.ALLOW, score=0, flags=[])

    monkeypatch.setattr(fraud, "evaluate", drain_then_allow)
    with pytest.raises(services.TransferError) as exc:
        go(pair, "500", "race")
    assert exc.value.code == "insufficient_funds"
    assert not Transaction.objects.filter(idempotency_key="race").exists()


def test_daily_limit_is_rechecked_under_the_lock(pair, monkeypatch):
    def add_big_transfer_then_allow(ctx):
        Transaction.objects.create(type="transfer", sender_wallet=ctx.sender, receiver_wallet=ctx.receiver,
                                   amount=D("480000"), status="completed")
        return fraud.FraudDecision(outcome=fraud.ALLOW, score=0, flags=[])

    monkeypatch.setattr(fraud, "evaluate", add_big_transfer_then_allow)
    with pytest.raises(services.TransferError) as exc:
        go(pair, "100000", "limit")
    assert exc.value.code == "limit_exceeded"


def test_replayed_request_does_not_double_spend(pair):
    first, second = go(pair, "500", "same"), go(pair, "500", "same")
    assert second.replayed and first.txn.pk == second.txn.pk
    assert first.txn.ledger_entries.count() == 2