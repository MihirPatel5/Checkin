import datetime
from rest_framework import serializers
from .models import Reservation, Guest, ICalFeed, DataRetainPolicy
from property.models import Property


class GuestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Guest
        fields ="__all__"
        read_only_fields = ('is_minor',)

    def validate(self,data):
        if data.get('document_type') in ['nie', 'dni']:
            if not data.get('second_surname'):
                raise serializers.ValidationError({"second_surname": "Second surname is required for NIF/NIE document types"})
            if not data.get('support_number'):
                raise serializers.ValidationError({"support_number": "Support Number is required for NIF/NIE document types"})
        return data


class ICalFeedSerializer(serializers.ModelSerializer):
    class Meta:
        model = ICalFeed
        fields = '__all__'
        read_only_fields = ('last_synced',)


class ReservationSerializer(serializers.ModelSerializer):
    guests = GuestSerializer(many=True, required=False, read_only=True)
    property_name = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = '__all__'
        read_only_fields = ('id', 'check_in_link', 'created_at', 'updated_at')
    
    def get_property_name(self, obj):
        return obj.property.name
    t
    def validate(self, data):
        # Validate check-in and check-out dates
        if data.get('check_in_date') and data.get('check_out_date'):
            if data['check_in_date'] >= data['check_ou_date']:
                raise serializers.ValidationError({"check_out_date": "Check-out date must be after check-in date"})
        
        # Validate property exists
        if 'property' in data and not Property.objects.filter(id=data['property'].id).exists():
            raise serializers.ValidationError({"property": "Invalid property ID"})
        
        return data


class ReservationCreateSerializer(ReservationSerializer):
    guests = GuestSerializer(many=True, required=False)

    def create(self, validated_data):
        guests_data = validated_data.pop('guests', [])
        reservation = Reservation.objects.create(**validated_data)

        for guest_data in guests_data:
            Guest.objects.create(reservation=reservation, **guests_data)
        return reservation


class DataRetainPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = DataRetainPolicy
        fields = '__all__'
        read_only_fields = ('deletion_date', 'is_anonymized')


class CheckInFormSerializer(serializers.ModelSerializer):
    lead_guest_name = serializers.CharField(max_length=255)
    lead_guest_email = serializers.EmailField()
    lead_guest_phone = serializers.CharField(max_length=20)
    total_guests = serializers.IntegerField(min_value=1)
    guests = serializers.ListField(child=serializers.DictField())
    electronic_signature = serializers.ImageField(required=False)

    def validate_guests(self, value):
        if not value:
            raise serializers.ValidationError("At least one guest must be provided")
        if len(value) != self.initial_data.get('total_guests', 1):
            raise serializers.ValidationError(f"Number of guests must match total_guests ({self.initial_data.get('total_guests', 1)})")
        required_fields = ['full_name', 'first_surname', 'document_type', 'document_number', 
                           'nationality', 'date_of_birth', 'address', 'postal_code', 
                           'city', 'country_of_residence']
        for i, guest in enumerate(value):
            missing_fields = [field for field in required_fields if field not in guest or not guest[field]]
            if missing_fields:
                raise serializers.ValidationError(f"Guest {i+1} is missing required fields: {', '.join(missing_fields)}")
            if guest.get('document_type') in ['nie', 'dni']:
                if not guest.get('second_surname'):
                    raise serializers.ValidationError(f"Guest {i+1}: Second surname is required for NIF/NIE document types")
                if not guest.get('support_number'):
                    raise serializers.ValidationError(f"Guest {i+1}: Support number is required for NIF/NIE document types")
            try:
                dob = datetime.strptime(guest['date_of_birth'], '%Y-%m-%d').date()
                today = datetime.now().date()
                age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                if age < 18:
                    if not guest.get('guardian_id') and not guest.get('is_lead_guest'):
                        raise serializers.ValidationError(f"Guest {i+1} is a minor and requires a guardian")
                    if guest.get('guardian_id') and not guest.get('relationship_to_minor'):
                        raise serializers.ValidationError(f"Guest {i+1} requires relationship to guardian")
            except (ValueError, KeyError):
                raise serializers.ValidationError(f"Guest {i+1}: Invalid date of birth format. Use YYYY-MM-DD")
        return value