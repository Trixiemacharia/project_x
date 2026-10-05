"""Tunable wallet / fraud settings.

Override any key from Django settings:

    WALLET_SETTINGS = {"SINGLE_TRANSFER_LIMIT": Decimal("100000"), "WEIGHTS": {"VELOCITY": 50}}
"""
from decimal import Decimal

from django.conf import settings

DEFAULTS = {
    "CURRENCY": "KES",
    "LOCAL_TIMEZONE": "Africa/Nairobi",
    # Transfer limits hard-block when exceeded.
    "MIN_TRANSFER": Decimal("10.00"),
    "SINGLE_TRANSFER_LIMIT": Decimal("250000.00"),
    "DAILY_TRANSFER_LIMIT": Decimal("500000.00"),  # rolling 24 hours
    # Amount at which "risky context" rules start to matter.
    "LARGE_AMOUNT": Decimal("20000.00"),
    # Risk score outcomes: < REVIEW allow, REVIEW..BLOCK-1 hold for review, >= BLOCK reject.
    "REVIEW_THRESHOLD": 40,
    "BLOCK_THRESHOLD": 80,
    "VELOCITY_WINDOW_MINUTES": 10,
    "VELOCITY_MAX_TRANSFERS": 5,
    "ANOMALY_LOOKBACK_DAYS": 30,
    "ANOMALY_MIN_HISTORY": 3,
    "ANOMALY_MULTIPLIER": Decimal("5"),
    "NEW_BENEFICIARY_HOURS": 24,
    "NEW_WALLET_HOURS": 24,
    "DRAIN_RATIO": Decimal("0.9"),
    "STRUCTURING_BAND": Decimal("0.8"),  # share of SINGLE_TRANSFER_LIMIT
    "STRUCTURING_MIN_PRIOR": 2,
    "ODD_HOURS": (0, 5),  # local time, start inclusive / end exclusive
    "REJECTION_WINDOW_MINUTES": 60,
    "REJECTION_MAX": 2,
    "FAN_IN_SENDERS": 5,
    "FAN_IN_WINDOW_HOURS": 24,
    "FAN_IN_MAX_WALLET_AGE_DAYS": 30,
    "WEIGHTS": {
        "SINGLE_LIMIT": 100,
        "DAILY_LIMIT": 100,
        "VELOCITY": 40,
        "AMOUNT_ANOMALY": 30,
        "NEW_BENEFICIARY": 35,
        "DRAIN_WALLET": 20,
        "NEAR_LIMIT_PATTERN": 25,
        "ODD_HOUR": 10,
        "RECENT_REJECTIONS": 25,
        "NEW_WALLET": 20,
        "MULE_FAN_IN": 25,
    },
}


class _Conf:
    def __getattr__(self, name):
        if name not in DEFAULTS:
            raise AttributeError(name)
        overrides = getattr(settings, "WALLET_SETTINGS", {})
        if name == "WEIGHTS":
            return {**DEFAULTS["WEIGHTS"], **overrides.get("WEIGHTS", {})}
        return overrides.get(name, DEFAULTS[name])


wallet_conf = _Conf()