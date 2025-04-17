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
    def create_user(self, email, password=None, phone_number=None, **extra_fields):
        if not email:
            raise ValueError("The Email field is required")
        if not phone_number:
            raise ValueError("The Phone Number field is required")
        email = self.normalize_email(email)
        username = extra_fields.pop("username", email.split("@")[0])
        first_name = extra_fields.pop("first_name", "")
        last_name = extra_fields.pop("last_name", "")
        role = extra_fields.pop("role", "")
        # language = extra_fields.pop("language", "en")
        user = self.model(
            email=email,
            username=username,
            phone_number=phone_number,
            first_name=first_name,
            last_name=last_name,
            role=role,
            **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_agent(self, landlord, email, password=None, first_name=None, last_name=None, phone_number=None, **extra_fields):
        user = self.model(
            email=self.normalize_email(email),
            phone_number=phone_number,
            role=User.AGENT,
            first_name=first_name,
            last_name=last_name,
            is_active=True,
            created_by=landlord,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_admin(self, superadmin, email, password=None, phone_number=None, **extra_fields):
        if superadmin.role != "SuperAdmin":
            raise PermissionError("Only SuperAdmins can create Admins")
        user = self.model(
            email=self.normalize_email(email),
            phone_number=phone_number,
            role="Admin",
            is_staff=True,
            **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, phone_number=None, **extra_fields):
        user = self.model(
            email=self.normalize_email(email),
            phone_number=phone_number,
            role="SuperAdmin",
            is_staff=True,
            is_superuser=True,
            **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user


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
        language=models.CharField(max_length=10, default="en"),
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

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="Guest")
    created_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="created_users")
    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["phone_number"]

    def __str__(self):
        return f"{self.email} ({self.role})"

    def is_superadmin(self):
        return self.role == self.SUPERADMIN

    def get_full_name(self):
        return f"{self.first_name or ''} {self.last_name or ''}".strip() or self.email

    def get_short_name(self):
        return self.first_name or self.email


class LandlordAgentRelationship(models.Model):
    landlord = models.ForeignKey(User, on_delete=models.CASCADE, related_name='agent_teams')
    agent = models.ForeignKey(User, on_delete=models.CASCADE, related_name='landlord_connections')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('landlord', 'agent')
        
    def __str__(self):
        return f"{self.landlord.email} - {self.agent.email}"