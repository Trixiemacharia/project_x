import base64
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

BASES = {"sandbox": "https://sandbox.safaricom.co.ke", "production": "https://api.safaricom.co.ke"}
TOKEN_CACHE_KEY = "daraja:access_token"
STILL_PROCESSING_CODE = "500.001.1001"  # STK Query answer while the customer hasn't acted yet
TIMEOUT = (5, 15)  # connect, read


class DarajaError(Exception):
    def __init__(self, message, *, error_code=None):
        super().__init__(message)
        self.error_code = error_code


class DarajaRejected(DarajaError):
    """Daraja answered and said no (or a status we can interpret)."""


class DarajaUnavailable(DarajaError):
    """Timeout / network / gateway trouble: we do NOT know what happened."""


def _base() -> str:
    return BASES[settings.MPESA_ENV]


def _timestamp() -> str:
    return datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d%H%M%S")


def _password(timestamp: str) -> str:
    raw = f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}"
    return base64.b64encode(raw.encode()).decode()


def get_access_token() -> str:
    token = cache.get(TOKEN_CACHE_KEY)
    if token:
        return token
    try:
        resp = requests.get(
            f"{_base()}/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
            timeout=TIMEOUT,
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise DarajaUnavailable(str(exc)) from exc
    if not resp.ok:
        raise DarajaRejected(f"OAuth failed: HTTP {resp.status_code}")
    try:
        data = resp.json()
        token = data["access_token"]
    except (ValueError, KeyError) as exc:
        raise DarajaRejected("OAuth response was not understood") from exc
    cache.set(TOKEN_CACHE_KEY, token, timeout=max(60, int(data.get("expires_in", 3599)) - 60))
    return token


def _post(path: str, payload: dict) -> dict:
    token = get_access_token()
    try:
        resp = requests.post(
            f"{_base()}{path}", json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise DarajaUnavailable(str(exc)) from exc

    try:
        data = resp.json()
    except ValueError:
        data = {}

    if resp.status_code == 401:
        cache.delete(TOKEN_CACHE_KEY)
    if resp.status_code >= 500 and not data.get("errorCode"):
        raise DarajaUnavailable(f"HTTP {resp.status_code}")
    if not resp.ok:
        raise DarajaRejected(data.get("errorMessage") or f"HTTP {resp.status_code}", error_code=data.get("errorCode"))
    return data


def stk_push(*, phone: str, amount, account_reference: str, description: str, callback_url: str) -> dict:
    ts = _timestamp()
    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": _password(ts),
        "Timestamp": ts,
        "TransactionType": "CustomerPayBillOnline",  # a Till number needs CustomerBuyGoodsOnline
        "Amount": int(amount),
        "PartyA": phone,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": callback_url,
        "AccountReference": account_reference[:12],
        "TransactionDesc": description[:13],
    }
    data = _post("/mpesa/stkpush/v1/processrequest", payload)
    if str(data.get("ResponseCode")) != "0":
        raise DarajaRejected(data.get("ResponseDescription", "STK push rejected"), error_code=str(data.get("ResponseCode")))
    return data


def stk_query(checkout_request_id: str) -> dict:
    ts = _timestamp()
    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": _password(ts),
        "Timestamp": ts,
        "CheckoutRequestID": checkout_request_id,
    }
    return _post("/mpesa/stkpushquery/v1/query", payload)