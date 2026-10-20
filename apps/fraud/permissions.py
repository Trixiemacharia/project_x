from rest_framework.permissions import BasePermission


class IsFraudAnalyst(BasePermission):
    """Reuses the existing wallets.review_transaction permission; roles are Django groups (see setup_fraud_roles)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.has_perm("wallets.review_transaction"))