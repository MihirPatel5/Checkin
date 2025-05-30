from decimal import Decimal
from checkin.utils import send_checkin_confirmation, send_police_submission_notification
from payment.models import Transaction, Upsell
from payment.services.payment_service import PaymentService
from rest_framework import serializers
from rest_framework.serializers import ValidationError
from .models import (
    DataRetentionPolicy, Guest, CheckIn, Municipality, IdentityDocumentType, CheckInStatus,
    GuardianRelationship, GuestbookEntry, PoliceSubmissionLog, PropertyICal, Reservation, SelectedUpsell
)
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.conf import settings
from django.db import transaction
import phonenumbers, logging

logger = logging.getLogger(__name__)

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
    last_name2 = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = Guest
        fields = [
            'id', 'full_name', 'first_name', 'last_name2',"is_lead",
            'document_type', 'document_number', 'support_number', 'is_minor',
            'nationality', 'date_of_birth', 'age', 'country_of_residence',
            'id_photo', 'municipality', 'gender', 'purpose_of_stay',
            'codigo_municipio', 'nombre_municipio', 'codigo_postal', 'provincia',
            'gdpr_consent', 'anonymized', 'translations',
            'phone_number', 'email', 'street_address'
        ]
        read_only_fields = ['is_minor', 'age', 'anonymized', 'translations', 'codigo_municipio', 'nombre_municipio', 'codigo_postal', 'provincia']

    def get_age(self, obj):
        return obj.age

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if not (request and request.user.is_staff):
            lang = getattr(request, 'LANGUAGE_CODE', settings.LANGUAGE_CODE)
            translatable_fields = {}
            for field_name in ['full_name', 'purpose_of_stay']:
                 translated_value = instance.get_translation(field_name, lang)
                 if translated_value != getattr(instance, field_name):
                     translatable_fields[field_name] = translated_value
            data['municipality'] = {
                'codigo_municipio': instance.codigo_municipio,
                'nombre_municipio': instance.nombre_municipio,
                'codigo_postal': instance.codigo_postal,
                'provincia': instance.provincia,
            }
            data.update(translatable_fields)
        return data

    def validate(self, data):
        reservation = self.context.get('reservation')
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


class SelectedUpsellWriteSerializer(serializers.ModelSerializer):
    upsell_id = serializers.PrimaryKeyRelatedField(
        queryset=Upsell.objects.filter(is_active=True),
        source='upsell',
        write_only=True
    )
    quantity = serializers.IntegerField(min_value=1, default=1)
    class Meta:
        model = SelectedUpsell
        fields = ['upsell_id', 'quantity']


class SelectedUpsellReadSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='upsell.name', read_only=True)
    description = serializers.CharField(source='upsell.description', read_only=True)
    price_at_selection = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    image_url = serializers.SerializerMethodField()
    currency = serializers.CharField(source='upsell.currency', read_only=True)

    def get_image_url(self, obj):
        if obj.upsell.image:
            return self.context['request'].build_absolute_uri(obj.upsell.image.url)
        return None
    
    class Meta:
        model = SelectedUpsell
        fields = ['id', 'name', 'description', 'image_url', 'price_at_selection', 'currency', 'quantity']


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


