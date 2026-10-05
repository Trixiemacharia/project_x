from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.wallets import fraud
from apps.wallets.models import Beneficiary, Transaction, Wallet

from .conftest import add_beneficiary, make_user

pytestmark = pytest.mark.django_db
D = Decimal


def ctx(sender, receiver, amount, beneficiary=None, now=None):
    return fraud.FraudContext(
        sender=Wallet.objects.get(user=sender),
        receiver=Wallet.objects.get(user=receiver),
        beneficiary=beneficiary or Beneficiary.objects.filter(owner=sender, wallet__user=receiver).first() or add_beneficiary(sender, receiver),
        amount=D(amount),
        now=now or timezone.now(),
    )


def codes(decision):
    return {f.code for f in decision.flags}


def history(sender, receiver, n, amount, status="completed", minutes_ago=1):
    for _ in range(n):
        txn = Transaction.objects.create(
            type="transfer", sender_wallet=Wallet.objects.get(user=sender), receiver_wallet=Wallet.objects.get(user=receiver),
            amount=D(amount), status=status,
        )
        Transaction.objects.filter(pk=txn.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))


@pytest.fixture
def pair(db):
    return make_user("sender", funds=1000000), make_user("receiver")


def test_ordinary_transfer_is_allowed(pair):
    decision = fraud.evaluate(ctx(*pair, "1000"))
    assert decision.outcome == fraud.ALLOW and decision.score == 0


def test_single_limit_is_a_hard_block(pair):
    decision = fraud.evaluate(ctx(*pair, "250000.01"))
    assert decision.outcome == fraud.BLOCK and "SINGLE_LIMIT" in codes(decision)


def test_daily_limit_counts_pending_but_not_rejected(pair):
    history(*pair, 1, "300000", status="pending_review")
    history(*pair, 1, "300000", status="rejected")
    assert "DAILY_LIMIT" in codes(fraud.evaluate(ctx(*pair, "250000")))
    assert "DAILY_LIMIT" not in codes(fraud.evaluate(ctx(*pair, "150000")))


def test_velocity(pair):
    history(*pair, 4, "10")
    assert "VELOCITY" not in codes(fraud.evaluate(ctx(*pair, "10")))
    history(*pair, 1, "10")
    assert "VELOCITY" in codes(fraud.evaluate(ctx(*pair, "10")))


def test_velocity_ignores_old_transfers(pair):
    history(*pair, 6, "10", minutes_ago=30)
    assert "VELOCITY" not in codes(fraud.evaluate(ctx(*pair, "10")))


def test_amount_anomaly_needs_history_and_a_big_jump(pair):
    assert "AMOUNT_ANOMALY" not in codes(fraud.evaluate(ctx(*pair, "10000")))  # no history
    history(*pair, 3, "1000", minutes_ago=60 * 24)
    assert "AMOUNT_ANOMALY" in codes(fraud.evaluate(ctx(*pair, "10000")))
    assert "AMOUNT_ANOMALY" not in codes(fraud.evaluate(ctx(*pair, "4000")))


def test_new_beneficiary_only_matters_for_large_amounts(pair):
    sender, receiver = pair
    fresh = add_beneficiary(sender, receiver, aged=False)
    assert "NEW_BENEFICIARY" in codes(fraud.evaluate(ctx(sender, receiver, "25000", beneficiary=fresh)))
    assert "NEW_BENEFICIARY" not in codes(fraud.evaluate(ctx(sender, receiver, "5000", beneficiary=fresh)))


def test_drain_wallet(pair):
    sender, receiver = pair
    Wallet.objects.filter(user=sender).update(balance=D("30000"))
    assert "DRAIN_WALLET" in codes(fraud.evaluate(ctx(sender, receiver, "29000")))
    assert "DRAIN_WALLET" not in codes(fraud.evaluate(ctx(sender, receiver, "10000")))


def test_near_limit_pattern(pair):
    history(*pair, 2, "210000", minutes_ago=120)
    assert "NEAR_LIMIT_PATTERN" in codes(fraud.evaluate(ctx(*pair, "210000")))


def test_odd_hour(pair):
    tz = ZoneInfo("Africa/Nairobi")
    night = datetime(2026, 10, 5, 2, 30, tzinfo=tz)
    day = datetime(2026, 10, 5, 14, 0, tzinfo=tz)
    assert "ODD_HOUR" in codes(fraud.evaluate(ctx(*pair, "25000", now=night)))
    assert "ODD_HOUR" not in codes(fraud.evaluate(ctx(*pair, "25000", now=day)))
    assert "ODD_HOUR" not in codes(fraud.evaluate(ctx(*pair, "500", now=night)))


def test_recent_rejections(pair):
    history(*pair, 2, "100", status="rejected")
    assert "RECENT_REJECTIONS" in codes(fraud.evaluate(ctx(*pair, "100")))


def test_new_wallet_sending_large_amount(db):
    sender, receiver = make_user("fresh", funds=100000, aged=False), make_user("old")
    assert "NEW_WALLET" in codes(fraud.evaluate(ctx(sender, receiver, "25000")))


def test_mule_fan_in(db):
    mule = make_user("mule", aged=False)
    for i in range(5):
        history(make_user(f"victim{i}", funds=1000), mule, 1, "100")
    sender = make_user("another", funds=1000)
    assert "MULE_FAN_IN" in codes(fraud.evaluate(ctx(sender, mule, "100")))


def test_scores_combine_into_review_and_block(pair):
    sender, receiver = pair
    Wallet.objects.filter(user=sender).update(balance=D("30000"))
    fresh = add_beneficiary(sender, receiver, aged=False)
    review = fraud.evaluate(ctx(sender, receiver, "29000", beneficiary=fresh))  # new beneficiary 35 + drain 20
    assert review.outcome == fraud.REVIEW and review.score == 55

    history(sender, receiver, 5, "10")  # + velocity 40 + anomaly 30
    block = fraud.evaluate(ctx(sender, receiver, "29000", beneficiary=fresh))
    assert block.outcome == fraud.BLOCK and block.score >= 80