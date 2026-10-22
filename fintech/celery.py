import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fintech.settings")

app = Celery("fintech")
app.config_from_object("django.conf:settings", namespace="CELERY")  # reads CELERY_* settings
app.autodiscover_tasks()