class SingleGuestSerializer(serializers.ModelSerializer):
    municipality = MunicipalitySerializer(required=False)
    id_photo = serializers.ImageField(required=False)
    
    class Meta:
        model = Guest
        fields =['id', 'full_name', 'first_name', 'last_name2', 'last_name', 'is_lead',
            'document_type', 'document_number', 'support_number', 'is_minor',
            'nationality', 'date_of_birth', 'age', 'country_of_residence',
            'id_photo', 'municipality', 'gender', 'codigo_municipio', 
            'nombre_municipio', 'codigo_postal', 'provincia',
            'phone_number', 'email', 'street_address'
        ]

    def create(self, validated_data):
        municipality_data = validated_data.pop('municipality', None)
        if municipality_data:
            validated_data.update({
                'codigo_municipio': municipality_data.get('codigo_municipio'),
                'nombre_municipio': municipality_data.get('nombre_municipio'),
                'codigo_postal': municipality_data.get('codigo_postal'),
                'provincia': municipality_data.get('provincia')
            })
        return Guest.objects.create(**validated_data)
    
    def update(self, instance, validated_data):
        municipality_data = validated_data.pop('municipality', None)
        if municipality_data:
            validated_data.update({
                'codigo_municipio': municipality_data.get('codigo_municipio'),
                'nombre_municipio': municipality_data.get('nombre_municipio'),
                'codigo_postal': municipality_data.get('codigo_postal'),
                'provincia': municipality_data.get('provincia')
            })

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class CompleteCheckInSerializer(serializers.Serializer):
    guardian_relationships = GuardianRelationshipSerializer(many=True, required=False)
    selected_upsells_data = SelectedUpsellWriteSerializer(many=True, required=False)
    payment_token = serializers.CharField(write_only=True, required=False, allow_null=True, allow_blank=True)
    digital_signature = serializers.CharField(write_only=True, required=False)

    def validate(self, data):
        checkin = self.context.get('checkin')
        reservation = checkin.reservation

        guest_count = Guest.objects.filter(check_in=checkin).count()
        if guest_count != reservation.total_guests:
            raise serializers.ValidationError(
                f"Guest count mismatch. Expected {reservation.total_guests}, got {guest_count}."
            )

        if not Guest.objects.filter(check_in=checkin, is_lead=True).exists():
            raise serializers.ValidationError("At least one lead guest is required.")

        minors = Guest.objects.filter(check_in=checkin, is_minor=True)
        for minor in minors:
            if not GuardianRelationship.objects.filter(minor=minor).exists() and not any(
                rel.get('minor') == minor for rel in data.get('guardian_relationships', [])
            ):
                raise serializers.ValidationError(f"No guardian specified for minor {minor.full_name}.")

        return data


