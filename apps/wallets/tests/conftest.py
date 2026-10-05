from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.wallets import services
from apps.wallets.models import Beneficiary, Wallet
from apps.wallets.throttles import BeneficiaryThrottle, TransferThrottle

User = get_user_model()
PASSWORD = "a-genuinely-long-passphrase-99"


@pytest.fixture(autouse=True)
def _fast_password_hashing(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # keeps create_user() quick


@pytest.fixture(autouse=True)
def _disable_throttling(monkeypatch):
    for cls in (TransferThrottle, BeneficiaryThrottle):
        monkeypatch.setattr(cls, "get_rate", lambda self: None)


def make_user(username, funds=0, aged=True):
    """Create a user (a wallet is auto-created). `aged` backdates the wallet so 'new wallet' rules stay quiet."""
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password=PASSWORD)
    wallet = Wallet.objects.get(user=user)
    if aged:
        Wallet.objects.filter(pk=wallet.pk).update(created_at=timezone.now() - timedelta(days=90))
    if funds:
        services.deposit(wallet=wallet, amount=Decimal(funds))
    return user


def add_beneficiary(owner, target, aged=True, nickname=None):
    b = Beneficiary.objects.create(owner=owner, wallet=Wallet.objects.get(user=target), nickname=nickname or target.username)
    if aged:
        Beneficiary.objects.filter(pk=b.pk).update(created_at=timezone.now() - timedelta(days=30))
    b.refresh_from_db()
    return b


def balance(user):
    return Wallet.objects.get(user=user).balance


@pytest.fixture
def alice(db):
    return make_user("alice", funds=100000)


@pytest.fixture
def bob(db):
    return make_user("bob")


@pytest.fixture
def api(alice):
    client = APIClient()
    client.force_authenticate(alice)
    return client