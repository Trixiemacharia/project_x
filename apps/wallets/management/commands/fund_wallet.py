from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.wallets import services


class Command(BaseCommand):
    help = "DEV ONLY: credit a user's wallet with test money (refuses to run when DEBUG=False)."

    def add_arguments(self, parser):
        parser.add_argument("identifier", help="username or email")
        parser.add_argument("amount")

    def handle(self, *args, identifier, amount, **options):
        if not settings.DEBUG:
            raise CommandError("fund_wallet only runs with DEBUG=True.")
        try:
            amount = Decimal(amount)
        except InvalidOperation:
            raise CommandError("Invalid amount.")
        if amount <= 0:
            raise CommandError("Amount must be positive.")
        User = get_user_model()
        user = User.objects.filter(Q(**{User.USERNAME_FIELD: identifier}) | Q(email=identifier)).first()
        if not user:
            raise CommandError("User not found.")
        txn = services.deposit(wallet=services.ensure_wallet(user), amount=amount, description="Dev top-up")
        self.stdout.write(self.style.SUCCESS(f"Credited {amount} to {user} ({txn.reference})."))