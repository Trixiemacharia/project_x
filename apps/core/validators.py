import re

from django.core.exceptions import ValidationError


class PasswordComplexityValidator:
    LOWER_RE = re.compile(r"[a-z]")
    DIGIT_RE = re.compile(r"[0-9]")
    SYMBOL_RE = re.compile(r"[^A-Za-z0-9]")

    def validate(self, password, user=None):
        missing = []
        if not self.LOWER_RE.search(password):
            missing.append("a lowercase letter")
        if not self.DIGIT_RE.search(password):
            missing.append("a number")
        if not self.SYMBOL_RE.search(password):
            missing.append("a symbol")
        if missing:
            raise ValidationError(
                "Password must contain " + ", ".join(missing) + ".",
                code="password_missing_character_classes",
            )

    def get_help_text(self):
        return (
            "Your password must contain an uppercase letter, a lowercase "
            "letter, a number, and a symbol."
        )


class PasswordNotSimilarToEmailValidator:
    def validate(self, password, user=None):
        email = getattr(user, "email", None) if user is not None else None
        if not email:
            return
        local_part = email.split("@")[0].lower()
        if password.lower() == email.lower() or (local_part and local_part in password.lower()):
            raise ValidationError(
                "Password must not be the same as, or contain, your email address.",
                code="password_same_as_email",
            )

    def get_help_text(self):
        return "Your password must not be the same as, or contain, your email address."
