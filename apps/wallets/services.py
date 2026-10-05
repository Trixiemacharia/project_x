"""All money movement lives here. Views and admin never touch balances directly."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import fraud
from .conf import wallet_conf as conf
from .models import Transaction, Wallet


class TransferError(Exception):
    def __init__(self, code: str, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


@dataclass
class TransferResult:
    txn: Transaction
    replayed: bool = False


def ensure_wallet(user) -> Wallet:
    wallet = Wallet.objects.filter(user=user).first()
    if wallet:
        return wallet
    for _attempt in range(5):  # retry covers the (very unlikely) account-number collision
        try:
            with transaction.atomic():
                wallet, _ = Wallet.objects.get_or_create(user=user, defaults={"currency": conf.CURRENCY})
            return wallet
        except IntegrityError:
            continue
    raise RuntimeError("Could not allocate a wallet account number.")


def is_limit_breach(txn: Transaction) -> bool:
    return any(f.get("code") in fraud.LIMIT_CODES for f in txn.fraud_flags)


def _lock_wallets(*wallet_ids) -> dict:
    # Always lock in primary-key order so two opposite transfers can't deadlock.
    qs = Wallet.objects.select_for_update().filter(pk__in=set(wallet_ids)).order_by("pk")
    return {w.pk: w for w in qs}


def _save_balance(wallet: Wallet) -> None:
    wallet.save(update_fields=["balance", "updated_at"])


@transaction.atomic
def deposit(*, wallet: Wallet, amount, description: str = "Deposit") -> Transaction:
    """Credit a wallet. Call this from a verified payment-provider callback (e.g. M-Pesa)."""
    amount = Decimal(amount)
    wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
    if wallet.is_frozen:
        raise TransferError("wallet_frozen", "This wallet is frozen.", 403)
    wallet.balance += amount
    _save_balance(wallet)
    return Transaction.objects.create(
        type=Transaction.Type.DEPOSIT, receiver_wallet=wallet, amount=amount, currency=wallet.currency,
        description=description, status=Transaction.Status.COMPLETED,
    )


def transfer(*, user, beneficiary, amount, description: str = "", idempotency_key: str) -> TransferResult:
    amount = Decimal(amount)
    sender_id = ensure_wallet(user).pk
    receiver_id = beneficiary.wallet_id
    if sender_id == receiver_id:
        raise TransferError("self_transfer", "You cannot transfer to your own wallet.")

    with transaction.atomic():
        locked = _lock_wallets(sender_id, receiver_id)
        sender, receiver = locked[sender_id], locked[receiver_id]

        existing = Transaction.objects.filter(sender_wallet=sender, idempotency_key=idempotency_key).first()
        if existing:
            if existing.receiver_wallet_id != receiver_id or existing.amount != amount:
                raise TransferError(
                    "idempotency_key_reused", "This Idempotency-Key was already used for a different transfer.", 409
                )
            return TransferResult(existing, replayed=True)

        if sender.is_frozen:
            raise TransferError("wallet_frozen", "Your wallet is frozen. Please contact support.", 403)
        if receiver.is_frozen or receiver.currency != sender.currency:
            raise TransferError("recipient_unavailable", "This recipient cannot receive funds right now.")
        if sender.balance < amount:
            raise TransferError("insufficient_funds", "Insufficient balance.")

        decision = fraud.evaluate(
            fraud.FraudContext(sender=sender, receiver=receiver, beneficiary=beneficiary, amount=amount, now=timezone.now())
        )
        txn = Transaction(
            type=Transaction.Type.TRANSFER, sender_wallet=sender, receiver_wallet=receiver, amount=amount,
            currency=sender.currency, description=description, idempotency_key=idempotency_key,
            fraud_score=decision.score, fraud_flags=decision.flags_as_json(),
        )
        if decision.outcome == fraud.BLOCK:
            txn.status = Transaction.Status.REJECTED  # nothing moves, but the attempt is audited
        elif decision.outcome == fraud.REVIEW:
            sender.balance -= amount  # funds are held until staff decide
            _save_balance(sender)
            txn.status = Transaction.Status.PENDING_REVIEW
        else:
            sender.balance -= amount
            receiver.balance += amount
            _save_balance(sender)
            _save_balance(receiver)
            txn.status = Transaction.Status.COMPLETED
        txn.save()
        return TransferResult(txn)


def _lock_pending(txn_id, reviewer):
    txn = Transaction.objects.select_for_update().get(pk=txn_id)
    if txn.status != Transaction.Status.PENDING_REVIEW:
        raise TransferError("not_pending", "This transfer is not awaiting review.", 409)
    locked = _lock_wallets(txn.sender_wallet_id, txn.receiver_wallet_id)
    sender, receiver = locked[txn.sender_wallet_id], locked[txn.receiver_wallet_id]
    if sender.user_id == reviewer.pk:
        raise TransferError("self_review", "You cannot review your own transfer.", 403)
    return txn, sender, receiver


def _finish_review(txn, reviewer, status):
    txn.status = status
    txn.reviewed_by = reviewer
    txn.reviewed_at = timezone.now()
    txn.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    return txn


@transaction.atomic
def approve_pending_transfer(txn_id, reviewer) -> Transaction:
    txn, _sender, receiver = _lock_pending(txn_id, reviewer)
    if receiver.is_frozen:
        raise TransferError("recipient_unavailable", "Recipient wallet is frozen; reject instead.")
    receiver.balance += txn.amount
    _save_balance(receiver)
    return _finish_review(txn, reviewer, Transaction.Status.COMPLETED)


@transaction.atomic
def reject_pending_transfer(txn_id, reviewer) -> Transaction:
    txn, sender, _receiver = _lock_pending(txn_id, reviewer)
    sender.balance += txn.amount  # release the hold
    _save_balance(sender)
    return _finish_review(txn, reviewer, Transaction.Status.REJECTED)