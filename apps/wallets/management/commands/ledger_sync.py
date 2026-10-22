from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q, Sum

from apps.wallets import ledger
from apps.wallets.ledger_models import LedgerEntry
from apps.wallets.models import Wallet


class Command(BaseCommand):
    help = "Create opening-balance ledger entries for wallets that predate the ledger, then report any wallet whose balance != credits - debits."

    def handle(self, *args, **options):
        opened = bad = 0
        for pk in Wallet.objects.values_list("pk", flat=True):
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(pk=pk)
                if not wallet.ledger_entries.exists():
                    if wallet.balance:
                        LedgerEntry.objects.create(wallet=wallet, transaction=None, direction=ledger.CREDIT,
                                                   amount=wallet.balance, balance_after=wallet.balance, memo="opening_balance")
                        opened += 1
                    continue
                agg = wallet.ledger_entries.aggregate(
                    c=Sum("amount", filter=Q(direction="credit")), d=Sum("amount", filter=Q(direction="debit")))
                net = (agg["c"] or Decimal("0")) - (agg["d"] or Decimal("0"))
                if net != wallet.balance:
                    bad += 1
                    self.stderr.write(f"MISMATCH wallet={wallet.account_number} balance={wallet.balance} ledger={net}")
        self.stdout.write(f"opening entries created: {opened}; mismatched wallets: {bad}")