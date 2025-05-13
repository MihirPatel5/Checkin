
import re
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey
from django.utils import timezone
from django.conf import settings
import uuid, shortuuid
from rest_framework.serializers import ValidationError
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


class ReservationStatus(models.TextChoices):
    CONFIRMED = 'confirmed', _('Confirmed')
    CANCELLED = 'cancelled', _('Cancelled')


class CheckInStatus(models.TextChoices):
    PENDING = 'pending', _('Pending')
    IN_PROGRESS = 'in_progress', _('In Progress')
    COMPLETED = 'completed', _('Completed')
    EXPIRED = 'expired', _('Expired')


class Municipality(models.Model):
    """Model to store Spanish municipality codes"""
    codigo_municipio = models.CharField(max_length=5)
    nombre_municipio = models.CharField(max_length=100)
    provincia = models.CharField(max_length=50)
    codigo_postal = models.CharField(max_length=5)
    
    class Meta:
        unique_together = ('codigo_municipio', 'nombre_municipio', 'codigo_postal')
    
    @classmethod
    def validate_spanish_address(cls, postal_code, municipality):
        try:
            return cls.objects.get(
                postal_code=postal_code,
                name__iexact=municipality
            )
        except cls.DoesNotExist:
            raise ValidationError(_("Invalid Spanish municipality/postal code combination"))
    
    def __str__(self):
        return f"{self.nombre_municipio} ({self.codigo_municipio})"


class Reservation(models.Model):
    """Model to store CheckIn information for a property"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation_code = models.CharField(max_length=8, unique=True, editable=False)
    property_ref = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='reservations')
    check_in_date = models.DateTimeField()
    check_out_date = models.DateTimeField()
    lead_guest_name = models.CharField(null=True, blank=True)
    lead_guest_email = models.EmailField(null=True, blank=True)
    lead_guest_phone = models.CharField(max_length=20, null=True, blank=True)
    total_guests = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=ReservationStatus.choices, default=ReservationStatus.CONFIRMED)
    source = models.CharField(max_length=20, choices=[
        ('manual', _('Manual_Creation')),
        ('ical', _('iCal_Import'))
    ], default='manual')
    ical_uid = models.CharField(max_length=255, blank=True, null=True)
    check_in_link = models.CharField(max_length=10, unique=True, editable=False)
    is_auto_submit = models.BooleanField(default=True)
    gdpr_compliant = models.BooleanField(default=False)
    data_purge_date = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    
    class Meta:
        ordering = ['check_in_date']
        indexes = [
            models.Index(fields=['reservation_code']),
            models.Index(fields=['check_in_date', 'check_out_date']),
        ]

    def save(self, *args, **kwargs):
        if not self.reservation_code:
            self.reservation_code = shortuuid.ShortUUID().random(length=8).upper()
        if not self.check_in_link:
            self.check_in_link = shortuuid.ShortUUID().random(length=8).upper()
        super().save(*args, **kwargs)

    @property
    def days_until_checkin(self):
        return (self.check_in_date - timezone.now()).days

    @property
    def is_active(self):
        return self.status == ReservationStatus.CONFIRMED and \
            self.check_in_date <= timezone.now() <= self.check_out_date

    def __str__(self):
        return f"{self.property_ref.name} - {self.reservation_code}"


class PropertyICal(models.Model):
    """Store iCal URLs for automated reservation imports"""
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='ical_sources')
    url = models.URLField(max_length=512)
    is_active = models.BooleanField(default=True)
    last_synced = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Property iCal Configuration')
        verbose_name_plural = _('Property iCal Configurations')

    def __str__(self):
        return f"iCal for {self.property.name}"


class CheckIn(models.Model):
    """Manages guest check-in process for a reservation"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation = models.OneToOneField(Reservation, on_delete=models.CASCADE, related_name='check_in')
    status = models.CharField(max_length=20, choices=CheckInStatus.choices, default=CheckInStatus.PENDING)
    initiated_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    digital_signature = models.TextField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ['-initiated_at']
        verbose_name = _('Check-In Process')
        verbose_name_plural = _('Check-In Processes')

    @property
    def is_expired(self):
        return timezone.now() > self.reservation.check_in_date + timezone.timedelta(hours=24)

    def save(self, *args, **kwargs):
        if self.is_expired and self.status != CheckInStatus.EXPIRED:
            self.status = CheckInStatus.EXPIRED
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Check-In for {self.reservation}"


class Translation(models.Model):
    content_type = models.ForeignKey(
        ContentType, 
        on_delete=models.CASCADE,
        verbose_name=_("Content Type")
    )
    content_object = GenericForeignKey('content_type', 'object_id')
    source_text = models.TextField()
    source_language = models.CharField(max_length=10, default='auto')
    target_language = models.CharField(max_length=10)
    translated_text = models.TextField()
    object_id = models.UUIDField()
    field_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        unique_together = ('object_id', 'field_name', 'target_language', 'content_type')


