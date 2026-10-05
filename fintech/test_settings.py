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
