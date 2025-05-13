from rest_framework import serializers
from rest_framework.serializers import ValidationError
from .models import (
    DataRetentionPolicy, Guest, CheckIn, Municipality, IdentityDocumentType, CheckInStatus,
    GuardianRelationship, GuestbookEntry, PoliceSubmissionLog, PropertyICal, Reservation
)
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.conf import settings
from django.db import transaction
import phonenumbers

class MunicipalitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Municipality
        fields = ['codigo_municipio', 'nombre_municipio', 'provincia', 'codigo_postal']

class GuestSerializer(serializers.ModelSerializer):
    age = serializers.SerializerMethodField()
    is_minor = serializers.BooleanField(read_only=True)
    document_type = serializers.ChoiceField(
        choices=IdentityDocumentType.choices,
        required=True
    )
    municipality = serializers.DictField(child=serializers.CharField(), required=False, allow_null=True)
    translations = serializers.JSONField(required=False, read_only=True)
    is_lead = serializers.BooleanField(required=False, default=False)
    second_surname = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    permission_class = []

    class Meta:
        model = Guest
        fields = [
            'id', 'full_name', 'first_surname', 'second_surname',"is_lead",
            'document_type', 'document_number', 'support_number', 'is_minor',
            'nationality', 'date_of_birth', 'age', 'country_of_residence',
            'id_photo', 'municipality', 'gender', 'purpose_of_stay',
            'codigo_municipio', 'nombre_municipio', 'codigo_postal', 'provincia',
            'gdpr_consent', 'anonymized', 'translations'
        ]
        read_only_fields = ['is_minor', 'age', 'anonymized', 'translations']

    def get_age(self, obj):
        return obj.age

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if not (request and request.user.is_staff):
            lang = getattr(request, 'LANGUAGE_CODE', settings.LANGUAGE_CODE)
            translatable_fields = {
                'full_name': instance.get_translation('full_name', lang),
                'purpose_of_stay': instance.get_translation('purpose_of_stay', lang)
            }
            data.update(translatable_fields)
        return data

    def validate(self, data):
        property_country = self.context.get('property_country', None)
        nationality = data.get('nationality')
        document_type = data.get('document_type')
        if 'phone' in data:
            try:
                phone = phonenumbers.parse(data['phone'], None)
                if not phonenumbers.is_valid_number(phone):
                    raise serializers.ValidationError({"phone": _("Invalid phone number")})
            except:
                raise serializers.ValidationError({"phone": _("Invalid phone number format")})
        if property_country == 'ES' and nationality == 'ES':
            if document_type not in ['dni', 'nie']:
                raise serializers.ValidationError({
                    "document_type": _("Spanish guests must provide DNI or NIE")
                })
            if not data.get('support_number'):
                raise serializers.ValidationError({
                    "support_number": _("Support number is required for Spanish documents")
                })
            if not data.get('municipality'):
                raise serializers.ValidationError({
                    "municipality": _("Municipality is required for Spanish guests")
                })
        elif document_type != 'passport' and nationality != 'ES':
            raise serializers.ValidationError({
                "document_type": _("Non-Spanish guests must provide passport")
            })
        if data.get('date_of_birth') and data.get('date_of_birth') > timezone.now().date():
            raise serializers.ValidationError({
                "date_of_birth": _("Date of birth cannot be in the future")
            })
        return data
    
    def create(self, validated_data):
        municipality_data = validated_data.pop('municipality', None)
        if municipality_data:
            validated_data['codigo_municipio'] = municipality_data.get('codigo_municipio')
            validated_data['nombre_municipio'] = municipality_data.get('nombre_municipio')
            validated_data['codigo_postal'] = municipality_data.get('codigo_postal')
            validated_data['provincia'] = municipality_data.get('provincia')
        return super().create(validated_data)

    def update(self, instance, validated_data):
        municipality_data = validated_data.pop('municipality', None)
        if municipality_data:
            validated_data['codigo_municipio'] = municipality_data.get('codigo_municipio')
            validated_data['nombre_municipio'] = municipality_data.get('nombre_municipio')
            validated_data['codigo_postal'] = municipality_data.get('codigo_postal')
            validated_data['provincia'] = municipality_data.get('provincia')
        return super().update(instance, validated_data)


class GuardianRelationshipSerializer(serializers.Serializer):
    "Seriaizer for Guardian Relationship"
    class Meta:
        model = GuardianRelationship
        fields = ['minor', 'guardian', 'relationship_type', 'verified']
        read_only_fields = ['verified']

    def validate(self, data):
        minor = data.get('minor')
        guardian = data.get('guardian')
        if not minor or not guardian:
            raise serializers.ValidationError(_("Both minor and guardian must be specified."))
        if minor == guardian:
            raise serializers.ValidationError(_("Guardian and minor cannot be the same person"))
        if minor.age >= 18:
            raise serializers.ValidationError(_("Minor must be under 18 years old"))
        if guardian.age < 18:
            raise serializers.ValidationError(_("Guardian must be an adult"))
        return data

