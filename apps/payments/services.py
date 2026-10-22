import hashlib
import logging
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.wallets import ledger
from apps.wallets.models import Transaction, Wallet

from . import daraja, events
from .models import MpesaCallback, MpesaPayment

logger = logging.getLogger(__name__)
Status = MpesaPayment.Status


class WalletUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # "no_wallet" | "frozen"


class IdempotencyConflict(Exception):
    """Same Idempotency-Key reused with a different amount/phone."""


def _fingerprint(amount: Decimal, phone: str) -> str:
    return hashlib.sha256(f"{int(amount)}:{phone}".encode()).hexdigest()


def _status_for(result_code: int) -> str:
    if result_code == 0:
        return Status.SUCCESSFUL
    if result_code == 1032:
        return Status.CANCELLED
    if result_code == 1037:
        return Status.TIMED_OUT
    return Status.FAILED  # 1 insufficient funds, 2001 wrong PIN, anything else


def _transition_allowed(current: str, new: str) -> bool:
    if current in MpesaPayment.FINAL_STATUSES:
        return False
    if current == Status.TIMED_OUT:
        return new == Status.SUCCESSFUL  # only a genuine late success may revive it
    return True


def initiate_stk_push(*, user, amount: Decimal, phone: str, idempotency_key: str):
    """Returns (payment, created). Never holds a DB transaction open during the Daraja call."""
    try:
        wallet = Wallet.objects.get(user=user)
    except Wallet.DoesNotExist:
        raise WalletUnavailable("no_wallet")
    if wallet.is_frozen:
        raise WalletUnavailable("frozen")

    fingerprint = _fingerprint(amount, phone)
    try:
        with transaction.atomic():
            payment = MpesaPayment.objects.create(
                user=user,
                wallet=wallet,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                phone_number=phone,
                amount=amount,
            )
    except IntegrityError:
        # Unique (user, key) fired: this is a replay (or a double-click race).
        existing = MpesaPayment.objects.get(user=user, idempotency_key=idempotency_key)
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyConflict()
        return existing, False

    callback_url = (
        f"{settings.MPESA_CALLBACK_BASE_URL.rstrip('/')}"
        f"/api/v1/payments/mpesa/callback/{settings.MPESA_CALLBACK_SECRET}/"
    )
    try:
        data = daraja.stk_push(
            phone=phone,
            amount=amount,
            account_reference=wallet.account_number,
            description="Deposit",
            callback_url=callback_url,
        )
    except daraja.DarajaRejected as exc:
        logger.warning("STK push rejected payment_id=%s: %s", payment.id, exc)
        apply_result(payment.pk, result_code=-1, result_desc=str(exc))
    except daraja.DarajaUnavailable as exc:
        # Outcome unknown: stay PENDING, the reconciler decides.
        logger.warning("STK push outcome unknown payment_id=%s: %s", payment.id, exc)
    else:
        MpesaPayment.objects.filter(pk=payment.pk, status=Status.PENDING).update(
            status=Status.PROCESSING,
            merchant_request_id=data.get("MerchantRequestID", ""),
            checkout_request_id=data["CheckoutRequestID"],
            updated_at=timezone.now(),
        )

    payment.refresh_from_db()
    events.publish_payment_event(payment)
    return payment, True


def apply_result(payment_id, *, result_code: int, result_desc: str, receipt=None, amount=None):
    """
    Single place where a payment outcome is applied (callback, STK Query, expiry,
    initiation failure). Returns (payment, outcome). Lock order: payment, then wallet.
    """
    new_status = _status_for(result_code)
    with transaction.atomic():
        payment = MpesaPayment.objects.select_for_update().get(pk=payment_id)
        if not _transition_allowed(payment.status, new_status):
            return payment, "duplicate"

        payment.result_code = result_code
        payment.result_desc = (result_desc or "")[:255]
        outcome = "applied"

        if new_status == Status.SUCCESSFUL:
            payment.mpesa_receipt_number = receipt or None
            if amount is not None and Decimal(str(amount)) != payment.amount:
                payment.status = Status.NEEDS_REVIEW
                outcome = "amount_mismatch"
                logger.error("Amount mismatch payment_id=%s expected=%s got=%s", payment.id, payment.amount, amount)
            else:
                wallet = Wallet.objects.select_for_update().get(pk=payment.wallet_id)
                if wallet.is_frozen:
                    payment.status = Status.NEEDS_REVIEW
                    outcome = "wallet_frozen"
                    logger.error("Wallet frozen at credit time payment_id=%s", payment.id)
                else:
                    txn = Transaction.objects.create(
                        type=Transaction.Type.DEPOSIT,
                        receiver_wallet=wallet,
                        amount=payment.amount,
                        currency=payment.currency,
                        description=f"M-Pesa deposit {receipt or ''}".strip()[:140],
                        status=Transaction.Status.COMPLETED,
                    )
                    ledger.post(wallet, txn, ledger.CREDIT, payment.amount, memo="mpesa_deposit")
                    payment.transaction = txn
                    payment.status = Status.SUCCESSFUL
        else:
            payment.status = new_status

        payment.save()
        transaction.on_commit(lambda: _after_commit(payment.pk))
    return payment, outcome


def _after_commit(payment_id) -> None:
    try:
        payment = MpesaPayment.objects.get(pk=payment_id)
        events.publish_payment_event(payment)
        from .tasks import send_payment_notification  # local import: tasks imports services

        send_payment_notification.delay(str(payment.pk))
    except Exception:  # noqa: BLE001 - post-commit side effects must not raise
        logger.exception("Post-commit hook failed payment_id=%s", payment_id)


def handle_callback(payload, source_ip) -> str:
    """Stores the raw callback, then applies it. Raises ValueError if malformed."""
    try:
        body = payload["Body"]["stkCallback"]
        checkout_id = body["CheckoutRequestID"]
        result_code = int(body["ResultCode"])
    except (KeyError, TypeError, ValueError):
        MpesaCallback.objects.create(payload=payload if isinstance(payload, (dict, list)) else {}, source_ip=source_ip, outcome="malformed")
        raise ValueError("malformed callback")

    items = (body.get("CallbackMetadata") or {}).get("Item", [])
    metadata = {item.get("Name"): item.get("Value") for item in items if isinstance(item, dict)}

    callback = MpesaCallback.objects.create(checkout_request_id=checkout_id, payload=payload, source_ip=source_ip)
    payment = MpesaPayment.objects.filter(checkout_request_id=checkout_id).first()
    if payment is None:
        callback.outcome = "unmatched"
        callback.save(update_fields=["outcome"])
        logger.error("Callback for unknown CheckoutRequestID=%s", checkout_id)
        return "unmatched"

    _, outcome = apply_result(
        payment.pk,
        result_code=result_code,
        result_desc=body.get("ResultDesc", ""),
        receipt=metadata.get("MpesaReceiptNumber"),
        amount=metadata.get("Amount"),
    )
    callback.payment = payment
    callback.outcome = outcome
    callback.save(update_fields=["payment", "outcome"])
    return outcome