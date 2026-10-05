from unittest.mock import patch
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from apps.users.services import InvalidGoogleToken, UnverifiedGoogleEmailConflict

User = get_user_model()
VALID_PASSWORD = "a-genuinely-long-passphrase-99"

@pytest.fixture
def client():
    return APIClient()

@pytest.mark.django_db
class TestRegister:
    url = reverse("auth-register")
    def test_register_success_returns_access_and_sets_refresh_cookie(self, client):
        response = client.post(
            self.url,
            {"username": "alex", "email": "alex@example.com", "password": VALID_PASSWORD},
            format="json",
        )
        assert response.status_code == 201
        assert "access" in response.data
        assert response.cookies["refresh_token"].value
        assert response.cookies["refresh_token"]["httponly"]
        user = User.objects.get(username="alex")
        assert user.check_password(VALID_PASSWORD)

    def test_register_rejects_duplicate_email(self, client):
        User.objects.create_user(username="existing", email="dupe@example.com", password=VALID_PASSWORD)
        response = client.post(
           self.url,
           {"username": "newname", "email": "dupe@example.com", "password": VALID_PASSWORD},
           format="json",
        )
        assert response.status_code == 400
        assert "email" in response.data

    def test_register_rejects_weak_password(self, client):
        response = client.post(
           self.url,
           {"username": "weakpw", "email": "weak@example.com", "password": "short"},
           format="json",
        )
        assert response.status_code == 400
        assert "password" in response.data

@pytest.mark.django_db
class TestLogin:
    url = reverse("auth-login")
    def setup_method(self):
        self.user = User.objects.create_user(
            username="loginuser", email="login@example.com", password=VALID_PASSWORD
        )

    def test_login_with_username_succeeds(self, client):
        response = client.post(
            self.url, {"identifier": "loginuser", "password": VALID_PASSWORD}, format="json"
        )
        assert response.status_code == 200
        assert "access" in response.data
        assert response.cookies["refresh_token"].value

    def test_login_with_email_succeeds(self, client):
        response = client.post(
            self.url, {"identifier": "login@example.com", "password": VALID_PASSWORD}, format="json"
        )
        assert response.status_code == 200

    def test_login_wrong_password_returns_generic_error(self, client):
        response = client.post(
            self.url, {"identifier": "loginuser", "password": "wrong-password-entirely"}, format="json"
        )
        assert response.status_code == 400
        assert "access" not in response.data

    def test_login_unknown_user_returns_same_generic_error(self, client):
        response = client.post(
            self.url, {"identifier": "nobody-here", "password": VALID_PASSWORD}, format="json"
        )
        assert response.status_code == 400
    # Same error shape/message as a wrong password — no enumeration signal.
        wrong_password_response = client.post(
            self.url, {"identifier": "loginuser", "password": "wrong-password-entirely"}, format="json"
        )
        assert response.data == wrong_password_response.data

    def test_google_only_account_cannot_log_in_with_password(self, client):
        google_user = User.objects.create(username="googleonly", email="g@example.com")
        google_user.set_unusable_password()
        google_user.save()
        response = client.post(
            self.url, {"identifier": "googleonly", "password": "anything-at-all-123"}, format="json"
        )
        assert response.status_code == 400

GOOGLE_PAYLOAD_NEW = {
    "sub": "google-sub-123",
    "email": "newgoogle@example.com",
    "email_verified": True,
    "given_name": "New",
    "family_name": "User",
    "iss": "accounts.google.com",
}

@pytest.mark.django_db
class TestGoogleAuth:
    url = reverse("auth-google")

    @patch("apps.users.views.verify_google_id_token")
    def test_new_google_user_is_created(self, mock_verify, client):
        mock_verify.return_value = GOOGLE_PAYLOAD_NEW
        response = client.post(self.url, {"id_token": "fake-token"}, format="json")
        assert response.status_code == 201
        assert "access" in response.data
        user = User.objects.get(email="newgoogle@example.com")
        assert user.google_sub == "google-sub-123"
        assert not user.has_usable_password()

    @patch("apps.users.views.verify_google_id_token")
    def test_existing_linked_google_user_logs_in(self, mock_verify, client):
        existing = User.objects.create(
            username="alreadylinked", email="linked@example.com", google_sub="existing-sub"
        )
        existing.set_unusable_password()
        existing.save()
        mock_verify.return_value = {**GOOGLE_PAYLOAD_NEW, "sub": "existing-sub", "email": "linked@example.com"}

        response = client.post(self.url, {"id_token": "fake-token"}, format="json")

        assert response.status_code == 200
        assert response.data["user"]["id"] == existing.id
        assert User.objects.filter(email="linked@example.com").count() == 1

    @patch("apps.users.views.verify_google_id_token")
    def test_unverified_email_conflict_is_rejected(self, mock_verify, client):
        User.objects.create_user(username="haspassword", email="conflict@example.com", password=VALID_PASSWORD)
        mock_verify.return_value = {
            **GOOGLE_PAYLOAD_NEW,
            "sub": "some-other-sub",
            "email": "conflict@example.com",
            "email_verified": False,
        }

        response = client.post(self.url, {"id_token": "fake-token"}, format="json")

        assert response.status_code == 409
        # No account was modified or created off the back of the unverified claim.
        assert not User.objects.filter(email="conflict@example.com", google_sub="some-other-sub").exists()

    @patch("apps.users.views.verify_google_id_token")
    def test_invalid_token_returns_401(self, mock_verify, client):
        mock_verify.side_effect = InvalidGoogleToken("Invalid or expired Google token.")
        response = client.post(self.url, {"id_token": "garbage"}, format="json")
        assert response.status_code == 401

@pytest.mark.django_db
class TestRefreshAndLogout:
    def setup_method(self):
        self.user = User.objects.create_user(
            username="refreshuser", email="refresh@example.com", password=VALID_PASSWORD)

    def _login(self, client):
        return client.post(
            reverse("auth-login"),
            {"identifier": "refreshuser", "password": VALID_PASSWORD},
            format="json",
        )

    def test_refresh_without_cookie_returns_401(self, client):
        response = client.post(reverse("auth-token-refresh"))
        assert response.status_code == 401

    def test_refresh_rotates_token_and_old_one_cannot_be_reused(self, client):
        login_response = self._login(client)
        old_refresh = login_response.cookies["refresh_token"].value
        client.cookies["refresh_token"] = old_refresh

        first_refresh = client.post(reverse("auth-token-refresh"))
        assert first_refresh.status_code == 200
        assert "access" in first_refresh.data

        # Replaying the same (now-rotated, blacklisted) refresh token must fail.
        client.cookies["refresh_token"] = old_refresh
        replay_attempt = client.post(reverse("auth-token-refresh"))
        assert replay_attempt.status_code == 401

    def test_logout_requires_authentication(self, client):
        response = client.post(reverse("auth-logout"))
        assert response.status_code == 401

    def test_logout_blacklists_refresh_token(self, client):
        login_response = self._login(client)
        access = login_response.data["access"]
        refresh = login_response.cookies["refresh_token"].value
        client.cookies["refresh_token"] = refresh

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        logout_response = client.post(reverse("auth-logout"))
        assert logout_response.status_code == 200

        # The refresh token that was just blacklisted must no longer work.
        client.credentials()
        client.cookies["refresh_token"] = refresh
        refresh_attempt = client.post(reverse("auth-token-refresh"))
        assert refresh_attempt.status_code == 401
