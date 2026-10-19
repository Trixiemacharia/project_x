import re


def normalize_phone(raw: str) -> str:
    """Accepts 07XXXXXXXX, 01XXXXXXXX, +2547XXXXXXXX, 2547XXXXXXXX; returns 254XXXXXXXXX."""
    digits = re.sub(r"[\s\-+]", "", raw)
    if re.fullmatch(r"0[17]\d{8}", digits):
        return "254" + digits[1:]
    if re.fullmatch(r"254[17]\d{8}", digits):
        return digits
    raise ValueError("invalid phone number")


def mask_phone(phone: str) -> str:
    return f"{phone[:6]}***{phone[-3:]}" if len(phone) >= 9 else "***"