class Guest(models.Model):
    """Stores detailed information about guests"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    check_in = models.ForeignKey(CheckIn, on_delete=models.CASCADE, related_name='guests')
    is_primary = models.BooleanField(default=False)
    full_name = models.CharField(max_length=255)
    first_surname = models.CharField(max_length=255)
    last_surname = models.CharField(max_length=255)
    second_surname = models.CharField(max_length=255, blank=True, null=True)
    date_of_birth = models.DateField()
    nationality = models.CharField(max_length=3)  # ISO 3166-1 alpha-3
    document_type = models.CharField(max_length=20, choices=IdentityDocumentType.choices)
    document_number = models.CharField(max_length=50)
    country_of_residence = models.CharField(max_length=3)  # ISO 3166-1 Alpha-3    
    support_number = models.CharField(max_length=50, blank=True, null=True)
    municipality = models.ForeignKey(
        'Municipality', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    is_lead = models.BooleanField(default=False)
    id_photo = models.ImageField(
        upload_to='guest_ids/',
        null=True,
        blank=True,
        help_text=_('Enabled/disabled by property configuration')
    )
    codigo_municipio = models.CharField(max_length=10, blank=True, null=True)
    nombre_municipio = models.CharField(max_length=255, blank=True, null=True)
    codigo_postal = models.CharField(max_length=10, blank=True, null=True)
    provincia = models.CharField(max_length=255, blank=True, null=True)
    gender = models.CharField(max_length=10, choices=[('Male', 'Male'), ('Female', 'Female'), ('Other', 'Other')], blank=True)
    purpose_of_stay = models.TextField(null=True, blank=True)
    gdpr_consent = models.BooleanField(default=False)
    anonymized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    translations = models.JSONField(
        default=dict,
        blank=True,
        help_text="Stores translations in format {'en': {'field_name': 'translation'}}"
    )

    def get_translation(self, field_name, lang=settings.LANGUAGE_CODE):
        return self.translations.get(lang, {}).get(field_name, getattr(self, field_name))

    def save_translation(self, field_name, lang, translated_text, user=None):
        self.translations.setdefault(lang, {})[field_name] = translated_text
        Translation.objects.update_or_create(
            content_type=ContentType.objects.get_for_model(self.__class__),
            object_id=self.id,
            field_name=field_name,
            target_language=lang,
            defaults={
                'source_text': getattr(self, field_name),
                'translated_text': translated_text,
                'created_by': user,
            }
        )
        self.save()

    def anonymize(self):
        if not self.anonymized:
            self.first_surname = f"ANON-{uuid.uuid4().hex[:6]}"
            self.last_surname = ""
            self.anonymized = True
            self.save()

    class Meta:
        ordering = ['-is_primary', 'last_surname', 'first_surname']

    @property
    def full_name(self):
        return f"{self.first_surname} {self.last_surname}"

    @property
    def age(self):
        today = timezone.now().date()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < 
            (self.date_of_birth.month, self.date_of_birth.day)
        )

    @property
    def is_minor(self):
        return self.age < 18

    def __str__(self):
        return f"{self.full_name} ({self.nationality})"


class GuardianRelationship(models.Model):
    """Model for tracking guardian relationship with minor"""
    minor = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="guardian")
    guardian = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="minors")
    relationship_type = models.CharField(max_length=20, choices=GuestRelationType.choices)
    verified = models.CharField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together =('minor', 'guardian')
        verbose_name = _('Guardian Relationship')

    def __str__(self):
        return f"{self.guardian} → {self.minor} ({self.relationship_type})"


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
    success = models.BooleanField()
    raw_request = models.TextField()
    raw_response = models.TextField()
    error_message = models.TextField(blank=True)
    xml_version = models.CharField(max_length=10)
    validation_errors = models.JSONField(default=list)
    retry_count = models.PositiveIntegerField(default=0)
    next_retry = models.DateTimeField(null=True)
    
    class SubmissionStatus(models.TextChoices):
        PENDING = 'pending', _('Pending')
        VALIDATED = 'validated', _('Validated')
        SUBMITTED = 'submitted', _('Submitted')
        FAILED = 'failed', _('Failed')
    
    status = models.CharField(
        max_length=20,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.PENDING
    )

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = _('Police Submission')

    def __str__(self):
        status = "Success" if self.success else "Failed"
        return f"Police Submission ({status}) for {self.check_in}"


class SpanishDocumentValidator:
    @staticmethod
    def validate_nie(value):
        pattern = r'^[XYZ]\d{7}[A-Z]$'
        return re.match(pattern, value)
    
    @staticmethod
    def validate_dni(value):
        pattern = r'^\d{8}[A-Z]$'
        return re.match(pattern, value)


class DataRetentionPolicy(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE)
    retention_period = models.PositiveIntegerField(
        default=1095,  # 3 years in days
        help_text=_("Data retention period in days")
    )
    auto_anonymize = models.BooleanField(default=True)
    last_cleanup = models.DateTimeField(null=True)
    
    @classmethod
    def enforce_retention_policies(cls):
        for policy in cls.objects.all():
            cutoff_date = timezone.now() - timezone.timedelta(days=policy.retention_period)
            guests = Guest.objects.filter(
                check_in__reservation__property=policy.property,
                created_at__lte=cutoff_date
            )
            
            for guest in guests:
                if policy.auto_anonymize:
                    guest.anonymize()
                else:
                    guest.delete()