class CheckInSerializer(serializers.ModelSerializer):
    guests = GuestSerializer(many=True)
    selected_upsells_data = SelectedUpsellWriteSerializer(many=True, required=False, write_only=True, source="selected_upsells")
    selected_upsells = SelectedUpsellReadSerializer(many=True, read_only=True)
    payment_token = serializers.CharField(write_only=True, required=False, allow_null=True, allow_blank=True)
    guardian_relationships = GuardianRelationshipSerializer(many=True, required=False)
    digital_signature = serializers.CharField(write_only=True, required=False)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    reservation_code = serializers.CharField(source='reservation.reservation_code', read_only=True)
    wifi_network = serializers.SerializerMethodField()
    wifi_password = serializers.SerializerMethodField()

    def get_wifi_network(self, obj):
        if obj.status == CheckInStatus.COMPLETED:
            return obj.reservation.property_ref.wifi_network
        return None
    
    def get_wifi_password(self, obj):
        if obj.status == CheckInStatus.COMPLETED:
            return obj.reservation.property_ref.wifi_password
        return None

    class Meta:
        model = CheckIn
        fields = [
            'id', 'reservation', 'guests', 'guardian_relationships',
            'status', 'digital_signature', 'ip_address', 'initiated_at',
            'completed_at', 'selected_upsells', 'payment_token', 'wifi_network', 'wifi_password',
            'total_amount_charged'
        ]
        read_only_fields = ['status', 'ip_address', 'initiated_at', 'completed_at']

    def validate(self, data):
        guests = data.get('guests', [])
        reservation = self.context.get('reservation')
        if not reservation:
             raise serializers.ValidationError(_("Reservation context is missing."))
        if not any(g.get('is_lead', False) for g in guests):
            raise serializers.ValidationError(_("At least one lead guest is required"))
        if reservation and len(guests) != reservation.total_guests:
            raise serializers.ValidationError(
                _("Guest count mismatch. Expected %(expected)d, got %(actual)d") % {
                    'expected': reservation.total_guests,
                    'actual': len(guests)
                }
            )
        guest_objects = []
        for guest_data in guests:
            guest_serializer = GuestSerializer(data=guest_data, context=self.context)
            guest_serializer.is_valid(raise_exception=True)
            pass
        return data

    def create(self, validated_data):
        guests_data = validated_data.pop('guests')
        relationships_data = validated_data.pop('guardian_relationships', [])
        selected_upsells_data =validated_data.pop('selected_upsells_data', [])
        payment_token = validated_data.pop('payment_token', None)
        digital_signature = validated_data.pop('digital_signature', None)
        reservation = self.context.get('reservation')
        request = self.context.get('request')
        if not reservation:
            raise serializers.ValidationError(_("Reservation context is missing."))

        ip_address = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
        if ',' in ip_address:
            ip_address = ip_address.split(',')[0].strip()

        total_amount_due = reservation.outstanding_amount

        with transaction.atomic():
            check_in = CheckIn.objects.create(reservation=reservation,
                ip_address=ip_address,
                initiated_at=timezone.now(),
                digital_signature=digital_signature,
                status=CheckInStatus.PENDING
            )
            guests = []
            minors = []
            for guest_data in guests_data:
                municipality_data = guest_data.pop('municipality', None)
                if municipality_data:
                    guest_data['codigo_municipio'] = municipality_data.get('codigo_municipio')
                    guest_data['nombre_municipio'] = municipality_data.get('nombre_municipio')
                    guest_data['codigo_postal'] = municipality_data.get('codigo_postal')
                    guest_data['provincia'] = municipality_data.get('provincia')
                if 'last_name' in guest_data:
                    guest_data['last_name2'] = guest_data.pop('last_name')
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
            upsell_total_amount = Decimal('0.00')
            for upsell_data in selected_upsells_data:
                 upsell_id = upsell_data.get('upsell_id')
                 quantity = upsell_data.get('quantity', 1)
                 try:
                    upsell = Upsell.objects.get(id=upsell_id, is_active=True)
                    selected_upsell = SelectedUpsell.objects.create(
                        check_in=check_in,
                        upsell=upsell,
                        quantity=quantity,
                        price_at_selection=upsell.price
                    )
                    upsell_total_amount += selected_upsell.price_at_selection * selected_upsell.quantity
                 except Upsell.DoesNotExist:
                      raise serializers.ValidationError(
                           _("Selected upsell (ID: %(id)s) is invalid or inactive.") % {'id': upsell_id}
                      )
            total_amount_for_payment = total_amount_due + upsell_total_amount
            check_in.total_amount_charged = total_amount_for_payment
            check_in.save()
            payment_success = True
            payment_intent_id = None
            transaction_status = Transaction.STATUS_CHOICES[0][0]
            error_message = None

            if total_amount_for_payment > Decimal('0.00'):
                if not payment_token:
                    check_in.status = CheckInStatus.PAYMENT_PENDING
                    check_in.save()
                    return check_in
                try:
                   payment_result = PaymentService.process_payment(
                      amount=total_amount_for_payment,
                      currency=reservation.property_ref.currency,
                      token=payment_token,
                      description=f"Payment for reservation {reservation.reservation_code}",
                      landlord=reservation.property_ref.owner
                   )
                   payment_success = payment_result['success']
                   payment_intent_id = payment_result.get('payment_intent_id')
                   error_message = payment_result.get('error')
                   transaction_status = 'succeeded' if payment_success else 'failed'
                except Exception as e:
                   logger.error(f"Payment processing error for check-in {check_in.id}: {str(e)}")
                   payment_success = False
                   transaction_status = 'failed'
                   error_message = str(e)
                Transaction.objects.create(
                    check_in=check_in,
                    reservation=reservation,
                    guest_email=reservation.lead_guest_email,
                    landlord=reservation.property_ref.owner,
                    transaction_type='addon_payment' if upsell_total_amount > Decimal('0.00') else 'reservation_payment', # Determine type
                    amount=total_amount_for_payment,
                    currency=reservation.property_ref.currency,
                    status=transaction_status,
                    stripe_payment_intent_id=payment_intent_id,
                    error_message=error_message,
                    completed_at=timezone.now() if transaction_status in ['succeeded', 'failed'] else None
                )
                if not payment_success:
                    check_in.status = CheckInStatus.FAILED
                    check_in.save()
                    raise serializers.ValidationError(_("Payment processing failed. ") + (error_message or ""))
            check_in.status = CheckInStatus.COMPLETED
            check_in.completed_at = timezone.now()
            check_in.save()
            if total_amount_for_payment >= reservation.outstanding_amount:
                reservation.amount_paid += reservation.outstanding_amount
                reservation.is_fully_paid = True
            else:
                reservation.amount_paid += total_amount_for_payment
                reservation.is_fully_paid = False
            reservation.save()
            try:
                send_checkin_confirmation(check_in)
            except Exception as e:
                logger.error(f"Failed to send check-in confirmation email for {check_in.id}: {e}")
            if reservation.is_auto_submit:
                 try:
                    checkin_date_local = timezone.localtime(reservation.check_in_date).date()
                    scheduled_date = checkin_date_local - timezone.timedelta(days=1)
                    scheduled_time = timezone.make_aware(
                        timezone.datetime.combine(
                            scheduled_date,
                            timezone.datetime.strptime("21:00", "%H:%M").time()
                        ),
                        timezone.get_current_timezone()
                    )
                    if scheduled_time <= timezone.now():
                        scheduled_time = timezone.now() + timezone.timedelta(minutes=5)
                        logger.warning(f"Check-in date {checkin_date_local} is in the past or today. Scheduling police submission for check-in {check_in.id} for {scheduled_time}")
                    from checkin.tasks import submit_ses_report
                    submit_ses_report.apply_async(
                        args=[check_in.id],
                        eta=scheduled_time,
                    )
                    logger.info(f"Scheduled police submission for check-in {check_in.id} at {scheduled_time}")
                 except Exception as e:
                    logger.error(f"Failed to schedule police submission for check-in {check_in.id}: {e}")
                    send_police_submission_notification(check_in, False)
        return check_in

