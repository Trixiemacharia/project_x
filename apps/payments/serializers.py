from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from .models import MpesaPayment
from .utils import mask_phone, normalize_phone


class StkPushRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=9, decimal_places=2)
    phone_number = serializers.CharField(max_length=20)

    def validate_amount(self, value):
        if value != value.to_integral_value():
            raise serializers.ValidationError("Amount must be a whole number of shillings.")
        minimum = Decimal(str(settings.MPESA_MIN_AMOUNT))
        maximum = Decimal(str(settings.MPESA_MAX_AMOUNT))
        if not (minimum <= value <= maximum):
            raise serializers.ValidationError(f"Amount must be between {minimum} and {maximum}.")
        return value.to_integral_value()

    def validate_phone_number(self, value):
        try:
            return normalize_phone(value)
        except ValueError:
            raise serializers.ValidationError("Enter a valid Safaricom number, e.g. 0712345678.")


class MpesaPaymentSerializer(serializers.ModelSerializer):
    phone_number = serializers.SerializerMethodField()

    class Meta:
        model = MpesaPayment
        fields = [
            "id", "status", "amount", "currency", "phone_number",
            "mpesa_receipt_number", "result_desc", "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_phone_number(self, obj) -> str:
        return mask_phone(obj.phone_number)