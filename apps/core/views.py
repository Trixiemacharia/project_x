import logging
import redis
from django.conf import settings
from django.db import connections
from django.db.utils import OperationalError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)
class HealthCheckView(APIView):
    """GET /api/v1/health/
Unauthenticated liveness/readiness probe for load balancers and
orchestration. Checks the two hard dependencies (DB, Redis) rather
than just returning 200 unconditionally, since a "healthy" instance
that can't reach either is not actually able to serve requests.

Response:
    200 {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}
    503 {"status": "unavailable", "checks": {...}}
"""

    permission_classes = [AllowAny]
    throttle_classes = []  

    def get(self, request):
        checks = {
            "database": self._check_database(),
            "redis": self._check_redis(),
        }
        healthy = all(v == "ok" for v in checks.values())
        status_code = 200 if healthy else 503
        return Response(
            {"status": "ok" if healthy else "unavailable", "checks": checks},
            status=status_code,
       )

    def _check_database(self) -> str:
        try:
            connections["default"].cursor()
        except OperationalError:
            logger.exception("Health check: database unreachable")
            return "unreachable"
        return "ok"

    def _check_redis(self) -> str:
        try:
            client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            client.ping()
        except redis.exceptions.RedisError:
            logger.exception("Health check: redis unreachable")
            return "unreachable"
        return "ok"
