import time
from django.core.management.base import BaseCommand from django.db import connections from django.db.utils import OperationalError
class Command(BaseCommand): """Blocks until the database is available, with a bounded retry/backoff."""
help = "Waits for the database to be available before proceeding."

def handle(self, *args, **options):
    max_attempts = 30
    delay_seconds = 2

    for attempt in range(1, max_attempts + 1):
        try:
            connections["default"].cursor()
            self.stdout.write(self.style.SUCCESS("Database available."))
            return
        except OperationalError:
            self.stdout.write(
            
            time.sleep(delay_seconds)

    self.stderr.write(self.style.ERROR("Database never became available. Exiting."))
    raise SystemE    f"Database unavailable (attempt {attempt}/{max_attempts}), "
                f"retrying in {delay_seconds}s..."
            )xit(1)
