from unittest.mock import patch
import pytest
import redis
from django.urls import reverse
from rest_framework.test import APIClient

@pytest.mark.django_db
class TestHealthCheck:
    """GET /api/v1/health/ must:
    - be reachable without authentication (load balancers don't auth)
    - report 200 only when both DB and Redis are actually reachable
    - report 503 (not crash with 500) when a dependency is down"""
    def setup_method(self):
        self.client = APIClient()
        self.url = reverse("health-check")

    def test_health_ok_when_dependencies_up(self):
        with patch("apps.core.views.redis.Redis.ping", return_value=True):
        response = self.client.get(self.url)

        assert response.status_code == 200
        assert response.data["status"] == "ok"
        assert response.data["checks"]["database"] == "ok"
        assert response.data["checks"]["redis"] == "ok"

    def test_health_returns_503_when_redis_down(self):
        with patch(
            "apps.core.views.redis.Redis.ping",
            side_effect=redis.exceptions.ConnectionError,
        ):
        response = self.client.get(self.url)

        assert response.status_code == 503
        assert response.data["status"] == "unavailable"
        assert response.data["checks"]["redis"] == "unreachable"

    def test_health_check_requires_no_authentication(self):
    # No credentials set on the client at all.
        with patch("apps.core.views.redis.Redis.ping", return_value=True):
        response = self.client.get(self.url)
        assert response.status_code != 401
        assert response.status_code != 403