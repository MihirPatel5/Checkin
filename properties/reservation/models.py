from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from datetime import timedelta, datetime

from authentication.models import User
from property.models import Property


STATUS_CHOICES = (
        ('pending', _('Pending')),
        ('confirmed', _('Confirmed')),
        ('checked_in', _('Checked In')),
        ('checked_out', _('Checked Out')),
        ('cancelled', _('Cancelled')),
    )

SOURCE_CHOICES = (
    ('manual', _('Manual Entry')),
    ('airbnb', _("Airbnb")),
    ('booking', _("Booking")),
    ('other', _("Other")),
)

DOCUMENT_TYPE_CHOICES = (
        ('passport', _('Passport')),
        ('dni', _('DNI')),
        ('nie', _('NIE')),
        ('other', _('Other')),
    )

RELATIONSHIP_CHOICES = (
        ('parent', _('Parent')),
        ('guardian', _('Legal Guardian')),
        ('relative', _('Relative')),
        ('other', _('Other')),
    )

class Reservation(models.Model):
    property = models.ForeignKey("property.Property", on_delete=models.CASCADE)
    landlord = models.ForeignKey(User, on_delete=models.CASCADE)
    lead_guest_name = models.CharField(max_length=255)
    lead_guest_email = models.EmailField(max_length=255)
    lead_guest_phone = models.PositiveIntegerField()
    checkin_date = models.DateTimeField()
    checkout_date = models.DateTimeField()
    total_guests = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='manual')
    unique_checkin_link = models.URLField(unique=True)
    ical_url = models.URLField(blank=True, null=True, help_text=_('URL for iCal sync'))
    e_signature = models.ImageField(upload_to='signatures/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.unique_checkin_link:
            self.unique_checkin_link = f"/checkin/{self.pk}-{timezone.now().timestamp()}"
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.lead_guest_name} - {self.property.name}"

class Guest(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name="guests")
    is_lead_guest = models.BooleanField(default=False)
    full_name = models.CharField(max_length=255)
    first_surname = models.CharField(max_length=255)
    second_surname = models.CharField(max_length=255)
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPE_CHOICES)
    document_number = models.CharField(max_length=50)
    support_number = models.CharField(max_length=100, blank=True, null=True, help_text=_('Required for NIF/NIE'))
    nationalty = models.CharField(max_length=100, help_text=_('ISO 3166-1 Alpha-3'))
    dob = models.DateField()
    address = models.TextField()
    postal_code = models.CharField(max_length=20)
    city = models.CharField(max_length=100)
    contry = models.CharField(max_length=3, help_text=_('ISO 3166-1 Alpha-3'))
    is_minor = models.BooleanField(default=False)
    gurdian = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="minors")
    relationship_to_minor = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES, blank=True, null=True)
    purpose_of_stay = models.TextField(blank=True, null=True)
    id_photo = models.ImageField(upload_to='guest_ids/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.full_name
    
    def save(self, *args, **kwargs):
        today = datetime.now().date()
        age = today.year - self.dob.year -((today.month, today.day) < (self.dob.month, self.dob.day))
        self.is_minor = age < 18
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = _('Guest')
        verbose_name_plural = _('Guests')


class ICalFeed(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="ical_feeds")
    name = models.CharField(max_length=100, help_text=_('Name of the source (e.g., Airbnb, Booking.com)'))
    url = models.URLField(help_text=_('URL of the iCal feed'))
    last_synced = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.property.name}"
    
    class Meta:
        verbose_name = _("iCal_feed")
        verbose_name_pural = _('iCal_feeds')


class DataRetainPolicy(models.Model):
    """Model to manage GDPR compliance for guest data retention"""
    guest = models.OneToOneField(Guest, on_delete=models.CASCADE, related_name="retention_policy")
    deletion_date = models.DateField()
    is_anonymized = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.pk and not self.deletion_date:
            checkout_date = self.guest.reservation.checkout_date.date()
            self.deletion_date = checkout_date + timedelta(days=365*3)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Retention for {self.guest.full_name}"        