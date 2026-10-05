from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ["username", "email", "password"]

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("That username is taken.")
        return value

    def validate(self, attrs):
        candidate_user = User(username=attrs.get("username", ""), email=attrs.get("email", ""))
        try:
            password_validation.validate_password(attrs["password"], user=candidate_user)
        except DjangoValidationError as exc:
            # A password policy failure belongs to the password field, rather
            # than being presented as a serializer-wide validation error.
            raise serializers.ValidationError({"password": exc.messages}) from exc
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            password=validated_data["password"],
        )


class LoginSerializer(serializers.Serializer):
    """
    Field-presence validation only — the actual credential check (and its
    401/lockout handling) lives in the view via
    apps.users.services.authenticate_identifier, so the view controls the
    response status code precisely.
    """

    identifier = serializers.CharField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class MfaVerifySerializer(serializers.Serializer):
    mfa_token = serializers.CharField()
    otp_code = serializers.CharField(min_length=1, max_length=12)
