from rest_framework import serializers
from rest_framework.serializers import ValidationError
from .models import (
    Guest, CheckIn, Municipality, IdentityDocumentType, CheckInStatus,
    GuardianRelationship, GuestbookEntry, PoliceSubmissionLog
)
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.conf import settings
import phonenumbers

class MunicipalitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Municipality
        fields = ['codigo_municipio', 'nombre_municipio', 'provincia', 'codigo_postal']

class GuestSerializer(serializers.ModelSerializer):
    id_photo = serializers.ImageField(required=False)
    age = serializers.SerializerMethodField()
    municipality = serializers.DictField(child=serializers.CharField(), required=False)
    permission_class = []

    class Meta:
        model = Guest
        fields = [
            'id', 'full_name', 'first_surname', 'second_surname',
            'document_type', 'document_number', 'support_number',
            'nationality', 'date_of_birth', 'age', 'address', 'postal_code',
            'city', 'country_of_residence', 'is_lead_guest', 'is_minor',
            'id_photo', 'municipality', 'gender',
            'codigo_municipio', 'nombre_municipio', 'codigo_postal', 'provincia',
        ]
        read_only_fields = ['is_minor', 'age']

    def get_age(self, obj):
        return obj.age

    def validate(self, data):
        property_country = self.context.get('property_country', None)
        if 'phone' in data:
            try:
                phone = phonenumbers.parse(data['phone'], None)
                if not phonenumbers.is_valid_number(phone):
                    raise serializers.ValidationError({"phone": _("Invalid phone number")})
            except:
                raise serializers.ValidationError({"phone": _("Invalid phone number format")})
        if property_country == 'ES' and data.get('nationality') == 'ES':
            if data.get('document_type') not in ['dni', 'nie']:
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
        else:
            if data.get('document_type') != IdentityDocumentType.PASSPORT:
                raise serializers.ValidationError({
                    "document_type": _("Non-Spanish guests must provide passport")
                })
        if data.get('date_of_birth') and data.get('date_of_birth') > timezone.now().date():
            raise serializers.ValidationError({
                "date_of_birth": _("Date of birth cannot be in the future")
            })
        return data


class GuardianRelationshipSerializer(serializers.Serializer):
    "Seriaizer for Guardian Relationship"
    class Meta:
        model = GuardianRelationship
        fields = ['minor', 'guardian', 'relationship_type']
    minor = serializers.CharField()
    guardian = serializers.CharField()
    relationship_type = serializers.CharField()
    
    def validate(self, data):
        return data


class CheckInLinkSerializer(serializers.ModelSerializer):
    check_in_url = serializers.SerializerMethodField()
    
    class Meta:
        model = CheckIn
        fields = ['id', 'check_in_link', 'check_in_url', 'property_ref', 'lead_guest_name', 
                 'lead_guest_email', 'lead_guest_phone', 'total_guests',
                 'check_in_date', 'check_out_date']
        read_only_fields = ['id', 'check_in_link', 'check_in_url']
    
    def get_check_in_url(self, obj):
        return f"{settings.FRONTEND_URL}/guest-checkin/{obj.check_in_link}"


