"""Imported at the bottom of wallets/models.py so Django discovers LedgerEntry."""
from django.db import models
from django.db.models import Q


class LedgerEntry(models.Model):
    """Append-only record of every balance change. wallet.balance must always equal credits - debits."""

    class Direction(models.TextChoices):
        DEBIT = "debit", "Debit"
        CREDIT = "credit", "Credit"

    id = models.BigAutoField(primary_key=True)
    wallet = models.ForeignKey("wallets.Wallet", on_delete=models.PROTECT, related_name="ledger_entries")
    # Null only for opening-balance entries created by `manage.py ledger_sync`.
    transaction = models.ForeignKey("wallets.Transaction", null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_entries")
    direction = models.CharField(max_length=6, choices=Direction.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    memo = models.CharField(max_length=30, blank=True)  # hold | hold_release | transfer | deposit | mpesa_deposit | opening_balance
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="ledger_amount_positive")]
        indexes = [models.Index(fields=["wallet", "-id"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError("LedgerEntry rows are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("LedgerEntry rows are append-only")