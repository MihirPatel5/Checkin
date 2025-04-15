import requests
from django.db import models
from django.core.exceptions import ValidationError
from rest_framework import permissions
from parler.models import TranslatableModel, TranslatedFields
from utils.ses_validation import generate_ses_xml, send_validation_request


class Property(TranslatableModel):
    PROPERTY_TYPES = [
                ("apartment", "Apartamento"),
                ("house", "Casa"),
                ("villa", "Villa"),
                ("hotel", "Hotel"),
                ("hostel", "Hostal"),
            ]
    translations = TranslatedFields(
        description=models.TextField(verbose_name="Description"),
        amenities=models.TextField(verbose_name="Amenities"),
    )
    property_type = models.CharField(
        max_length=100,
        choices=PROPERTY_TYPES,
        verbose_name="Property_type", 
        default="apartment"
    )
    name = models.CharField(max_length=255, verbose_name="Property Name",null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Price")
    owner = models.ForeignKey(
        "authentication.user", on_delete=models.CASCADE, related_name="property_owner"
    )
    country = models.CharField(verbose_name="Country", null=True, blank=True)
    state = models.CharField(verbose_name="State", null=True, blank=True)
    city = models.CharField(verbose_name="City", null=True, blank=True)
    postal_code = models.CharField(max_length=20, verbose_name="Postal Code", null=True,  blank=True)
    address = models.TextField(verbose_name="Address", null=True, blank=True)
    created_at = models.DateTimeField(auto_now=True)
    available = models.BooleanField(default=True, verbose_name="Availability")
    rating = models.DecimalField(
        max_digits=3, decimal_places=2, default=0.0, verbose_name="Rating"
    )
    webservice_username = models.CharField(max_length=255, null=True, blank=True, verbose_name="SES Username")
    webservice_password = models.CharField(max_length=255, null=True, blank=True, verbose_name="SES Password")
    establishment_code = models.CharField(max_length=255, null=True, blank=True, verbose_name="Establishment Code")
    landlord_code = models.CharField(max_length=255, null=True, blank=True, verbose_name="Landlord Code")
    ses_status = models.BooleanField(default=False, verbose_name="SES Connection Status")

    def __str__(self):
        return self.name if self.name else "Unnamed Property"

    def validate_ses_credentials(self):
        """Validate SES credentials and update status"""
        if all([self.webservice_username, self.webservice_password, 
                self.establishment_code, self.landlord_code]):
            try:
                xml_data = generate_ses_xml(
                    self.webservice_username, 
                    self.webservice_password, 
                    self.establishment_code, 
                    self.landlord_code
                )
                success, _ = send_validation_request(
                    xml_data, 
                    self.webservice_username, 
                    self.webservice_password,
                    verify_ssl=False
                )
                self.ses_status = success
                print('success: ', success)
                return success
            except Exception as e:
                self.ses_status = False
                raise e
        else:
            self.ses_status = False
            raise ValueError("Missing SES credentials")


class PropertyImage(models.Model):
    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name='images'
    )
    name = models.CharField(null=True, blank=True)
    image = models.ImageField(upload_to='properties_images/')
    name = models.CharField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class IsLanlordOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.role in ["SuperAdmin", "Landlord", "Admin"]
    

class IsSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):    
        return request.user.role == "SuperAdmin"