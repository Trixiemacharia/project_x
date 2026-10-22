"""The only code allowed to change wallet.balance."""
from decimal import Decimal

from .ledger_models import LedgerEntry

DEBIT, CREDIT = LedgerEntry.Direction.DEBIT, LedgerEntry.Direction.CREDIT


def post(wallet, txn, direction, amount, memo: str = "") -> LedgerEntry:
    """Apply one leg. The caller MUST hold a select_for_update lock on `wallet` inside transaction.atomic()."""
    amount = Decimal(amount)
    wallet.balance = wallet.balance - amount if direction == DEBIT else wallet.balance + amount
    wallet.save(update_fields=["balance", "updated_at"])  # DB check constraint also blocks negative balances
    return LedgerEntry.objects.create(
        wallet=wallet, transaction=txn, direction=direction, amount=amount, balance_after=wallet.balance, memo=memo
    )