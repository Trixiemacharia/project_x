from rest_framework.throttling import UserRateThrottle


class StkPushThrottle(UserRateThrottle):
    scope = "mpesa_stk"
    rate = "5/min"