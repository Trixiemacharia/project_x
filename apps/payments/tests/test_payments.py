from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.payments import daraja, tasks
from apps.payments.models import MpesaCallback, MpesaPayment
from apps.wallets.models import Transaction, Wallet

User = get_user_model()
STK_PATH = "apps.payments.services.daraja.stk_push"
DARAJA_OK = {"MerchantRequestID": "m-1", "CheckoutRequestID": "ws_CO_1", "ResponseCode": "0"}
SECRET = "test-callback-secret"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.MPESA_CALLBACK_SECRET = SECRET
    settings.MPESA_CALLBACK_BASE_URL = "https://example.test"
    settings.MPESA_CALLBACK_ALLOWED_IPS = []
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(db):
    u = User.objects.create_user(username="payer", email="payer@example.com", password="Str0ng&Secure-Pass99")
    Wallet.objects.get_or_create(user=u)
    return u


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _stk(client, key="key-12345678", amount="500", phone="0712345678"):
    return client.post(
        reverse("mpesa-stk"), {"amount": amount, "phone_number": phone}, format="json", HTTP_IDEMPOTENCY_KEY=key
    )


def _callback_body(checkout_id="ws_CO_1", code=0, amount=500, receipt="QWE123RTY4"):
    body = {"MerchantRequestID": "m-1", "CheckoutRequestID": checkout_id, "ResultCode": code,
            "ResultDesc": "ok" if code == 0 else "Request cancelled by user"}
    if code == 0:
        body["CallbackMetadata"] = {"Item": [
            {"Name": "Amount", "Value": amount},
            {"Name": "MpesaReceiptNumber", "Value": receipt},
            {"Name": "PhoneNumber", "Value": 254712345678},
        ]}
    return {"Body": {"stkCallback": body}}


def _post_callback(secret=SECRET, **kw):
    return APIClient().post(reverse("mpesa-callback", args=[secret]), _callback_body(**kw), format="json")


def _processing_payment(client):
    with patch(STK_PATH, return_value=DARAJA_OK):
        resp = _stk(client)
    assert resp.status_code == 202
    return MpesaPayment.objects.get(pk=resp.data["id"])


@pytest.mark.django_db
class TestInitiate:
    def test_new_payment_goes_processing(self, client):
        payment = _processing_payment(client)
        assert payment.status == MpesaPayment.Status.PROCESSING
        assert payment.checkout_request_id == "ws_CO_1"

    def test_replay_with_same_key_does_not_push_twice(self, client):
        with patch(STK_PATH, return_value=DARAJA_OK) as mock_push:
            first = _stk(client)
            second = _stk(client)
        assert first.status_code == 202 and second.status_code == 200
        assert first.data["id"] == second.data["id"]
        assert mock_push.call_count == 1

    def test_same_key_different_payload_is_422(self, client):
        with patch(STK_PATH, return_value=DARAJA_OK):
            _stk(client, amount="500")
            resp = _stk(client, amount="900")
        assert resp.status_code == 422

    def test_missing_idempotency_key_is_400(self, client):
        resp = client.post(reverse("mpesa-stk"), {"amount": "500", "phone_number": "0712345678"}, format="json")
        assert resp.status_code == 400

    @pytest.mark.parametrize("amount,phone", [("500.50", "0712345678"), ("0", "0712345678"), ("500", "12345")])
    def test_invalid_input_is_400(self, client, amount, phone):
        assert _stk(client, amount=amount, phone=phone).status_code == 400

    def test_frozen_wallet_is_403(self, client, user):
        Wallet.objects.filter(user=user).update(is_frozen=True)
        assert _stk(client).status_code == 403

    def test_daraja_rejection_marks_failed(self, client):
        with patch(STK_PATH, side_effect=daraja.DarajaRejected("bad request")):
            resp = _stk(client)
        assert resp.data["status"] == "failed"

    def test_daraja_timeout_leaves_pending(self, client):
        with patch(STK_PATH, side_effect=daraja.DarajaUnavailable("timeout")):
            resp = _stk(client)
        assert resp.data["status"] == "pending"

    def test_cannot_read_someone_elses_payment(self, client):
        payment = _processing_payment(client)
        other = User.objects.create_user(username="other", email="other@example.com", password="Str0ng&Secure-Pass99")
        c = APIClient()
        c.force_authenticate(user=other)
        assert c.get(reverse("mpesa-payment-detail", args=[payment.id])).status_code == 404


@pytest.mark.django_db
class TestCallback:
    def test_success_credits_wallet_once(self, client, user):
        payment = _processing_payment(client)
        assert _post_callback().status_code == 200
        payment.refresh_from_db()
        wallet = Wallet.objects.get(user=user)
        assert payment.status == "successful"
        assert wallet.balance == 500
        txn = Transaction.objects.get()
        assert txn.type == Transaction.Type.DEPOSIT and txn.status == Transaction.Status.COMPLETED

    def test_duplicate_callback_does_not_double_credit(self, client, user):
        _processing_payment(client)
        _post_callback()
        resp = _post_callback()
        assert resp.status_code == 200
        assert Wallet.objects.get(user=user).balance == 500
        assert Transaction.objects.count() == 1
        assert MpesaCallback.objects.filter(outcome="duplicate").count() == 1

    def test_user_cancel_does_not_credit(self, client, user):
        payment = _processing_payment(client)
        _post_callback(code=1032)
        payment.refresh_from_db()
        assert payment.status == "cancelled"
        assert Wallet.objects.get(user=user).balance == 0

    def test_amount_mismatch_goes_to_review_without_credit(self, client, user):
        payment = _processing_payment(client)
        _post_callback(amount=50)
        payment.refresh_from_db()
        assert payment.status == "needs_review"
        assert Wallet.objects.get(user=user).balance == 0

    def test_wrong_secret_is_404_and_changes_nothing(self, client):
        payment = _processing_payment(client)
        assert _post_callback(secret="nope").status_code == 404
        payment.refresh_from_db()
        assert payment.status == "processing"

    def test_unknown_checkout_id_is_acked_and_logged(self, client):
        resp = _post_callback(checkout_id="ws_CO_unknown")
        assert resp.status_code == 200
        assert MpesaCallback.objects.get().outcome == "unmatched"


@pytest.mark.django_db
class TestReconcile:
    def _age(self, payment, seconds):
        MpesaPayment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(seconds=seconds))

    def test_query_success_credits_wallet(self, client, user):
        payment = _processing_payment(client)
        self._age(payment, 200)
        with patch("apps.payments.tasks.daraja.stk_query", return_value={"ResultCode": "0", "ResultDesc": "ok"}):
            tasks.reconcile_pending_payments()
        assert Wallet.objects.get(user=user).balance == 500

    def test_stale_unresolved_payment_times_out(self, client):
        payment = _processing_payment(client)
        self._age(payment, 1200)
        still = daraja.DarajaRejected("processing", error_code=daraja.STILL_PROCESSING_CODE)
        with patch("apps.payments.tasks.daraja.stk_query", side_effect=still):
            tasks.reconcile_pending_payments()
        payment.refresh_from_db()
        assert payment.status == "timed_out"

    def test_late_success_after_timeout_is_still_credited(self, client, user):
        payment = _processing_payment(client)
        self._age(payment, 1200)
        still = daraja.DarajaRejected("processing", error_code=daraja.STILL_PROCESSING_CODE)
        with patch("apps.payments.tasks.daraja.stk_query", side_effect=still):
            tasks.reconcile_pending_payments()
        _post_callback()
        payment.refresh_from_db()
        assert payment.status == "successful"
        assert Wallet.objects.get(user=user).balance == 500