class CheckInCreateSerializer(serializers.ModelSerializer):
    """serializer for creating a CheckIn with guest"""
    guests = GuestSerializer(many=True)
    guardian_relationships = GuardianRelationshipSerializer(many=True, required=False)

    class Meta:
        model = CheckIn
        fields = [
            'id', 'guests', 'guardian_relationships',
            'lead_guest_name', 'lead_guest_email',
            'lead_guest_phone', 'total_guests', 'check_in_date',
            'check_out_date', 'purpose_of_stay', 'auto_submit_to_police',
            'digital_signature'
        ]
        read_only_fields = ['id']
    
    def validate(self, data):
        property_country = self.context.get('property_country', None)
        if 'guests' in data and len(data['guests']) != data.get('total_guests', 0):
            raise ValidationError({
                'total_guests': _("The number of guests provided does not match the total_guests value")
            })
        if data.get('check_in_date') and data.get('check_out_date'):
            if data['check_in_date'] >= data['check_out_date']:
                raise ValidationError({
                    "check_out_date":_('Check out date must be after check-in date')
                })
        if property_country == 'ES':
            spanish_guests = [g for g in data.get('guests', []) if g.get('nationality') == 'ES']
            for guest in spanish_guests:
                if not guest.get('municipality'):
                    raise ValidationError({
                        "municipality": _("Spanish guests must have municipality specified")
                    })
        return data

    def create(self, validated_data):
        guests_data = validated_data.pop('guests', [])
        guardian_relationships_data = validated_data.pop('guardian_relationships', [])
        
        check_in = CheckIn.objects.create(**validated_data)
        guests_mapping = {}

        for guest_data in guests_data:
            if 'municipality' in guest_data:
                municipality_data = guest_data.pop('municipality')
                municipality = Municipality.objects.get(
                    codigo_municipio=municipality_data['codigo_municipio'],
                    nombre_municipio=municipality_data['nombre_municipio']
                )
                guest_data['municipality'] = municipality
            guest = Guest.objects.create(check_in=check_in, **guest_data)
            guests_mapping[str(guest.id)] = guest            
            
            if guest_data.get('is_lead_guest'):
                check_in.lead_guest_name = guest.full_name
                check_in.lead_guest_email = validated_data.get('lead_guest_email')
                check_in.lead_guest_phone = validated_data.get('lead_guest_phone')
                check_in.save()
        for relationship_data in guardian_relationships_data:
            minor_id = relationship_data.get('minor')
            guardian_id = relationship_data.get('guardian')            
            try:
                minor = Guest.objects.get(id=minor_id, check_in=check_in)
                guardian = Guest.objects.get(id=guardian_id, check_in=check_in)
                if not minor.is_minor:
                    raise ValidationError("The specified guest is not a minor.")
                if guardian.is_minor:
                    raise ValidationError("A guardian cannot be a minor.")
                if minor == guardian:
                    raise ValidationError("A guest cannot be their own guardian.")
                GuardianRelationship.objects.create(
                    minor=minor,
                    guardian=guardian,
                    relationship_type=relationship_data.get('relationship_type')
                )
            except Guest.DoesNotExist:
                raise ValidationError("Invalid guardian or minor reference.")
        return check_in

class CheckInDetailSerializer(serializers.ModelSerializer):
    """Serializer for retrieving CheckIn details"""
    guests = GuestSerializer(many=True, read_only=True)
    guardian_relationships = serializers.SerializerMethodField()
    remaining_time = serializers.SerializerMethodField()

    class Meta:
        model = CheckIn
        fields = [
            'id', 'property_ref', 'lead_guest_name', 'lead_guest_email',
            'lead_guest_phone', 'total_guests', 'check_in_date',
            'check_out_date', 'status', 'purpose_of_stay',
            'digital_signature', 'guests', 'guardian_relationships',
            'created_at', 'updated_at', 'auto_submit_to_police',
            'submission_date', 'remaining_time'
        ]
        read_only_fields = ['status', 'created_at', 'updated_at', 'submission_date']
    
    def get_guardian_relationships(self, obj):
        """Get guardian relationships for the CheckIn"""
        relationships = GuardianRelationship.objects.filter(
            minor__check_in=obj
        ).select_related('minor', 'guardian')
        return GuardianRelationshipSerializer(relationships, many=True).data
    
    def get_remaining_time(self, obj):
        """Get time remaining until check-in"""
        now = timezone.now()
        if obj.check_in_date > now:
            time_diff = obj.check_out_date - now
            print('time_diff: ', time_diff)
            return {
                'days': time_diff.days,
                'hours': time_diff.seconds // 3600,
                'minutes': (time_diff.seconds % 3600) // 60
            }
        return None


class CheckInUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating CheckIn status"""
    class Meta:
        model = CheckIn
        fields = ['status', 'auto_submit_to_police']


class CheckInSubmitSerializer(serializers.ModelSerializer):
    """Serializer for submitting a check-in"""
    guests = GuestSerializer(many=True, required=True)
    guardian_relationships = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        write_only=True
    )
    digital_signature = serializers.CharField(required=True)

    class Meta:
        model = CheckIn
        fields = [
            'id', 'guests', 'guardian_relationships',
            'purpose_of_stay', 'digital_signature'
        ]
        read_only_fields = ['id']

    def validate(self, data):
        if not data.get('digital_signature'):
            raise serializers.ValidationError({
                "digital_signature": _("Digital signature is required")
            })
        if not any(guest.get('is_lead_guest', False) for guest in data.get('guests', [])):
            raise serializers.ValidationError({
                "guests": _("At least one guest must be the lead guest")
            })
        return data

    def update(self, instance, validated_data):
        guests_data = validated_data.pop('guests', [])
        guardian_relationships_data = validated_data.pop('guardian_relationships', [])
        if len(guests_data) > instance.total_guests:
            raise ValidationError({"error":"Guest must registered or Guests are not more that total guests"})
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        created_guests = []
        for guest_data in guests_data:
            municipality_data = guest_data.pop('municipality', None)
            if municipality_data:
                guest_data.update({
                    'codigo_municipio': municipality_data.get('municipio_id'),
                    'nombre_municipio': municipality_data.get('municipio_nombre'),
                    'codigo_postal': municipality_data.get('codigo_postal'),
                    'provincia': guest_data.get('province'),
                })
            guest = Guest.objects.create(check_in=instance, **guest_data)
            created_guests.append(guest)
            if guest_data.get('is_lead_guest', False):
                instance.lead_guest_name = f"{guest.full_name} {guest.first_surname}"
                instance.lead_guest_email = guest_data.get('email', '')
                instance.lead_guest_phone = guest_data.get('phone', '')
        if guardian_relationships_data:
            for rel_data in guardian_relationships_data:
                try:
                    minor_index = rel_data.get('minor_index')
                    guardian_index = rel_data.get('guardian_index')                    
                    if minor_index is None or guardian_index is None:
                        raise ValidationError("Missing minor_index or guardian_index")
                    minor = created_guests[minor_index]
                    guardian = created_guests[guardian_index]
                    if not minor.is_minor:
                        raise ValidationError("The specified guest is not a minor.")
                    if guardian.is_minor:
                        raise ValidationError("A guardian cannot be a minor.")
                    if minor.id == guardian.id:
                        raise ValidationError("A guest cannot be their own guardian.")
                    GuardianRelationship.objects.create(
                        minor=minor,
                        guardian=guardian,
                        relationship_type=rel_data.get('relationship_type', 'parent')
                    )
                except IndexError:
                    raise ValidationError("Invalid guest index in relationship")
                except Exception as e:
                    raise ValidationError(str(e))
        if instance.is_complete:
            instance.status = CheckInStatus.CONFIRMED
        instance.save()
        return instance


class GuestbookEntrySerializer(serializers.ModelSerializer):
    """Serializer for guestbook entries"""
    property_name = serializers.CharField(source='property.name', read_only=True)
    lead_guest_name = serializers.CharField(source='check_in.lead_guest_name', read_only=True)
    
    class Meta:
        model = GuestbookEntry
        fields = [
            'id', 'property', 'property_name', 'check_in', 'lead_guest_name',
            'generated_file', 'generation_date'
        ]
        read_only_fields = ['generation_date']


class PoliceSubmissionLogSerializer(serializers.ModelSerializer):
    """Serializer for police submission logs"""
    check_in_id = serializers.UUIDField(source='check_in.id', read_only=True)
    property_name = serializers.CharField(source='check_in.property_ref.name', read_only=True)
    lead_guest_name = serializers.CharField(source='check_in.lead_guest_name', read_only=True)
    
    class Meta:
        model = PoliceSubmissionLog
        fields = [
            'id', 'check_in', 'check_in_id', 'property_name', 'lead_guest_name',
            'submitted_at', 'submitted_type', 'status', 'response_data', 'error_message'
        ]
        read_only_fields = ['submitted_at', 'response_data', 'error_message']