"""Settings used only by the local pytest suite.

Production continues to use the MySQL configuration in ``fintech.settings``.
Keeping tests on SQLite makes the suite runnable without a local database
server and lets Django create and discard its test database automatically.
"""

from .settings import *  # noqa: F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}

# Unit tests use in-memory adapters, so local development does not require Redis.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "test-cache"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"auth": "1000/minute", "transfer": "1000/minute", "beneficiary": "1000/minute"},
}

CELERY_TASK_ALWAYS_EAGER = True