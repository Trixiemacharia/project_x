import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APIClient

from apps.wallets.models import Beneficiary, Wallet

from .conftest import add_beneficiary, balance, make_user

pytestmark = pytest.mark.django_db


def test_wallet_is_created_automatically_with_zero_balance(bob):
    wallet = Wallet.objects.get(user=bob)
    assert wallet.balance == 0
    assert len(wallet.account_number) == 10


def test_view_balance(api):
    response = api.get(reverse("wallet-me"))
    assert response.status_code == 200
    assert response.data["balance"] == "100000.00"
    assert response.data["currency"] == "KES"


def test_wallet_requires_authentication():
    response = APIClient().get(reverse("wallet-me"))
    assert response.status_code in (401, 403)


def test_database_refuses_negative_balance(bob):
    with pytest.raises(IntegrityError), transaction.atomic():
        Wallet.objects.filter(user=bob).update(balance=-1)


class TestBeneficiaries:
    def test_add_beneficiary_by_account_number_masks_the_name(self, api, bob):
        response = api.post(
            reverse("wallet-beneficiaries"),
            {"nickname": "Bobby", "account_number": Wallet.objects.get(user=bob).account_number},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["account_name"] == "b**"
        assert response.data["nickname"] == "Bobby"

    def test_cannot_add_own_account(self, api, alice):
        response = api.post(
            reverse("wallet-beneficiaries"),
            {"nickname": "me", "account_number": Wallet.objects.get(user=alice).account_number},
            format="json",
        )
        assert response.status_code == 400

    def test_unknown_account_is_rejected(self, api):
        response = api.post(reverse("wallet-beneficiaries"), {"nickname": "x", "account_number": "0000000000"}, format="json")
        assert response.status_code == 400

    def test_frozen_account_looks_like_unknown_account(self, api, bob):
        Wallet.objects.filter(user=bob).update(is_frozen=True)
        response = api.post(
            reverse("wallet-beneficiaries"),
            {"nickname": "x", "account_number": Wallet.objects.get(user=bob).account_number},
            format="json",
        )
        assert response.status_code == 400

    def test_duplicate_beneficiary_is_rejected(self, api, alice, bob):
        add_beneficiary(alice, bob)
        response = api.post(
            reverse("wallet-beneficiaries"),
            {"nickname": "again", "account_number": Wallet.objects.get(user=bob).account_number},
            format="json",
        )
        assert response.status_code == 400

    def test_list_only_shows_my_beneficiaries(self, api, alice, bob):
        carol = make_user("carol")
        add_beneficiary(alice, bob)
        add_beneficiary(bob, carol)
        response = api.get(reverse("wallet-beneficiaries"))
        results = response.data["results"] if isinstance(response.data, dict) else response.data
        assert [b["nickname"] for b in results] == ["bob"]

    def test_rename_but_cannot_repoint_account(self, api, alice, bob):
        carol = make_user("carol")
        b = add_beneficiary(alice, bob)
        response = api.patch(
            reverse("wallet-beneficiary-detail", args=[b.id]),
            {"nickname": "Renamed", "account_number": Wallet.objects.get(user=carol).account_number},
            format="json",
        )
        assert response.status_code == 200
        b.refresh_from_db()
        assert b.nickname == "Renamed"
        assert b.wallet.user == bob

    def test_delete_beneficiary(self, api, alice, bob):
        b = add_beneficiary(alice, bob)
        assert api.delete(reverse("wallet-beneficiary-detail", args=[b.id])).status_code == 204
        assert not Beneficiary.objects.filter(pk=b.pk).exists()

    def test_cannot_touch_someone_elses_beneficiary(self, api, bob):
        carol = make_user("carol")
        b = add_beneficiary(bob, carol)
        url = reverse("wallet-beneficiary-detail", args=[b.id])
        assert api.get(url).status_code == 404
        assert api.patch(url, {"nickname": "hax"}, format="json").status_code == 404
        assert api.delete(url).status_code == 404