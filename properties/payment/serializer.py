from rest_framework import serializers
from payment.models import (
    SubscriptionPlan,
    LandlordSubscription,
    Coupon,
    Transaction,
    StripeConnect,
    Upsell
)
from django.conf import settings

class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = ['id', 'property_type', 'billing_cycle', 'price_per_unit', 'is_active', 'min_units']
    
class LandlordSubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(source='subscription_plan', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = LandlordSubscription
        fields = [
            'id', 'plan', 'unit_count', 'status', 'status_display',
            'start_date', 'end_date', 'stripe_subscription_id', 'trial_end_date', 'is_active', 'total_price'
        ]
        read_only_fields = [
            'status', 'status_display', 'start_date', 'end_date', 'stripe_subscription_id', 'trial_end_date', 'is_active', 'total_price'
        ]

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
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Upsell
        fields = ['id', 'name', 'description', 'price', 'is_active', 'currency', 'charge_type', 'image', 'image_url', 'landlord']
        read_only_fields = ['image_url', 'landlord']

    def get_image_url(self, obj):
        request = self.context.get('request')
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None