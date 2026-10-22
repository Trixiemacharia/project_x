"""All money movement lives here. Views and admin never touch balances directly.

transfer() follows the sequence diagram:
  8-9    wallet + balance pre-check           (no locks, no open DB transaction)
  10-13  risk evaluation                      (no locks, no open DB transaction)
  14-18  BLOCK   record rejected txn, queue fraud alert
  19-23  REVIEW  hold funds, create REVIEW txn + case, queue analyst alert
  24-39  ALLOW   BEGIN, lock wallets, re-validate, ledger debit/credit, record, COMMIT, then queue notifications
Notifications are queued from transaction.on_commit (see apps.fraud.events), so nothing is announced before COMMIT.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.fraud import recorder

from . import fraud, ledger
from .conf import wallet_conf as conf
from .models import Transaction, Wallet

Status = Transaction.Status


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
    # Always lock in primary-key order so two opposite transfers can't deadlock
    # (the diagram shows sender-then-recipient; pk order is the deadlock-safe version of that).
    qs = Wallet.objects.select_for_update().filter(pk__in=set(wallet_ids)).order_by("pk")
    return {w.pk: w for w in qs}


# --- deposits ------------------------------------------------------------------------------------------------

@transaction.atomic
def deposit(*, wallet: Wallet, amount, description: str = "Deposit") -> Transaction:
    """Credit a wallet. Call this from a verified payment-provider callback (e.g. M-Pesa)."""
    amount = Decimal(amount)
    wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
    if wallet.is_frozen:
        raise TransferError("wallet_frozen", "This wallet is frozen.", 403)
    txn = Transaction.objects.create(
        type=Transaction.Type.DEPOSIT, receiver_wallet=wallet, amount=amount, currency=wallet.currency,
        description=description, status=Status.COMPLETED,
    )
    ledger.post(wallet, txn, ledger.CREDIT, amount, memo="deposit")
    return txn


# --- transfers (diagram order) -------------------------------------------------------------------------------

def _find_replay(sender, receiver, amount, key):
    existing = Transaction.objects.filter(sender_wallet=sender, idempotency_key=key).first()
    if existing and (existing.receiver_wallet_id != receiver.pk or existing.amount != amount):
        raise TransferError("idempotency_key_reused", "This Idempotency-Key was already used for a different transfer.", 409)
    return existing


def _check_wallets(sender, receiver, amount):
    if sender.is_frozen:
        raise TransferError("wallet_frozen", "Your wallet is frozen. Please contact support.", 403)
    if receiver.is_frozen or receiver.currency != sender.currency:
        raise TransferError("recipient_unavailable", "This recipient cannot receive funds right now.")
    if sender.balance < amount:
        raise TransferError("insufficient_funds", "Insufficient balance.")


def _revalidate(sender, receiver, beneficiary, amount):
    """Run under the wallet lock: the risk decision was made on unlocked data, so re-check what can race."""
    _check_wallets(sender, receiver, amount)
    ctx = fraud.FraudContext(sender=sender, receiver=receiver, beneficiary=beneficiary, amount=amount, now=timezone.now())
    if any(check(ctx) for check in fraud.LIMIT_CHECKS):
        raise TransferError("limit_exceeded", "This transfer exceeds your transaction limits.", 403)


def _new_txn(*, status, sender, receiver, amount, description, key, decision):
    return Transaction.objects.create(
        type=Transaction.Type.TRANSFER, sender_wallet=sender, receiver_wallet=receiver, amount=amount,
        currency=sender.currency, description=description, status=status, idempotency_key=key,
        fraud_score=decision.score, fraud_flags=decision.flags_as_json(),
    )


def transfer(*, user, beneficiary, amount, description: str = "", idempotency_key: str) -> TransferResult:
    amount = Decimal(amount)
    sender = ensure_wallet(user)
    receiver = beneficiary.wallet
    if sender.pk == receiver.pk:
        raise TransferError("self_transfer", "You cannot transfer to your own wallet.")

    replay = _find_replay(sender, receiver, amount, idempotency_key)
    if replay:
        return TransferResult(replay, replayed=True)

    _check_wallets(sender, receiver, amount)  # steps 8-9

    decision = fraud.evaluate(  # steps 10-13
        fraud.FraudContext(sender=sender, receiver=receiver, beneficiary=beneficiary, amount=amount, now=timezone.now())
    )
    args = dict(sender=sender, receiver=receiver, beneficiary=beneficiary, amount=amount,
                description=description, key=idempotency_key, decision=decision)
    try:
        if decision.outcome == fraud.BLOCK:
            txn = _record_blocked(**args)
        elif decision.outcome == fraud.REVIEW:
            txn = _hold_for_review(**args)
        else:
            txn = _execute(**args)
    except IntegrityError:
        # Lost a race to an identical concurrent request (unique idempotency constraint): replay the winner.
        replay = _find_replay(sender, receiver, amount, idempotency_key)
        if replay is None:
            raise
        return TransferResult(replay, replayed=True)
    return TransferResult(txn)


def _record_blocked(*, sender, receiver, beneficiary, amount, description, key, decision):
    """Steps 14-18. Nothing moves; the attempt is stored and the alert is queued after commit."""
    with transaction.atomic():
        txn = _new_txn(status=Status.REJECTED, sender=sender, receiver=receiver, amount=amount,
                       description=description, key=key, decision=decision)
        recorder.record_transfer_decision(txn=txn, decision=decision)
        return txn


def _hold_for_review(*, sender, receiver, beneficiary, amount, description, key, decision):
    """Steps 19-23. Funds are held (ledger debit) until an analyst decides."""
    with transaction.atomic():
        sender = _lock_wallets(sender.pk)[sender.pk]
        _revalidate(sender, receiver, beneficiary, amount)
        txn = _new_txn(status=Status.PENDING_REVIEW, sender=sender, receiver=receiver, amount=amount,
                       description=description, key=key, decision=decision)
        ledger.post(sender, txn, ledger.DEBIT, amount, memo="hold")
        recorder.record_transfer_decision(txn=txn, decision=decision)
        return txn


def _execute(*, sender, receiver, beneficiary, amount, description, key, decision):
    """Steps 24-39. BEGIN / lock / re-validate / ledger debit + credit / record / audit / COMMIT."""
    with transaction.atomic():
        locked = _lock_wallets(sender.pk, receiver.pk)
        sender, receiver = locked[sender.pk], locked[receiver.pk]
        _revalidate(sender, receiver, beneficiary, amount)
        txn = _new_txn(status=Status.COMPLETED, sender=sender, receiver=receiver, amount=amount,
                       description=description, key=key, decision=decision)
        ledger.post(sender, txn, ledger.DEBIT, amount, memo="transfer")    # step 27 (+ balance update, 29)
        ledger.post(receiver, txn, ledger.CREDIT, amount, memo="transfer")  # step 28 (+ balance update, 29)
        recorder.record_transfer_decision(txn=txn, decision=decision)       # assessment, audit log (31), events queued for after COMMIT
        return txn


# --- manual review -------------------------------------------------------------------------------------------

def _lock_pending(txn_id, reviewer):
    txn = Transaction.objects.select_for_update().get(pk=txn_id)
    if txn.status != Status.PENDING_REVIEW:
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
    ledger.post(receiver, txn, ledger.CREDIT, txn.amount, memo="transfer")
    return _finish_review(txn, reviewer, Status.COMPLETED)


@transaction.atomic
def reject_pending_transfer(txn_id, reviewer) -> Transaction:
    txn, sender, _receiver = _lock_pending(txn_id, reviewer)
    ledger.post(sender, txn, ledger.CREDIT, txn.amount, memo="hold_release")
    return _finish_review(txn, reviewer, Status.REJECTED)