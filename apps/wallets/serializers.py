from django.db import IntegrityError
from rest_framework import serializers

from .conf import wallet_conf as conf
from .models import Beneficiary, Transaction, Wallet
from .utils import mask_name


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["account_number", "balance", "currency", "is_frozen"]


class BeneficiarySerializer(serializers.ModelSerializer):
    account_number = serializers.CharField(max_length=20)
    account_name = serializers.CharField(read_only=True)

    class Meta:
        model = Beneficiary
        fields = ["id", "nickname", "account_number", "account_name", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_account_number(self, value):
        user = self.context["request"].user
        try:
            wallet = Wallet.objects.select_related("user").get(account_number=value.strip(), is_frozen=False)
        except Wallet.DoesNotExist:
            raise serializers.ValidationError("No account found with this account number.")
        if wallet.user_id == user.id:
            raise serializers.ValidationError("You cannot add your own account as a beneficiary.")
        if Beneficiary.objects.filter(owner=user, wallet=wallet).exists():
            raise serializers.ValidationError("This beneficiary is already saved.")
        return wallet

    def create(self, validated_data):
        wallet = validated_data.pop("account_number")
        try:
            return Beneficiary.objects.create(owner=self.context["request"].user, wallet=wallet, **validated_data)
        except IntegrityError:
            raise serializers.ValidationError({"account_number": "This beneficiary is already saved."})


class BeneficiaryUpdateSerializer(serializers.ModelSerializer):
    """Only the nickname is editable; to change the account, delete and re-add."""

    class Meta:
        model = Beneficiary
        fields = ["nickname"]

    def to_representation(self, instance):
        return BeneficiarySerializer(instance, context=self.context).data


class TransferSerializer(serializers.Serializer):
    beneficiary = serializers.PrimaryKeyRelatedField(queryset=Beneficiary.objects.none())
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    description = serializers.CharField(max_length=140, required=False, allow_blank=True, default="")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None:  # a user can only pay their OWN beneficiaries
            self.fields["beneficiary"].queryset = Beneficiary.objects.filter(owner=request.user).select_related("wallet__user")

    def validate_amount(self, value):
        if value < conf.MIN_TRANSFER:
            raise serializers.ValidationError(f"Minimum transfer is {conf.MIN_TRANSFER}.")
        return value

    def validate_description(self, value):
        return value.strip()


class TransactionSerializer(serializers.ModelSerializer):
    """Customer-facing view. Fraud score/flags are deliberately NOT exposed."""

    direction = serializers.SerializerMethodField()
    counterparty = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = ["id", "reference", "type", "direction", "counterparty", "amount", "currency", "status", "description", "created_at"]

    def get_direction(self, obj):
        return "credit" if obj.receiver_wallet_id == self.context["wallet"].pk else "debit"

    def get_counterparty(self, obj):
        other = obj.sender_wallet if self.get_direction(obj) == "credit" else obj.receiver_wallet
        if other is None:
            return None
        return {"account_number": other.account_number, "name": mask_name(other.user)}