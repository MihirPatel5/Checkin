from django.db import models
from django.utils import timezone
from django.conf import settings
from datetime import timedelta

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
    property_type = models.CharField(max_length=20, choices=PROPERTY_TYPE_CHOICES)
    billing_cycle = models.CharField(max_length=10, choices=BILLING_CYCLE_CHOICES)
    price_per_unit = models.DecimalField(max_digits=6, decimal_places=2)
    min_units = models.IntegerField(default=1)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.get_property_type_display()} - {self.get_billing_cycle_display()} - €{self.price_per_unit}/unit"


class LandlordSubscription(models.Model):
    """Tracks the subscription for each landlord"""
    STATUS_CHOICE = [
        ('active', 'Active'),
        ('past_due', 'Past Due'),
        ('canceled', 'Canceled'),
        ('trailing', 'Trailing'),
        ('suspended', 'Suspended')
    ]
    landlord = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    subscription_plan = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE)
    unit_count = models.PositiveIntegerField(default=1)
    stripe_subscription_id = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(choices=STATUS_CHOICE, default='trailing')
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)
    trail_end_date = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.pk and self.status == 'trialing' and not self.trial_end_date:
            self.trial_end_date = timezone.now() + timedelta(days=15)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.landlord.username} - {self.subscription_plan} - {self.status}"
    
    @property
    def is_active(self):
        return self.status in ['active', 'trailing']
    
    @property
    def total_price(self):
        return self.subscription_plan.price_per_unit * self.unit_count


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
    valid_until = models.PositiveIntegerField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True,blank=True)
    current_uses = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"{self.code} - {self.discount_type} - {self.discount_value}"
    
    @property
    def is_valid(self):
        now = timezone.now()
        max_uses_valid =self.max_uses is None or self.current_uses < self.max_uses
        date_valid = now >= self.valid_from and (self.valid_until is None or now <= self.valid_until)
        return max_uses_valid and date_valid


class SubscriptionInvoice(models.Model):
    """Invoices for subscription payments"""
    STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('pending', 'Pending'),
        ('failed', 'Failed'),
    ]
    subscription = models.ForeignKey(LandlordSubscription, on_delete=models.CASCADE)
    stripe_invoice_id = models.CharField(max_length=100, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Invoice #{self.id} - {self.subscription.landlord.username} - {self.status}"


class StripeConnect(models.Model):
    """Stores Stripe Connect account details for landlords"""
    landlord = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    stripe_account_id = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    guest_pays_fee = models.BooleanField(default=True)
    connected_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.landlord.username} - {self.stripe_account_id}"


class Transaction(models.Model):
    """Records all payments mades by guest"""
    TRANSACTION_TYPE_CHOICES =[
        ('reservation', 'Reservation'),
        ('upsell', 'Upsell'),
        ('deposit', 'Deposit'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]
    reservation = models.ForeignKey("checkin.Reservation", on_delete=models.CASCADE, null=True, blank=True)
    guest = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="guest_transactions")
    landlord = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="landlord_transactions")
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2)
    stripe_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    landlord_amount = models.DecimalField(max_digits=10, decimal_places=2)
    guest_paid_fee = models.BooleanField(default=False)
    stripe_payment_id = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.guest.username} to {self.landlord.username} - {self.amount} - {self.status}"


class Upsell(models.Model):
    """Reusable addtional services that landlord can offer to guests"""
    landlord = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.name} - {self.price}"


class UpsellPropertyAssigment(models.Model):
    """Links upsells to specific properties"""
    upsell =  models.ForeignKey(Upsell, on_delete=models.CASCADE)
    property_ref = models.ForeignKey('property.Property', on_delete=models.CASCADE)

    class Meta:
        unique_together = ('upsell', 'property_ref')
    
    def __str__(self):
        return f"{self.upsell.name} - {self.property_ref.name}"


class PaymentFailureLog(models.Model):
    """Tracks payment failure attempts for retry logic"""
    subscription = models.ForeignKey(LandlordSubscription, on_delete=models.CASCADE)
    stripe_error_code = models.CharField(max_length=100, null=True, blank=True)
    stripe_error_message = models.TextField(null=True, blank=True)
    attempt_number = models.PositiveIntegerField(default=1)
    next_retry_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    
    def __str__(self):
        return f"{self.subscription.landlord.username} - Attempt {self.attempt_number} - {self.created_at}"