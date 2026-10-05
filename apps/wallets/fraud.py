"""Rule-based fraud scoring for outgoing transfers.

Each rule inspects a FraudContext and returns a Flag (with a weight) or None. Scores are summed:

    hard limit breached or score >= BLOCK_THRESHOLD  -> BLOCK   (transfer rejected, audit row saved)
    score >= REVIEW_THRESHOLD                        -> REVIEW  (funds held, staff approve/reject)
    otherwise                                        -> ALLOW
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Avg, Count, Sum

from .conf import wallet_conf as conf
from .models import Beneficiary, Transaction, Wallet

ALLOW, REVIEW, BLOCK = "allow", "review", "block"
LIMIT_CODES = frozenset({"SINGLE_LIMIT", "DAILY_LIMIT"})
COUNTED = (Transaction.Status.COMPLETED, Transaction.Status.PENDING_REVIEW)


@dataclass
class FraudContext:
    sender: Wallet
    receiver: Wallet
    beneficiary: Beneficiary
    amount: Decimal
    now: datetime


@dataclass(frozen=True)
class Flag:
    code: str
    score: int
    detail: str


@dataclass
class FraudDecision:
    outcome: str
    score: int
    flags: list

    def flags_as_json(self) -> list:
        return [asdict(f) for f in self.flags]


def _flag(code: str, detail: str) -> Flag:
    return Flag(code, conf.WEIGHTS[code], detail)


def _outgoing(ctx):
    return Transaction.objects.filter(sender_wallet=ctx.sender, type=Transaction.Type.TRANSFER)


def _counted(ctx):
    return _outgoing(ctx).filter(status__in=COUNTED)


# --- hard limits -------------------------------------------------------------

def check_single_limit(ctx):
    if ctx.amount > conf.SINGLE_TRANSFER_LIMIT:
        return _flag("SINGLE_LIMIT", f"{ctx.amount} exceeds single-transfer limit {conf.SINGLE_TRANSFER_LIMIT}")


def check_daily_limit(ctx):
    since = ctx.now - timedelta(hours=24)
    sent = _counted(ctx).filter(created_at__gte=since).aggregate(t=Sum("amount"))["t"] or Decimal("0")
    if sent + ctx.amount > conf.DAILY_TRANSFER_LIMIT:
        return _flag("DAILY_LIMIT", f"{sent} already sent in 24h; +{ctx.amount} exceeds {conf.DAILY_TRANSFER_LIMIT}")


# --- scored rules ------------------------------------------------------------

def rule_velocity(ctx):
    since = ctx.now - timedelta(minutes=conf.VELOCITY_WINDOW_MINUTES)
    n = _outgoing(ctx).filter(created_at__gte=since).count()  # includes rejected attempts
    if n >= conf.VELOCITY_MAX_TRANSFERS:
        return _flag("VELOCITY", f"{n} transfer attempts in {conf.VELOCITY_WINDOW_MINUTES} min")


def rule_amount_anomaly(ctx):
    since = ctx.now - timedelta(days=conf.ANOMALY_LOOKBACK_DAYS)
    stats = _counted(ctx).filter(created_at__gte=since).aggregate(avg=Avg("amount"), n=Count("id"))
    if stats["n"] >= conf.ANOMALY_MIN_HISTORY and stats["avg"] is not None:
        avg = Decimal(str(stats["avg"]))
        if ctx.amount > avg * conf.ANOMALY_MULTIPLIER:
            return _flag("AMOUNT_ANOMALY", f"{ctx.amount} is >{conf.ANOMALY_MULTIPLIER}x the 30-day average {avg:.2f}")


def rule_new_beneficiary(ctx):
    age = ctx.now - ctx.beneficiary.created_at
    if age < timedelta(hours=conf.NEW_BENEFICIARY_HOURS) and ctx.amount >= conf.LARGE_AMOUNT:
        return _flag("NEW_BENEFICIARY", "large transfer to a recently added beneficiary")


def rule_drain_wallet(ctx):
    if ctx.amount >= conf.LARGE_AMOUNT and ctx.amount >= ctx.sender.balance * conf.DRAIN_RATIO:
        return _flag("DRAIN_WALLET", "transfer empties most of the wallet balance")


def rule_near_limit_pattern(ctx):
    floor = conf.SINGLE_TRANSFER_LIMIT * conf.STRUCTURING_BAND
    if ctx.amount < floor:
        return None
    since = ctx.now - timedelta(hours=24)
    prior = _counted(ctx).filter(created_at__gte=since, amount__gte=floor).count()
    if prior >= conf.STRUCTURING_MIN_PRIOR:
        return _flag("NEAR_LIMIT_PATTERN", f"{prior} earlier transfers close to the limit in 24h")


def rule_odd_hour(ctx):
    local = ctx.now.astimezone(ZoneInfo(conf.LOCAL_TIMEZONE))
    start, end = conf.ODD_HOURS
    if start <= local.hour < end and ctx.amount >= conf.LARGE_AMOUNT:
        return _flag("ODD_HOUR", f"large transfer at {local:%H:%M} local time")


def rule_recent_rejections(ctx):
    since = ctx.now - timedelta(minutes=conf.REJECTION_WINDOW_MINUTES)
    n = _outgoing(ctx).filter(status=Transaction.Status.REJECTED, created_at__gte=since).count()
    if n >= conf.REJECTION_MAX:
        return _flag("RECENT_REJECTIONS", f"{n} rejected attempts in the last {conf.REJECTION_WINDOW_MINUTES} min")


def rule_new_wallet(ctx):
    if ctx.now - ctx.sender.created_at < timedelta(hours=conf.NEW_WALLET_HOURS) and ctx.amount >= conf.LARGE_AMOUNT:
        return _flag("NEW_WALLET", "large transfer from a brand-new wallet")


def rule_mule_fan_in(ctx):
    """Young recipient wallet suddenly collecting money from many different senders."""
    if ctx.now - ctx.receiver.created_at > timedelta(days=conf.FAN_IN_MAX_WALLET_AGE_DAYS):
        return None
    since = ctx.now - timedelta(hours=conf.FAN_IN_WINDOW_HOURS)
    senders = (
        Transaction.objects.filter(
            receiver_wallet=ctx.receiver, type=Transaction.Type.TRANSFER, status__in=COUNTED, created_at__gte=since
        ).aggregate(n=Count("sender_wallet", distinct=True))["n"]
    )
    if senders >= conf.FAN_IN_SENDERS:
        return _flag("MULE_FAN_IN", f"recipient received from {senders} different senders in {conf.FAN_IN_WINDOW_HOURS}h")


LIMIT_CHECKS = (check_single_limit, check_daily_limit)
RULES = (
    rule_velocity, rule_amount_anomaly, rule_new_beneficiary, rule_drain_wallet, rule_near_limit_pattern,
    rule_odd_hour, rule_recent_rejections, rule_new_wallet, rule_mule_fan_in,
)


def evaluate(ctx: FraudContext) -> FraudDecision:
    limit_flags = [f for f in (check(ctx) for check in LIMIT_CHECKS) if f]
    flags = limit_flags + [f for f in (rule(ctx) for rule in RULES) if f]
    score = min(100, sum(f.score for f in flags))
    if limit_flags or score >= conf.BLOCK_THRESHOLD:
        outcome = BLOCK
    elif score >= conf.REVIEW_THRESHOLD:
        outcome = REVIEW
    else:
        outcome = ALLOW
    return FraudDecision(outcome=outcome, score=score, flags=flags)