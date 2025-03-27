from typing import override
from django.db import models
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.core.validators import RegexValidator
from parler.models import TranslatableModel, TranslatedFields


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, phone_number=None, role="Guest", **extra_fields):
        if not email:
            raise ValueError("The Email field is required")
        if not phone_number:
            raise ValueError("The Phone Number field is required")
        email = self.normalize_email(email)
        username = extra_fields.pop("username", email.split("@")[0])
        first_name = extra_fields.pop("first_name", "")
        last_name = extra_fields.pop("last_name", "")
        language = extra_fields.pop("language", "en")
        user = self.model(email=email, phone_number=phone_number, role=role, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)

        user.create_translation(
            language_code=language, first_name=first_name, last_name=last_name
        )
        return user

    def create_superuser(self, email, phone_number=None, password=None, **extra_fields):
        """Creates and returns a SuperAdmin user."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, password, phone_number, role="SuperAdmin", **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, TranslatableModel):
    """Role based User model using AbstractBaseUser for complete customization."""

    email = models.EmailField(unique=True)
    username = models.CharField(max_length=150, null=True, blank=True)
    phone_number = models.CharField(
        max_length=15,
        unique=True,
        validators=[RegexValidator(r"^\+?1?\d{9,15}$", "Enter a valid phone number.")],
    )
    is_active = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    translations = TranslatedFields(
        first_name=models.CharField(max_length=150),
        last_name=models.CharField(max_length=150),
    )

    SUPERADMIN = "SuperAdmin"
    ADMIN = "Admin"
    LANDLORD = "Landlord"
    AGENT = "Agent"
    GUEST = "Guest"

    ROLE_CHOICES = [
        (SUPERADMIN, "Superadmin"),
        (ADMIN, "Admin"),
        (LANDLORD, "Landlord"),
        (AGENT, "Agent"),
        (GUEST, "Guest"),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=GUEST)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["phone_number"]

    def __str__(self):
        return self.email

    def is_superadmin(self):
        return self.role == self.SUPERADMIN

    def get_full_name(self):
        return f"{self.first_name or ''} {self.last_name or ''}".strip() or self.email

    def get_short_name(self):
        return self.first_name or self.email