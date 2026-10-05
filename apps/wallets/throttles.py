from rest_framework.settings import api_settings
from rest_framework.throttling import UserRateThrottle


class _DefaultedRate(UserRateThrottle):
    """Uses DEFAULT_THROTTLE_RATES[scope] if configured, else a safe built-in default."""

    default_rate = "30/min"

    def get_rate(self):
        return api_settings.DEFAULT_THROTTLE_RATES.get(self.scope) or self.default_rate


class TransferThrottle(_DefaultedRate):
    scope = "transfer"
    default_rate = "10/min"


class BeneficiaryThrottle(_DefaultedRate):
    """Also slows account-number enumeration through the add-beneficiary lookup."""

    scope = "beneficiary"
    default_rate = "20/min"s