class CheckInSerializer(serializers.ModelSerializer):
    guests = GuestSerializer(many=True)
    guardian_relationships = GuardianRelationshipSerializer(many=True, required=False)
    digital_signature = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = CheckIn
        fields = [
            'id', 'reservation', 'guests', 'guardian_relationships',
            'status', 'digital_signature', 'ip_address', 'initiated_at',
            'completed_at'
        ]
        read_only_fields = ['status', 'ip_address', 'initiated_at', 'completed_at']

    def validate(self, data):
        guests = data.get('guests', [])
        reservation = self.context.get('reservation')
        if not any(g.get('is_lead', False) for g in guests):
            raise serializers.ValidationError(_("At least one lead guest is required"))
        if reservation and len(guests) != reservation.total_guests:
            raise serializers.ValidationError(
                _("Guest count mismatch. Expected %(expected)d, got %(actual)d") % {
                    'expected': reservation.total_guests,
                    'actual': len(guests)
                }
            )
        return data

    def create(self, validated_data):
        guests_data = validated_data.pop('guests')
        relationships_data = validated_data.pop('guardian_relationships', [])
        with transaction.atomic():
            check_in = CheckIn.objects.create(**validated_data)
            guests = []
            minors = []
            for guest_data in guests_data:
                municipality_data = guest_data.pop('municipality', None)
                if municipality_data:
                    guest_data['codigo_municipio'] = municipality_data.get('codigo_municipio')
                    guest_data['nombre_municipio'] = municipality_data.get('nombre_municipio')
                    guest_data['codigo_postal'] = municipality_data.get('codigo_postal')
                    guest_data['provincia'] = municipality_data.get('provincia')
                if 'last_surname' in guest_data:
                    guest_data['second_surname'] = guest_data.pop('last_surname')
                guest = Guest.objects.create(check_in=check_in, **guest_data)
                guests.append(guest)
                if guest.is_minor:
                    minors.append(guest)
            for rel_data in relationships_data:
                GuardianRelationship.objects.create(
                    minor=rel_data['minor'],
                    guardian=rel_data['guardian'],
                    relationship_type=rel_data['relationship_type']
                )
            for minor in minors:
                if not GuardianRelationship.objects.filter(minor=minor).exists():
                    raise serializers.ValidationError(
                        _("No guardian specified for minor %(name)s") % {'name': minor.full_name}
                    )
            return check_in

class ReservationSerializer(serializers.ModelSerializer):
    check_in_url = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)
    property_ref = serializers.PrimaryKeyRelatedField(read_only=True)  # ✅ Make it read-only

    class Meta:
        model = Reservation
        fields = [
            'id', 'reservation_code', 'property_ref', 'check_in_date',
            'check_out_date', 'status', 'source', 'check_in_link',
            'lead_guest_name', 'lead_guest_email', 'lead_guest_phone', 'total_guests',
            'check_in_url', 'is_auto_submit', 'gdpr_compliant', 'is_active'
        ]
        read_only_fields = ['reservation_code', 'check_in_link', 'status', 'property_ref']

    def get_check_in_url(self, obj):
        return f"{settings.FRONTEND_URL}/guest-checkin/{obj.check_in_link}"

    def validate_check_in_date(self, value):
        if value < timezone.now():
            raise ValidationError(_("Check-in date cannot be in the past"))
        return value

class PoliceSubmissionLogSerializer(serializers.ModelSerializer):
    xml_preview = serializers.SerializerMethodField()

    class Meta:
        model = PoliceSubmissionLog
        fields = [
            'id', 'check_in', 'status', 'submitted_at', 'xml_version',
            'xml_preview', 'retry_count', 'error_message'
        ]
        read_only_fields = ['submitted_at', 'xml_version']

    def get_xml_preview(self, obj):
        return obj.raw_request[:100] + ' [...]' if obj.raw_request else ''

class PropertyICalSerializer(serializers.ModelSerializer):
    sync_status = serializers.SerializerMethodField()
    last_synced = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")

    class Meta:
        model = PropertyICal
        fields = ['id', 'url', 'is_active', 'last_synced', 'sync_status']
        read_only_fields = ['last_synced']

    def get_sync_status(self, obj):
        if not obj.last_synced:
            return "Never synced"
        return "Active" if obj.is_active else "Inactive"

class DataRetentionPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = DataRetentionPolicy
        fields = ['id', 'retention_period', 'auto_anonymize', 'last_cleanup']
        extra_kwargs = {
            'retention_period': {'min_value': 1095}
        }

class GuestbookEntrySerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    class Meta:
        model = GuestbookEntry
        fields = ['id', 'generation_date', 'download_url']
        read_only_fields = ['generation_date']

    def get_download_url(self, obj):
        return obj.generated_file.url if obj.generated_file else None