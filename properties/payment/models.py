from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
from checkin.models import Reservation, CheckIn, Guest

PROPERTY_TYPE_CHOICES = [
        ('full_property', 'Full Property'),
        ('room', 'Room'),
        ('bed', 'Bed'),
    ]

BILLING_CYCLE_CHOICES = [
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    ]

class SubscriptionPlan(models.Model):
    """Base subscription plan configuration"""
    # property_type = models.CharField(max_length=20, choices=PROPERTY_TYPE_CHOICES)
    billing_cycle = models.CharField(max_length=10, choices=BILLING_CYCLE_CHOICES)
    full_property = models.DecimalField(max_digits=10, null=True, blank=True, decimal_places=2)
    room = models.DecimalField(max_digits=10, null=True, blank=True, decimal_places=2)
    bed = models.DecimalField(max_digits=10, null=True, blank=True, decimal_places=2)
    custom_branding = models.DecimalField(max_digits=10, null=True, blank=True, decimal_places=2)
    smart_lock_full_property = models.DecimalField(max_digits=10, null=True, blank=True, decimal_places=2)
    smart_lock_room = models.DecimalField(max_digits=10, null=True, blank=True, decimal_places=2)
    commission_rate = models.DecimalField(max_digits=10, blank=True, null=True, decimal_places=2)
    min_units_full_property = models.IntegerField(default=1)
    min_units_room = models.IntegerField(default=5)
    min_units_bed = models.IntegerField(default=5)
    currency_type = models.CharField(max_length=3, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    discount_tiers = models.JSONField(
        default=list,
        blank=True,
        help_text="e.g. [{\"minProperties\":10,\"discount\":0.05,\"label\":\"5% off\"}, …]"
    )
    stripe_price_id = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"- €{self.billing_cycle}"


class Coupon(models.Model):
    """Discount coupons for subscriptions"""
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'),
        ('fixed', 'Fixed Amount')
    ]
    code = models.CharField(max_length=20, unique=True)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=6, decimal_places=2)
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True,blank=True)
    current_uses = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    stripe_coupon_id = models.CharField(max_length=100, blank=True, null=True) # if needed in stripe 

    def __str__(self):
        return f"{self.code} - {self.discount_type} - {self.discount_value}"
    
    @property
    def is_valid(self):
        now = timezone.now()
        max_uses_valid =self.max_uses is None or self.current_uses < self.max_uses
        date_valid = now >= self.valid_from and (self.valid_until is None or now <= self.valid_until)
        return max_uses_valid and date_valid


class LandlordSubscription(models.Model):
    """Tracks the subscription for each landlord"""
    STATUS_CHOICE = [
        ('active', 'Active'),
        ('past_due', 'Past Due'),
        ('canceled', 'Canceled'),
        ('trialing', 'Trialing'),  # Fixed typo from 'Trailing' to 'Trialing'
        ('suspended', 'Suspended')
    ]
    landlord = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True)
    unit_count = models.PositiveBigIntegerField(default=1) # remove on finalize
    full_property_count = models.PositiveIntegerField(default=0)
    room_count = models.PositiveIntegerField(default=0)
    bed_count = models.PositiveIntegerField(default=0)
    custom_branding_full_property_count = models.PositiveIntegerField(default=0)
    custom_branding_room_count = models.PositiveIntegerField(default=0)
    custom_branding_bed_count = models.PositiveIntegerField(default=0)
    smart_lock_full_property_count = models.PositiveIntegerField(default=0)
    smart_lock_room_count = models.PositiveIntegerField(default=0)
    smart_lock_bed_count = models.PositiveIntegerField(default=0)
    billing_cycle = models.CharField(choices=BILLING_CYCLE_CHOICES, default='monthly', null=True, blank=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    subscription_details = models.JSONField(default=dict, blank=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True, null=True)
    stripe_customer_id = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICE, default='trialing')
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)
    trial_end_date = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.pk and self.status == 'trialing' and not self.trial_end_date:
            self.trial_end_date = timezone.now() + timedelta(days=15)

        if not self.end_date and self.start_date and self.billing_cycle:
            if self.billing_cycle == 'monthly':
                self.end_date = self.start_date + timedelta(days=30)
            elif self.billing_cycle == 'yearly':
                self.end_date = self.start_date + timedelta(days=365)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.landlord.username} - {self.status} ({self.billing_cycle})"
    
    @property
    def is_active(self):
        return self.status in ['active', 'trialing']

    @property
    def is_expired(self):
        """Check if subscription has expired"""
        if self.end_date:
            return timezone.now() > self.end_date
        return False


