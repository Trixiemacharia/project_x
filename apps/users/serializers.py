from django.contrib.auth import authenticate, get_user_model, password_validation
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

   def validate_password(self, value):
       # Runs Django's configured validators (includes the 12-char
       # minimum set in settings/base.py) distinguishing them lets an attacker enumerate
       # valid usernames/emails.
       if user is None or not user.is_active:
           self.fail("invalid_credentials")

       attrs["user"] = user
       return attrs

