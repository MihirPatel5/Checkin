from django.utils import timezone
from django.conf import settings
from django.db.models import Sum
from decimal import Decimal

from payment.models import (
    SubscriptionPlan,
    LandlordSubscription,
    Coupon,
    Transaction,
    StripeConnect,
    Upsell,
    UpsellPropertyAssigment
)
from payment.services.stripe_service import StripeService

class PaymentService:
    @staticmethod
    def calculate_subscription_price(property_type, billing_cycle, unit_count):
        """Calculate subscription price based on property type, billing cycle and unit count"""
        # logic for minimum unit 
        # if property_type in ['rooom', 'bed'] and unit_count <5:
        #     unit_count = 5
        try:
            plan = SubscriptionPlan.objects.get(
                property_type=property_type,
                billing_cycle=billing_cycle,
                is_active=True
            )
            return {
                'unit_price':plan.price_per_unit,
                'unit_count':unit_count,
                'total_price':plan.price_per_unit * unit_count
            }
        except SubscriptionPlan.DoesNotExist:
            if property_type == 'full_property':
                price = Decimal('4.0') if billing_cycle == 'yearly' else Decimal('5.0')
            elif property_type == 'room':
                price = Decimal('1.50') if billing_cycle == 'yearly' else Decimal('1.85')
            elif property_type == 'bed':
                price = Decimal('1.00') if billing_cycle == 'yearly' else Decimal('1.35')
            else:
                price = Decimal('5.0')
            
            return {
                'unit_price': price,
                'unit_count': unit_count,
                'total_price': price * unit_count
            }
    
    @staticmethod
    def update_subscription(subscription_id, unit_count=None, billing_cycle=None):
        """Update an existing subscription"""
        try:
            subscription = LandlordSubscription.objects.get(id=subscription_id)
            if unit_count is not None:
                if subscription.subscription_plan.property_type in ['room', 'bed'] and unit_count < 5:
                    unit_count = 5
                
                subscription.unit_count = unit_count
            if billing_cycle and billing_cycle != subscription.subscription_plan.billing_cycle:
                new_plan, created = SubscriptionPlan.objects.get_or_create(
                    property_type=subscription.subscription_plan.property_type,
                    billing_cycle=billing_cycle,
                    defaults={
                        'price_per_unit': PaymentService.calculate_subscription_price(
                            subscription.subscription_plan.property_type, 
                            billing_cycle, 
                            1
                        )['unit_price']
                    }
                )
                subscription.subscription_plan = new_plan
            subscription.save()
            if subscription.stripe_subscription_id:
                StripeService.update_subscription(subscription)
            
            return {
                'success': True,
                'subscription': subscription
            }
        except LandlordSubscription.DoesNotExist:
            return {
                'success': False,
                'message': 'Subscription not found'
            }
    
    @staticmethod
    def cancel_subscription(subscription_id):
        """Cancel a subscription"""
        try:
            subscription = LandlordSubscription.objects.get(id=subscription_id)
            if subscription.stripe_subscription_id and subscription.status != 'trialing':
                StripeService.cancel_subscription(subscription)
            else:
                subscription.status = 'canceled'
                subscription.end_date = timezone.now()
                subscription.save()
            
            return {
                'success': True,
                'subscription': subscription
            }
        except LandlordSubscription.DoesNotExist:
            return {
                'success': False,
                'message': 'Subscription not found'
            }
    
    @staticmethod
    def manual_assign_subscription(landlord, property_type, billing_cycle, unit_count, duration_months=12):
        """Manually assign a subscription to a landlord (for admin use)"""
        # Get or create subscription plan
        plan, created = SubscriptionPlan.objects.get_or_create(
            property_type=property_type,
            billing_cycle=billing_cycle,
            defaults={
                'price_per_unit': PaymentService.calculate_subscription_price(
                    property_type, billing_cycle, 1
                )['unit_price']
            }
        )
        end_date = timezone.now() + timezone.timedelta(days=30 * duration_months)
        subscription = LandlordSubscription.objects.create(
            landlord=landlord,
            subscription_plan=plan,
            unit_count=unit_count,
            status='active',
            end_date=end_date
        )
        
        return {
            'success': True,
            'subscription': subscription
        }
    
    @staticmethod
    def setup_stripe_connect(landlord, stripe_account_id, guest_pays_fee=True):
        """Set up Stripe Connect for a landlord"""
        connect, created = StripeConnect.objects.get_or_create(
            landlord=landlord,
            defaults={
                'stripe_account_id': stripe_account_id,
                'guest_pays_fee': guest_pays_fee
            }
        )
        if not created:
            connect.stripe_account_id = stripe_account_id
            connect.guest_pays_fee = guest_pays_fee
            connect.save()
        
        return connect
    
    @staticmethod
    def create_transaction(reservation, guest, landlord, amount, transaction_type='reservation'):
        """Create a transaction for guest payment"""
        try:
            stripe_connect = StripeConnect.objects.get(landlord=landlord)
            platform_fee = amount * Decimal('0.01')
            # Determine who pays the fee
            guest_paid_fee = stripe_connect.guest_pays_fee
            total_amount = amount + platform_fee if guest_paid_fee else amount
            landlord_amount = amount - (0 if guest_paid_fee else platform_fee)
            # Create transaction record
            transaction = Transaction.objects.create(
                reservation=reservation,
                guest=guest,
                landlord=landlord,
                transaction_type=transaction_type,
                amount=total_amount,
                platform_fee=platform_fee,
                landlord_amount=landlord_amount,
                guest_paid_fee=guest_paid_fee,
                stripe_payment_id='',
                status='pending'
            )
            
            return {
                'success': True,
                'transaction': transaction
            }
        except StripeConnect.DoesNotExist:
            return {
                'success': False,
                'message': 'Landlord has not set up payment processing'
            }
    
    @staticmethod
    def process_transaction_payment(transaction_id, payment_method_id):
        """Process payment for a transaction"""
        try:
            transaction = Transaction.objects.get(id=transaction_id)
            payment_intent = StripeService.create_payment_intent(
                transaction, 
                payment_method_id
            )
            if payment_intent.status == 'succeeded':
                transaction.status = 'completed'
                transaction.completed_at = timezone.now()
                transaction.save()
                
                return {
                    'success': True,
                    'transaction': transaction,
                    'requires_action': False
                }
            elif payment_intent.status == 'requires_action':
                return {
                    'success': True,
                    'requires_action': True,
                    'payment_intent_client_secret': payment_intent.client_secret
                }
            # Other status
            else:
                return {
                    'success': False,
                    'message': f"Payment failed with status: {payment_intent.status}"
                }
            
        except Transaction.DoesNotExist:
            return {
                'success': False,
                'message': 'Transaction not found'
            }
    
    @staticmethod
    def create_upsell(landlord, name, description, price, property_ids=None):
        """Create a reusable upsell item"""
        upsell = Upsell.objects.create(
            landlord=landlord,
            name=name,
            description=description,
            price=price
        )
        if property_ids:
            for property_id in property_ids:
                UpsellPropertyAssigment.objects.create(
                    upsell=upsell,
                    property_id=property_id
                )
        return upsell
    
    @staticmethod
    def assign_upsell_to_properties(upsell_id, property_ids):
        """Assign an upsell to multiple properties"""
        upsell = Upsell.objects.get(id=upsell_id)
        UpsellPropertyAssigment.objects.filter(upsell=upsell).delete()
        assignments = []
        for property_id in property_ids:
            assignment = UpsellPropertyAssigment(
                upsell=upsell,
                property_id=property_id
            )
            assignments.append(assignment)
        UpsellPropertyAssigment.objects.bulk_create(assignments)
        return len(assignments)
    
    @staticmethod
    def get_property_upsells(property_id):
        """Get all upsells available for a property"""
        assignments = UpsellPropertyAssigment.objects.filter(
            property_id=property_id,
            upsell__is_active=True
        ).select_related('upsell')
        
        return [assignment.upsell for assignment in assignments]
    
    @staticmethod
    def get_landlord_dashboard_stats(landlord):
        """Get payment statistics for landlord dashboard"""
        try:
            subscription = LandlordSubscription.objects.filter(
                landlord=landlord,
                status__in=['active', 'trialing']
            ).latest('start_date')
            
            subscription_info = {
                'status': subscription.status,
                'plan': subscription.subscription_plan.get_property_type_display(),
                'billing_cycle': subscription.subscription_plan.billing_cycle,
                'unit_count': subscription.unit_count,
                'price_per_unit': subscription.subscription_plan.price_per_unit,
                'total_price': subscription.total_price,
                'start_date': subscription.start_date,
                'end_date': subscription.end_date,
                'trial_end_date': subscription.trial_end_date
            }
        
        except LandlordSubscription.DoesNotExist:
            subscription_info = None
        transactions = Transaction.objects.filter(landlord=landlord)
        
        total_earnings = transactions.filter(status='completed').aggregate(
            sum=Sum('landlord_amount')
        )['sum'] or 0
        pending_earnings = transactions.filter(status='pending').aggregate(
            sum=Sum('landlord_amount')
        )['sum'] or 0
        
        transaction_count = transactions.filter(status='completed').count()
        return {
            'subscription': subscription_info,
            'total_earnings': total_earnings,
            'pending_earnings': pending_earnings,
            'transaction_count': transaction_count
        }
    
    @staticmethod
    def apply_coupon(price, coupon_code):
        """Apply coupon to subscription price"""
        try:
            coupon = Coupon.objects.get(code=coupon_code)
            
            if not coupon.is_valid:
                return {
                    'success': False,
                    'message': 'Coupon is not valid or has expired',
                    'original_price': price,
                    'discounted_price': price
                }
            
            if coupon.discount_type == 'percentage':
                discount = price * (coupon.discount_value / 100)
            else:
                discount = coupon.discount_value
            discount = min(discount, price)
            discounted_price = price - discount
            
            return {
                'success': True,
                'message': 'Coupon applied successfully',
                'original_price': price,
                'discounted_price': discounted_price,
                'coupon': coupon
            }
        except Coupon.DoesNotExist:
            return {
                'success': False,
                'message': 'Invalid coupon code',
                'original_price': price,
                'discounted_price': price
            }
    
    @staticmethod
    def create_subscription(landlord, property_type, billing_cycle, unit_count, payment_method_id, coupon_code=None):
        """Create a new subscription for a landlord with Stripe integration"""
        if property_type in ['room', 'bed'] and unit_count < 5:
            unit_count = 5
        plan, created = SubscriptionPlan.objects.get_or_create(
            property_type=property_type,
            billing_cycle=billing_cycle,
            defaults={
                'price_per_unit': PaymentService.calculate_subscription_price(
                    property_type, billing_cycle, 1
                )['unit_price']
            }
        )
        price_info = PaymentService.calculate_subscription_price(property_type, billing_cycle, unit_count)
        total_price = price_info['total_price']
        coupon = None
        
        if coupon_code:
            coupon_result = PaymentService.apply_coupon(total_price, coupon_code)
            if coupon_result['success']:
                total_price = coupon_result['discounted_price']
                coupon = coupon_result['coupon']
        subscription = LandlordSubscription.objects.create(
            landlord=landlord,
            subscription_plan=plan,
            unit_count=unit_count,
            status='trialing',
            coupon=coupon
        )
        try:
            stripe_subscription = StripeService.create_subscription(
                subscription, 
                payment_method_id
            )
            return {
                'success': True,
                'subscription': subscription,
                'stripe_subscription_id': stripe_subscription.id
            }
        except Exception as e:
            subscription.delete()
            return {
                'success': False,
                'message': str(e)
            }