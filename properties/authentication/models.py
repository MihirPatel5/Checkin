from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, role="Guest", **extra_fields):
        if not email:
            raise ValueError("The Email field is required")
        email = self.normalize_email(email)
        user = self.model(email=email, role=role, **extra_fields)
        user.set_password(password)
        user.save(using=self.db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """Creates and returns a SuperAdmin user."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, role=User.SUPERADMIN, **extra_fields)


class User(AbstractUser):
    """Role based User model Defined using django abstractuser model."""

    email = models.EmailField(unique=True)

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
    REQUIRED_FIELDS = ["first_name", "last_name"]

    def __str__(self):
        return self.email

    def is_superadmin(self):
        return self.role == self.SUPERADMIN