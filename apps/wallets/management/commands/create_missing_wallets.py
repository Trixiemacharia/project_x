from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.wallets.services import ensure_wallet


class Command(BaseCommand):
    help = "Create wallets for users that registered before the wallets app existed."

    def handle(self, *args, **options):
        users = get_user_model().objects.filter(wallet__isnull=True)
        count = 0
        for user in users.iterator():
            ensure_wallet(user)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Created {count} wallet(s)."))