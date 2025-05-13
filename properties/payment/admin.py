from django.contrib import admin

from .models import (
    LandlordSubscription,
    PaymentFailureLog,
    SubscriptionPlan,
    Coupon,
    SubscriptionInvoice,
    StripeConnect,
    Transaction,
    Upsell,
    UpsellPropertyAssigment,
)

admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('property_type', 'billing_cycle', 'price_per_unit', 'is_active')
    list_filter = ('property_type', 'billing_cycle', 'is_active')
    search_fields = ('property_type', 'billing_cycle')
    list_editable = ('price_per_unit', 'is_active')

@admin.register(LandlordSubscription)
class LandlordSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('landlord', 'subscription_plan', 'unit_count', 'status', 'start_date', 'end_date', 'trail_end_date')
    list_filter = ('status', 'subscription_plan__property_type', 'subscription_plan__billing_cycle')
    search_fields = ('landlord__username', 'landlord__email', 'stripe_subscription_id')
    raw_id_fields = ('landlord',)
    date_hierarchy = 'start_date'
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(landlord=request.user)

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount_type', 'discount_value', 'valid_from', 'valid_until', 'max_uses', 'current_uses', 'is_valid')
    list_filter = ('discount_type', 'valid_from', 'valid_until')
    search_fields = ('code',)
    raw_id_fields = ('created_by',)
    readonly_fields = ('current_uses',)
    
    def is_valid(self, obj):
        return obj.is_valid
    is_valid.boolean = True

@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'subscription', 'amount', 'status', 'created_at', 'paid_at')
    list_filter = ('status', 'created_at')
    search_fields = ('subscription__landlord__username', 'subscription__landlord__email', 'stripe_invoice_id')
    raw_id_fields = ('subscription', 'coupon')

@admin.register(StripeConnect)
class StripeConnectAdmin(admin.ModelAdmin):
    list_display = ('landlord', 'stripe_account_id', 'is_active', 'guest_pays_fee', 'connected_at')
    list_filter = ('is_active', 'guest_pays_fee', 'connected_at')
    search_fields = ('landlord_username', 'landlord_email', 'stripe_account_id')
    raw_id_fields = ('landlord',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(landlord=request.user)

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'guest', 'landlord', 'reservation', 'transaction_type', 'amount', 'platform_fee', 'status', 'created_at')
    list_filter = ('transaction_type', 'status', 'created_at', 'guest_paid_fee')
    search_fields = ('guest_username', 'guest_email', 'landlord_email', 'stripe_payment_id')
    raw_id_fields =  ('reservation', 'guest', 'landlord')
    readonly_fields = ('platform_fee', 'stripe_fee', 'landlord_amount')

    def get_queryset(self, request):
        qs = super.get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(landlord=request.user)
    
    def has_change_permission(self, request, obj = None):
        if obj and not request.user.is_superuser:
            return False
        return super().has_change_permission(request, obj)

@admin.register(Upsell)
class UpsellAdmin(admin.ModelAdmin):
    list_display = ('name', 'landlord', 'is_active', 'price', 'property_count')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'description', 'landlord__username', 'landlord__email')
    raw_id_fields = ('landlord',)

    def property_count(self, obj):
        return UpsellPropertyAssigment.objects.filter(upsell=obj).count()
    property_count.short_description = 'properties'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(landlord=request.user)

@admin.register(UpsellPropertyAssigment)
class UpsellPropertyAssignmentAdmin(admin.ModelAdmin):
    list_display = ('upsell', 'property_ref')
    list_filter = ('upsell', 'property_ref')
    search_fields = ('upsell_name', 'property_ref__name')
    raw_id_fields = ('upsell', 'property_ref')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(upsell__landlord=request.user)

@admin.register(PaymentFailureLog)
class PaymentFailureLogAdmin(admin.ModelAdmin):
    list_display = ('subscription','attempt_number', 'stripe_error_code', 'next_retry_date', 'created_at')
    list_filter = ('attempt_number', 'created_at')
    search_fields = ('subscription__landlord__username', 'subscription__landlord__email', 'stripe_error_code')
    raw_id_fields = ('subscription',)
    readonly_fields = ('subscription', 'stripe_error_code', 'stripe_error_message', 'attempt_number', 'next_retry_date', 'created_at')

    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj = None):
        return False