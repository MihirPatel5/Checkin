from django.utils import timezone
from rest_framework import serializers
from payment.models import (
    SubscriptionInvoice,
    SubscriptionPlan,
    LandlordSubscription,
    Coupon,
    Transaction,
    StripeConnect,
    Upsell
)
from .services.stripe_service import StripeService
from django.conf import settings

class SubscriptionPlanSerializer(serializers.ModelSerializer):
    discount_tiers = serializers.JSONField(required=False)
    commission_rate = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, allow_null=True, required=False)
    class Meta:
        model = SubscriptionPlan
        fields = ['id', 'full_property', 'room', 'bed', 'custom_branding', 'smart_lock_full_property',
                'smart_lock_room', 'commission_rate', 'billing_cycle', 'is_active',  'stripe_price_id',
                'min_units_full_property', 'min_units_room', 'min_units_bed', 'currency_type', 'discount_tiers']
        read_only_fields = ['stripe_price_id']

    def validate(self, attrs):
        unit_fields = ['full_property', 'room', 'bed', 'custom_branding', 'smart_lock_full_property', 'smart_lock_room']
        if not any(attrs.get(field) for field in unit_fields):
            raise serializers.ValidationError("At least one unit rate must be provided.")
        return attrs
    
class LandlordSubscriptionSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_active = serializers.SerializerMethodField()
    payment_method = serializers.CharField(write_only=True, required=True)
    subscription_details = serializers.DictField(required=False)
    days_remaining =serializers.SerializerMethodField()
    period_display = serializers.SerializerMethodField()

    class Meta:
        model = LandlordSubscription
        fields = [
            'id', 'full_property_count', 'room_count', 'bed_count', 'billing_cycle',
            'custom_branding_full_property_count', 'custom_branding_room_count', 'custom_branding_bed_count',
            'smart_lock_full_property_count', 'smart_lock_room_count', 'smart_lock_bed_count',
            'status', 'status_display', 'start_date', 'end_date', 'stripe_subscription_id',
            'trial_end_date', 'is_active', 'total_price', 'payment_method', 'subscription_details',
            'days_remaining', 'period_display'
        ]
        read_only_fields = [
            'status', 'status_display', 'start_date', 'end_date',
            'stripe_subscription_id', 'trial_end_date', 'is_active',
        ]

    def get_is_active(self, obj):
        return obj.is_active

    def get_days_remaining(self, obj):
        """Calculate days remaining in current period"""
        if obj.end_date:
            remaining = (obj.end_date - timezone.now()).days
            return max(0, remaining)
        return 0
    
    def get_final_price(self, obj):
        return obj.total_price

    def get_period_display(self, obj):
        """Display current period information"""
        if obj.start_date and obj.end_date:
            return f"{obj.start_date.strftime('%b %d, %Y')} - {obj.end_date.strftime('%b %d, %Y')}"
        return None

    def validate(self, data):
        room_count = data.get('room_count', 0)
        bed_count = data.get('bed_count', 0)
        if room_count > 0 and room_count < 10:
            raise serializers.ValidationError({"room_count": "Must be at least 10 if greater than 0."})
        if bed_count > 0 and bed_count < 10:
            raise serializers.ValidationError({"bed_count": "Must be at least 10 if greater than 0."})

        full_property_count = data.get('full_property_count', 0)
        if data.get('custom_branding_full_property_count', 0) > full_property_count:
            raise serializers.ValidationError({"custom_branding_full_property_count": "Cannot exceed full property count."})
        if data.get('smart_lock_full_property_count', 0) > full_property_count:
            raise serializers.ValidationError({"smart_lock_full_property_count": "Cannot exceed full property count."})

        if data.get('custom_branding_room_count', 0) > room_count:
            raise serializers.ValidationError({"custom_branding_room_count": "Cannot exceed room count."})
        if data.get('smart_lock_room_count', 0) > room_count:
            raise serializers.ValidationError({"smart_lock_room_count": "Cannot exceed room count."})

        if data.get('custom_branding_bed_count', 0) > bed_count:
            raise serializers.ValidationError({"custom_branding_bed_count": "Cannot exceed bed count."})
        if data.get('smart_lock_bed_count', 0) > bed_count:
            raise serializers.ValidationError({"smart_lock_bed_count": "Cannot exceed bed count."})

        if data.get('billing_cycle') not in ['monthly', 'yearly']:
            raise serializers.ValidationError({"billing_cycle": "Must be either 'monthly' or 'yearly'."})

        if not data.get('total_price') or data.get('total_price') <= 0:
            raise serializers.ValidationError({"total_price": "Total price must be provided and greater than 0."})

        return data


class CouponSerializer(serializers.ModelSerializer):
    is_valid = serializers.BooleanField(read_only=True)

    class Meta:
        model = Coupon
        fields = ['code', 'discount_type', 'discount_value', 'valid_from', 'valid_until', 'max_uses', 'current_uses', 'is_valid']
        read_only_fields = ['current_uses', 'is_valid']


class TransactionSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    reservation_code = serializers.CharField(source='reservation.reservation_code', read_only=True, allow_null=True)
    check_in_id = serializers.UUIDField(source='check_in.id', read_only=True, allow_null=True)

    class Meta:
        model = Transaction
        fields = [
            'id', 'reservation', 'check_in', 'guest_email', 'landlord',
            'transaction_type', 'description', 'amount', 'currency',
            'platform_fee', 'stripe_processing_fee', 'landlord_amount',
            'guest_paid_platform_fee', 'stripe_payment_intent_id', 'stripe_charge_id',
            'status', 'status_display', 'error_message', 'created_at',
            'completed_at', 'refunded_at', 'refund_amount',
            'reservation_code', 'check_in_id'
        ]
        read_only_fields = [f for f in fields if f not in ['description', 'guest_paid_platform_fee']]

class StripeConnectSerializer(serializers.ModelSerializer):
    class Meta:
        model = StripeConnect
        fields = ['stripe_account_id', 'guest_pays_fee', 'connected_at', 'is_active']
        read_only_fields = ['connected_at', 'is_active']

class UpsellSerializer(serializers.ModelSerializer):
    PREDEFINED_CHOICES = [
        ('custom_branding', 'Custom Branding'),
        ('smart_lock', 'Smart Lock'),
        ('other', 'Other'),
    ]
    name = serializers.CharField(max_length=100)
    # predefined_name = serializers.ChoiceField(choices=PREDEFINED_CHOICES, required=True)
    # custom_name = serializers.CharField(max_length=100, required=False)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Upsell #'name',
        fields = ['id',  'name', 'description', 'price', 'is_active', 'currency', 'charge_type', 'image', 'image_url', 'landlord']
        read_only_fields = ['image_url', 'landlord']

    def validate_name(self, value):
        """
        If the incoming `name` matches one of our choice-keys,
        replace it with the human-friendly label.
        Otherwise leave it untouched (a true custom name).
        """
        mapping = dict(self.PREDEFINED_CHOICES)
        # value might be e.g. "smart_lock" → we store "Smart Lock"
        if value in mapping:
            return mapping[value]
        # value is something else → we treat it as a custom name
        return value

    def get_image_url(self, obj):
        request = self.context.get('request', None)
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


class SubscriptionInvoiceSerializer(serializers.ModelSerializer):
    pdf_url = serializers.URLField(read_only=True)
    hosted_invoice_url = serializers.URLField(read_only=True)
    
    class Meta:
        model = SubscriptionInvoice
        fields = [
            'id', 'subscription', 'stripe_invoice_id', 'amount', 'status', 
            'created_at', 'paid_at', 'pdf_url', 'hosted_invoice_url'
        ]