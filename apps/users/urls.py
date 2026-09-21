from django.urls import path

from apps.users.views import (
    GoogleExchangeView,
    LoginView,
    LogoutView,
    RefreshView,
    RegisterView,
    google_login_finish,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("login/", LoginView.as_view(), name="auth-login"),
    path("google/finish/", google_login_finish, name="auth-google-finish"),
    path("google/exchange/", GoogleExchangeView.as_view(), name="auth-google-exchange"),
    path("token/refresh/", RefreshView.as_view(), name="auth-token-refresh"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
]