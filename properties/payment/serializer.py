from rest_framework import serializers
from payment.models import (
    SubscriptionPlan,
    LandlordSubscription,
    Coupon,
    Transaction,
    StripeConnect,
    Upsell
)

class SubscriptionPalnSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = ['id', 'property_type', 'billing_cycle', 'price_per_unit', 'is_active']
    
class LandlordSubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPalnSerializer(source='subscription_plan', read_only=True)

    class Meta:
        model = LandlordSubscription
        fields = ['id', 'plan', 'unit_count', 'status', 'start_date', 'end_date', 'stripe_subscription_id']
        read_only_fields = ['status', 'start_date', 'end_date', 'stripe_subscription_id']

class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = ['code', 'discount_type', 'discount_value', 'valid_from', 'valid_until', 'max_uses']

class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = ['id', 'amount', 'status', 'created_at', 'completed_at', 'transaction_type']
        read_only_fields = ['status', 'created_at', 'completed_at']

class StripeConnectSerializer(serializers.ModelSerializer):
    class Meta:
        model = StripeConnect
        fields = ['stripe_account_id', 'guest_pays_fee', 'connected_at']
        read_only_fields = ['connected_at']

class UpsellSerializer(serializers.ModelSerializer):
    class Meta:
        model = Upsell
        fields = ['id', 'name', 'description', 'price', 'is_active']
