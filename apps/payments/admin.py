from django.contrib import admin

from .models import MpesaCallback, MpesaPayment


@admin.register(MpesaPayment)
class MpesaPaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "amount", "status", "mpesa_receipt_number", "created_at")
    list_filter = ("status",)
    search_fields = ("id", "user__username", "user__email", "mpesa_receipt_number", "checkout_request_id")
    readonly_fields = [f.name for f in MpesaPayment._meta.fields]


@admin.register(MpesaCallback)
class MpesaCallbackAdmin(admin.ModelAdmin):
    list_display = ("id", "checkout_request_id", "outcome", "source_ip", "received_at")
    list_filter = ("outcome",)
    readonly_fields = [f.name for f in MpesaCallback._meta.fields]