class SubscriptionInvoice(models.Model):
    """Invoices for subscription payments"""
    STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('pending', 'Pending'),
        ('failed', 'Failed'),
    ]
    subscription = models.ForeignKey(LandlordSubscription, on_delete=models.CASCADE, related_name="invoices")
    stripe_invoice_id = models.CharField(max_length=100, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    pdf_url = models.URLField(blank=True, null=True)
    hosted_invoice_url = models.URLField(blank=True, null=True)
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Invoice #{self.id} - {self.subscription.landlord.username} - {self.status}"


class StripeConnect(models.Model):
    """Stores Stripe Connect account details for landlords"""
    landlord = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="stripe_connect_account")
    stripe_account_id = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    guest_pays_fee = models.BooleanField(default=True)
    connected_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.landlord.username} - {self.stripe_account_id}"


class Transaction(models.Model):
    """Records all payments made by guests during check-in or for other services"""
    TRANSACTION_TYPE_CHOICES = [
        ('reservation_payment', 'Reservation Payment'),
        ('addon_payment', 'Add-on Payment'),
        ('security_deposit', 'Security Deposit'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('succeeded', 'Succeeded'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
        ('disputed', 'Disputed'),
    ]
    reservation = models.ForeignKey("checkin.Reservation", on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_transactions")
    check_in = models.ForeignKey("checkin.CheckIn", on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_transactions")
    guest_email = models.EmailField(null=True, blank=True, help_text="Email of the guest making the payment")
    guest_user = models.ForeignKey(Guest, on_delete=models.SET_NULL, null=True, blank=True, related_name="guest_payment_transactions")
    landlord = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="landlord_received_transactions")
    transaction_type = models.CharField(max_length=30, choices=TRANSACTION_TYPE_CHOICES)
    description = models.CharField(max_length=255, blank=True, help_text="Brief description of the transaction, e.g., 'Parking Fee'")
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Total amount of the transaction")
    currency = models.CharField(max_length=3, default='EUR', help_text="Currency code (e.g., EUR, USD)")
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    stripe_processing_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    landlord_amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Amount credited to landlord after fees")
    guest_paid_platform_fee = models.BooleanField(default=False)
    stripe_payment_intent_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    stripe_charge_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True, null=True, help_text="Error message if transaction failed")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        guest_identifier = self.guest_email or (self.guest_user.username if self.guest_user else "Unknown Guest")
        return f"Txn for {self.reservation.reservation_code if self.reservation else 'N/A'} by {guest_identifier} - {self.amount} {self.currency} - {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if not self.pk and self.status == 'trialing' and not self.trial_end_date:
            self.trial_end_date = timezone.now() + timedelta(days=15)
        if self.status in ['active', 'trialing'] and not self.end_date:
            if self.billing_cycle == 'monthly':
                self.end_date = self.start_date + timedelta(days=30)
            elif self.billing_cycle == 'yearly':
                self.end_date = self.start_date + timedelta(days=365)
        if self.status == 'canceled' and not self.end_date:
            self.end_date = timezone.now()
        super().save(*args, **kwargs)


class Upsell(models.Model):
    """Reusable additional services that landlord can offer to guests"""
    landlord = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="upsells")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='EUR')
    charge_type = models.CharField(max_length=20, choices=[('per_guest', 'Per Guest'), ('one_time', 'One Time')], default='one_time')
    image = models.ImageField(upload_to='upsell_images/', null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.price} {self.currency}"


class UpsellPropertyAssignment(models.Model):
    """Links upsells to specific properties, making them available for those properties"""
    upsell = models.ForeignKey(Upsell, on_delete=models.CASCADE, related_name="property_assignments")
    property_ref = models.ForeignKey('property.Property', on_delete=models.CASCADE, related_name="available_upsells") 

    class Meta:
        unique_together = ('upsell', 'property_ref')
        verbose_name = "Upsell Property Assignment"
        verbose_name_plural = "Upsell Property Assignments"
    
    def __str__(self):
        return f"{self.upsell.name} available at {self.property_ref.name}"


class PaymentFailureLog(models.Model):
    """Tracks payment failure attempts for retry logic (primarily for subscriptions)"""
    subscription = models.ForeignKey(LandlordSubscription, on_delete=models.CASCADE, null=True, blank=True, related_name="payment_failures")
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, null=True, blank=True, related_name="payment_failures")
    stripe_error_code = models.CharField(max_length=100, null=True, blank=True)
    stripe_error_message = models.TextField(null=True, blank=True)
    attempt_number = models.PositiveIntegerField(default=1)
    next_retry_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        target = self.subscription.landlord.username if self.subscription else (self.transaction.id if self.transaction else "N/A")
        return f"Payment Failure for {target} - Attempt {self.attempt_number} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"