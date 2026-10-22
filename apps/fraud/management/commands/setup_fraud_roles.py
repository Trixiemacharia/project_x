from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

ROLES = {
    "FRAUD_ANALYST": [("wallets", "review_transaction")],
    "FRAUD_ADMIN": [("wallets", "review_transaction"), ("fraud", "assign_case")],
}


class Command(BaseCommand):
    help = "Create fraud role groups and attach their permissions (idempotent)."

    def handle(self, *args, **options):
        for name, perms in ROLES.items():
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(
                [Permission.objects.get(content_type__app_label=app, codename=code) for app, code in perms]
            )
            self.stdout.write(f"{name}: {len(perms)} permission(s)")