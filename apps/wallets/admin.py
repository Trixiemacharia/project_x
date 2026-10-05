from django.contrib import admin, messages

from . import services
from .models import Beneficiary, Transaction, Wallet


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("account_number", "user", "currency", "balance", "is_frozen", "created_at")
    list_filter = ("is_frozen", "currency")
    search_fields = ("account_number", "user__username", "user__email")
    readonly_fields = ("user", "account_number", "currency", "balance", "created_at", "updated_at")  # balance moves only via services
    actions = ["freeze", "unfreeze"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Freeze selected wallets")
    def freeze(self, request, queryset):
        self.message_user(request, f"Froze {queryset.update(is_frozen=True)} wallet(s).")

    @admin.action(description="Unfreeze selected wallets")
    def unfreeze(self, request, queryset):
        self.message_user(request, f"Unfroze {queryset.update(is_frozen=False)} wallet(s).")


@admin.register(Beneficiary)
class BeneficiaryAdmin(admin.ModelAdmin):
    list_display = ("nickname", "owner", "wallet", "created_at")
    search_fields = ("nickname", "owner__username", "wallet__account_number")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("reference", "type", "sender_wallet", "receiver_wallet", "amount", "status", "fraud_score", "created_at")
    list_filter = ("status", "type")
    search_fields = ("reference", "sender_wallet__account_number", "receiver_wallet__account_number")
    ordering = ("-created_at",)
    actions = ["approve_selected", "reject_selected"]

    # The ledger is append-only from the admin's point of view.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_review_permission(self, request):
        return request.user.has_perm("wallets.review_transaction")

    def _run(self, request, queryset, fn, verb):
        ok = skipped = 0
        pending = queryset.filter(status=Transaction.Status.PENDING_REVIEW).values_list("pk", flat=True)
        for pk in list(pending):
            try:
                fn(pk, reviewer=request.user)
                ok += 1
            except services.TransferError as exc:
                skipped += 1
                self.message_user(request, f"{pk}: {exc.detail}", messages.WARNING)
        self.message_user(request, f"{verb} {ok} transfer(s); skipped {skipped}.")

    @admin.action(description="Approve selected pending transfers", permissions=["review"])
    def approve_selected(self, request, queryset):
        self._run(request, queryset, services.approve_pending_transfer, "Approved")

    @admin.action(description="Reject selected pending transfers (refund sender)", permissions=["review"])
    def reject_selected(self, request, queryset):
        self._run(request, queryset, services.reject_pending_transfer, "Rejected")