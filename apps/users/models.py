from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)
    # The stable subject claim supplied by Google.  Email addresses can change;
    # this is the identifier used to recognise an already-linked account.
    google_sub = models.CharField(max_length=255, unique=True, null=True, blank=True)
    mfa_enabled = models.BooleanField(default=False)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        db_table = "users"
