from rest_framework.throttling import ScopedRateThrottle
class AuthRateThrottle(ScopedRateThrottle):
    """Applied to register/login/google endpoints at a tighter rate (see REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["auth"]) than the general API default, to slow down credential stuffing and account-enumeration attempts. Throttled by IP for these endpoints since the caller isn't authenticated yet."""
    scope = "auth"