class ReservationSerializer(serializers.ModelSerializer):
    check_in_url = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)
    property_ref = serializers.PrimaryKeyRelatedField(read_only=True)  # ✅ Make it read-only
    owner = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = [
            'id', 'reservation_code', 'property_ref', 'check_in_date',
            'check_out_date', 'status', 'source', 'check_in_link',
            'lead_guest_name', 'lead_guest_email', 'lead_guest_phone', 'total_guests',
            'check_in_url', 'is_auto_submit', 'gdpr_compliant', 'is_active', 'owner',
            'reservation_amount', 'amount_paid', 'is_fully_paid', 'outstanding_amount'
        ]
        read_only_fields = [
            'reservation_code', 'check_in_link', 'status', 'property_ref',
            'is_active', 'amount_paid', 'is_fully_paid', 'outstanding_amount'
        ]
    
    def get_owner(self, obj):
        return obj.property_ref.owner.id if obj.property_ref and obj.property_ref.owner else None
    
    def get_check_in_url(self, obj):
        return f"{settings.FRONTEND_URL}/guest-checkin/{obj.reservation_code}"

    def validate_check_in_date(self, value):
        if value < timezone.now():
            raise ValidationError(_("Check-in date cannot be in the past"))
        return value
    
    def validate(self, data):
        check_in = data.get('check_in_date')
        check_out = data.get('check_out_date')
        
        if check_in and check_out and check_in >= check_out:
            raise ValidationError("Check-out date must be after check-in date")
        return data

class PoliceSubmissionLogSerializer(serializers.ModelSerializer):
    xml_preview = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    class Meta:
        model = PoliceSubmissionLog
        fields = [
            'id', 'check_in', 'status', 'submitted_at', 'xml_version',
            'xml_preview', 'retry_count', 'error_message', 'raw_response'
        ]
        read_only_fields = ['submitted_at', 'xml_version', 'status', 'status_display', 'retry_count', 'error_message', 'raw_response']

    def get_xml_preview(self, obj):
        return obj.raw_request[:500] + ' [...]' if obj.raw_request else ''

class PropertyICalSerializer(serializers.ModelSerializer):
    sync_status = serializers.SerializerMethodField()
    last_synced = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S", read_only=True)

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
        read_only_fields = ['last_cleanup']

class GuestbookEntrySerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    class Meta:
        model = GuestbookEntry
        fields = ['id', 'generation_date', 'download_url']
        read_only_fields = ['generation_date']

    def get_download_url(self, obj):
        request = self.context.get('request')
        if obj.generated_file and request:
             return request.build_absolute_uri(obj.generated_file.url)
        return None