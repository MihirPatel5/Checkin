
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.conf import settings
import uuid, shortuuid
from property.models import Property


class IdentityDocumentType(models.TextChoices):
    PASSPORT = 'passport', _('Passport')
    DNI = 'dni', _('DNI')
    NIE = 'nie', _('NIE')
    OTHER ='other', _('Other')


class GuestRelationType(models.TextChoices):
    PARENT = 'parent', _('Parent')
    GUARDIAN = 'guardian', _('Guardian')
    OTHER = 'other', _('Other')


class CheckInStatus(models.TextChoices):
    PENDING = 'pending', _('Pending')
    SUBMITTED = 'submitted', _('Submitted')
    REJECTED = 'rejected', _('Rejected')
    CONFIRMED ='confirmed', _('Confirmed')


class Municipality(models.Model):
    """Model to store Spanish municipality codes"""
    codigo_municipio = models.CharField(max_length=5)
    nombre_municipio = models.CharField(max_length=100)
    provincia = models.CharField(max_length=50)
    codigo_postal = models.CharField(max_length=5)
    
    class Meta:
        unique_together = ('codigo_municipio', 'nombre_municipio', 'codigo_postal')
    
    def __str__(self):
        return f"{self.nombre_municipio} ({self.codigo_municipio})"


class CheckIn(models.Model):
    """Model to store CheckIn information for a property"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property_ref = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='check_ins')
    lead_guest_name = models.CharField(max_length=255)
    lead_guest_email = models.EmailField()
    lead_guest_phone = models.CharField(max_length=20)
    total_guests = models.PositiveIntegerField()
    check_in_date = models.DateTimeField()
    check_out_date = models.DateTimeField()
    status = models.CharField(max_length=20, choices=CheckInStatus.choices, default=CheckInStatus.PENDING)
    check_in_link = models.CharField(max_length=10, unique=True, editable=False)
    purpose_of_stay = models.TextField(blank=True, null=True)
    digital_signature = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    auto_submit_to_police = models.BooleanField(default=False)
    submission_date = models.DateTimeField(blank=True, null=True)
    submission_log = models.TextField(blank=True, null=True)
    reservation_id = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"CheckIn for {self.property_ref} - {self.lead_guest_name}"
    
    class Meta:
        ordering = ['check_in_date']

    def save(self, *args, **kwargs):
        if not self.check_in_link:
            self.check_in_link = shortuuid.ShortUUID().random(length=6).upper()
        super().save(*args, **kwargs)

    @property
    def has_minors(self):
        """Check if there are any minor guests in this check-in"""
        return self.guests.filter(is_minor=True).exists()
    
    @property
    def is_pending_police_submission(self):
        """Check if this check-in should be submitted to police"""
        return (
            self.auto_submit_to_police and 
            self.status == CheckInStatus.CONFIRMED and
            not self.submission_date and
            self.check_in_date > timezone.now()
        )
    
    @property
    def is_complete(self):
        """Check if all required information is provided"""
        if self.guests.count() != self.total_guests:
            return False        
        for guest in self.guests.all():
            if not guest.is_valid_for_submission(self.property_ref.country):
                return False
        for guest in self.guests.filter(is_minor=True):
            if not guest.has_guardian:
                return False                
        if not self.digital_signature:
            return False
        return True


class Guest(models.Model):
    """Model to store individual guest information"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    check_in = models.ForeignKey(CheckIn, on_delete=models.CASCADE, related_name='guests')
    full_name = models.CharField(max_length=255)
    first_surname = models.CharField(max_length=255)
    second_surname = models.CharField(max_length=255, blank=True, null=True)
    document_type = models.CharField(max_length=20, choices=IdentityDocumentType.choices)
    document_number = models.CharField(max_length=50)
    support_number = models.CharField(max_length=50, blank=True, null=True)
    nationality = models.CharField(max_length=3)  # ISO 3166-1 Alpha-3
    date_of_birth = models.DateField()
    address = models.TextField()
    postal_code = models.CharField(max_length=20)
    city = models.CharField(max_length=100)
    country_of_residence = models.CharField(max_length=3)  # ISO 3166-1 Alpha-3
    is_lead_guest = models.BooleanField(default=False)
    is_minor = models.BooleanField(default=False)
    id_photo = models.ImageField(upload_to='guest_ids/', blank=True, null=True)
    codigo_municipio = models.CharField(max_length=10, blank=True, null=True)
    nombre_municipio = models.CharField(max_length=255, blank=True, null=True)
    codigo_postal = models.CharField(max_length=10, blank=True, null=True)
    provincia = models.CharField(max_length=255, blank=True, null=True)
    gender = models.CharField(max_length=10, choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')], blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.full_name} - {self.document_number}"
    
    def save(self, *args, **kwargs):
        if self.date_of_birth:
            today = timezone.now().date()
            age = today.year - self.date_of_birth.year - ((today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
            self.is_minor = age < 18
        super().save(*args, **kwargs)

    def is_valid_for_submission(self, property_country_code):
        """Check if guest has all required fields based on property location and nationality"""
        if property_country_code == 'ES' and self.nationality == 'ES':
            return all([
                self.document_type in ['DNI', 'NIE'],
                self.document_number,
                self.support_number,
                self.codigo_municipio and self.nombre_municipio
            ])
        return all([
            self.document_type,
            self.document_number,
            self.nationality,
            self.date_of_birth,
            self.address,
            self.postal_code,
            self.city,
            self.country_of_residence
        ])

    @property
    def has_guardian(self):
        """Check if this minor guest has at least one guardian"""
        if not self.is_minor:
            return None
        return GuardianRelationship.objects.filter(minor=self).exists()
    
    @property
    def age(self):
        """Calculate the age of the guest"""
        if not self.date_of_birth:
            return None
        today = timezone.now().date()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )


class GuardianRelationship(models.Model):
    """Model for tracking guardian relationship with minor"""
    minor = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="guardian")
    guardian = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="minors")
    relationship_type = models.CharField(max_length=20, choices=GuestRelationType.choices)

    class Meta:
        unique_together =('minor', 'guardian')


class GuestbookEntry(models.Model):
    """Model to track guestbook entries"""
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="guestbook_entries")
    check_in = models.ForeignKey(CheckIn, on_delete=models.CASCADE, related_name="guestbook_entries")
    generated_file = models.FileField(upload_to='guestbook/', blank=True, null=True)
    generation_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Guestbokk for {self.property} - {self.generation_date}"
    
    class Meta:
        ordering = ['-generation_date']


class PoliceSubmissionLog(models.Model):
    """Model to tracke police submission logs"""
    check_in = models.ForeignKey(CheckIn, on_delete=models.CASCADE, related_name="police_submissions")
    submitted_at = models.DateTimeField(auto_now=True)
    submitted_type = models.CharField(max_length=20, choices=[
        ('auto', _('Automatic')),
        ('manual', _('Manual')),
    ])
    status = models.CharField(max_length=20, choices=[
        ('success', _('Success')),
        ('failed', _('Failed')),
    ])
    response_data = models.TextField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Police submission for {self.check_in} - {self.submitted_at}"
    
    class Meta:
        ordering = ['-submitted_at']