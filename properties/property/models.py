from django.db import models
from django.core.exceptions import ValidationError
from rest_framework import permissions
from parler.models import TranslatableModel, TranslatedFields
import requests


class Property(TranslatableModel):
    PROPERTY_TYPES = [
        ("apartment", "Apartment"),
        ("house", "House"),
        ("villa", "Villa"),
        ("hotel", "Hotel"),
        ("hostel", "Hostel"),
    ]

    translations = TranslatedFields(
        name=models.CharField(max_length=255, verbose_name="property_name"),
        description=models.TextField(verbose_name="Description"),
        address=models.TextField(verbose_name="Address"),
        amenities=models.TextField(verbose_name="Amenities"),
        property_type=models.CharField(
            max_length=100,
            choices=PROPERTY_TYPES,
            verbose_name="Property_type",
        ),
    )

    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Price")
    owner = models.ForeignKey(
        "authentication.user", on_delete=models.CASCADE, related_name="property_owner"
    )
    created_at = models.DateTimeField(auto_now=True)
    available = models.BooleanField(default=True, verbose_name="aviability")
    rating = models.DecimalField(
        max_digits=3, decimal_places=2, default=0.0, verbose_name="rating"
    )
    webservice_username = models.CharField(max_length=255, null=True, blank=True, verbose_name="SES Username")
    webservice_password = models.CharField(max_length=255, null=True, blank=True, verbose_name="SES Password")
    establishment_code = models.CharField(max_length=50, null=True, blank=True, verbose_name="Establishment Code")
    landlord_code = models.CharField(max_length=50, null=True, blank=True, verbose_name="Landlord Code")
    ses_status = models.BooleanField(default=False, verbose_name="SES Connection Status")

    def __str__(self):
        return self.safe_translation_getter("name", default="Unnamed Property")

    def validate_ses_credentials(self):
        """
        Validates the SES.Hospedajes credentials via their API.
        """
        if not all(
            [
                self.webservice_username,
                self.webservice_password,
                self.establishment_code,
                self.landlord_code,
            ]
        ):
            raise ValidationError("All SES.Hospedajes credentials must be provided.")

        api_url = "https://ses.hospedajes.gov/api/validate"
        payload = {
            "username": self.webservice_username,
            "password": self.webservice_password,
            "establishment_code": self.establishment_code,
            "landlord_code": self.landlord_code,
        }
        try:
            response = requests.post(api_url, json=payload)
            response_data = response.json()
            if response.status_code == 200 and response_data.get("status") == "success":
                self.ses_status = True
            else:
                self.ses_status = False
                raise ValidationError("SES Credentials validation failed")
        except requests.RequestException:
            raise ValidationError("Error connecting to SES.Hospedajes API.")

        def save(self, *args, **kwargs):
            """Override save method to validate SES credentials when updated"""
            if self.webservice_username and self.wewebservice_password:
                self.validate_ses_credentials()
            super().save(*args, **kwargs)


class PropertyImage(models.Model):
    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to="property_images/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for {self.property.safe_translation_getter('name', default='Unnamed Property')}"


class IsLandlordOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.role in ["SuperAdmin", "Landlord", "Admin"]


class IsSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.role == "SuperAdmin"
