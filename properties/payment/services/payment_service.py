from datetime import timedelta
from payment.utils import send_subscription_invoice_email
import math, logging, stripe
from typing import Optional
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.db.models import Sum
from decimal import Decimal, ROUND_HALF_UP
from stripe.error import StripeError
from payment.models import (
    SubscriptionInvoice,
    SubscriptionPlan,
    LandlordSubscription,
    Coupon,
    Transaction,
    StripeConnect,
    Upsell,
    UpsellPropertyAssignment
)
from payment.services.stripe_service import StripeService

logger = logging.getLogger(__name__)

class PaymentService:
    PLATFORM_FEE_RATE = Decimal("0.012")
    @staticmethod
    def calculate_subscription_price(
        full_property_count, room_count, bed_count, custom_branding_full_property_count, 
        custom_branding_room_count, custom_branding_bed_count, smart_lock_full_property_count, 
        smart_lock_room_count, smart_lock_bed_count
        ):
        """Calculate subscription price based on property type counts and add-ons"""
        plans = SubscriptionPlan.objects.filter(is_active=True, billing_cycle='monthly')
        base_price = Decimal('0.00')

        if room_count > 0:
            room_plan = plans.filter(property_type='room').first()
            if not room_plan:
                return Decimal('0.00')
            base_price += room_count * room_plan.price_per_unit

        add_on_price = (
            custom_branding_full_property_count * Decimal('1.00') +
            custom_branding_room_count * Decimal('1.00') +
            custom_branding_bed_count * Decimal('1.00') +
            smart_lock_full_property_count * Decimal('3.50') +
            smart_lock_room_count * Decimal('2.00') +
            smart_lock_bed_count * Decimal('2.00')
        )

        total_units = room_count
        discount_percentage = 0
        if total_units >= 10:
            discount_percentage = 5 * math.floor((total_units - 10) / 10 + 1)

        total_price_before_discount = base_price + add_on_price
        discount_amount = total_price_before_discount * (Decimal(discount_percentage) / 100)
        final_price = total_price_before_discount - discount_amount

        return final_price
    
    @staticmethod
    def update_subscription(
        subscription: LandlordSubscription,
        full_property_count=None,
        room_count=None,
        bed_count=None,
        custom_branding_full_property_count=None,
        custom_branding_room_count=None,
        custom_branding_bed_count=None,
        smart_lock_full_property_count=None,
        smart_lock_room_count=None,
        smart_lock_bed_count=None,
        billing_cycle=None
    ):
        """
        Update an existing subscription’s breakdown (property + add-on counts), 
        possibly switch billing cycle, then push changes to Stripe in one shot.
        """

        if room_count is not None and room_count > 0 and room_count < 10:
            return {'success': False, 'message': 'Room count must be at least 10 if greater than 0.'}
        if bed_count is not None and bed_count > 0 and bed_count < 10:
            return {'success': False, 'message': 'Bed count must be at least 10 if greater than 0.'}

        if (custom_branding_full_property_count or 0) > (full_property_count or subscription.full_property_count):
            return {'success': False, 'message': 'Add-on counts for full_property cannot exceed full_property_count.'}
        if (smart_lock_full_property_count or 0) > (full_property_count or subscription.full_property_count):
            return {'success': False, 'message': 'Add-on counts for full_property cannot exceed full_property_count.'}
        if (custom_branding_room_count or 0) > (room_count or subscription.room_count):
            return {'success': False, 'message': 'Add-on counts for room cannot exceed room_count.'}
        if (smart_lock_room_count or 0) > (room_count or subscription.room_count):
            return {'success': False, 'message': 'Add-on counts for room cannot exceed room_count.'}
        if (custom_branding_bed_count or 0) > (bed_count or subscription.bed_count):
            return {'success': False, 'message': 'Add-on counts for bed cannot exceed bed_count.'}
        if (smart_lock_bed_count or 0) > (bed_count or subscription.bed_count):
            return {'success': False, 'message': 'Add-on counts for bed cannot exceed bed_count.'}

        subscription.full_property_count = full_property_count if full_property_count is not None else subscription.full_property_count
        subscription.room_count = room_count if room_count is not None else subscription.room_count
        subscription.bed_count = bed_count if bed_count is not None else subscription.bed_count

        subscription.custom_branding_full_property_count = (
            custom_branding_full_property_count if custom_branding_full_property_count is not None
            else subscription.custom_branding_full_property_count
        )
        subscription.custom_branding_room_count = (
            custom_branding_room_count if custom_branding_room_count is not None
            else subscription.custom_branding_room_count
        )
        subscription.custom_branding_bed_count = (
            custom_branding_bed_count if custom_branding_bed_count is not None
            else subscription.custom_branding_bed_count
        )

        subscription.smart_lock_full_property_count = (
            smart_lock_full_property_count if smart_lock_full_property_count is not None
            else subscription.smart_lock_full_property_count
        )
        subscription.smart_lock_room_count = (
            smart_lock_room_count if smart_lock_room_count is not None
            else subscription.smart_lock_room_count
        )
        subscription.smart_lock_bed_count = (
            smart_lock_bed_count if smart_lock_bed_count is not None
            else subscription.smart_lock_bed_count
        )

        if billing_cycle and billing_cycle != subscription.billing_cycle:
            subscription.billing_cycle = billing_cycle

        subscription.save()

        try:
            property_counts = {
                'full_property': subscription.full_property_count,
                'room': subscription.room_count,
                'bed': subscription.bed_count
            }
            addon_counts = {
                'custom_branding_full_property': subscription.custom_branding_full_property_count,
                'custom_branding_room': subscription.custom_branding_room_count,
                'custom_branding_bed': subscription.custom_branding_bed_count,
                'smart_lock_full_property': subscription.smart_lock_full_property_count,
                'smart_lock_room': subscription.smart_lock_room_count,
                'smart_lock_bed': subscription.smart_lock_bed_count
            }
            stripe_update_result = StripeService.update_subscription(
                subscription=subscription,
                property_counts=property_counts,
                addon_counts=addon_counts
            )
            if stripe_update_result.get('success'):
                return {'success': True, 'subscription': subscription}
            else:
                return {'success': False, 'message': stripe_update_result.get('message')}
        except Exception as e:
            logger.error(f"Error updating subscription {subscription.id} in Stripe: {e}", exc_info=True)
            return {'success': False, 'message': str(e)}
    
    @staticmethod
    def cancel_subscription(subscription):
        """Cancel a subscription"""
        if subscription.status == 'trialing':
            subscription.status = 'canceled'
            subscription.end_date = timezone.now()
            subscription.save(update_fields=['status', 'end_date'])
            return {'success': True}
        try:
            result = StripeService.cancel_subscription(
                subscription.stripe_subscription_id,
                subscription.landlord
            )
            
            if result['success']:
                subscription.status = 'canceled'
                subscription.end_date = timezone.now()
                subscription.save(update_fields=['status', 'end_date'])
                return {'success': True}
            
            return {'success': False, 'message': result.get('message', 'Cancellation failed')}
        except Exception as e:
            logger.error(f"PaymentService error: {e}")
            return {'success': False, 'message': str(e)}
    
    @staticmethod
    def manual_assign_subscription(landlord, property_type, billing_cycle, unit_count, duration_months=12):
        """Manually assign a subscription to a landlord (for admin use)"""
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
        platform_fee = (amount * PaymentService.PLATFORM_FEE_RATE).quantize(Decimal("0.01"))
        try:
            stripe_connect = StripeConnect.objects.get(landlord=landlord)
            guest_pays_fee = stripe_connect.guest_pays_fee
        except StripeConnect.DoesNotExist:
            stripe_connect = None
            guest_pays_fee = True

        if guest_pays_fee:
            total_amount = (amount + platform_fee).quantize(Decimal("0.01"))
            landlord_amount = amount
        else:
            total_amount = amount
            landlord_amount = (amount - platform_fee).quantize(Decimal("0.01"))

        transaction = Transaction.objects.create(
            reservation=reservation,
            guest=guest,
            landlord=landlord,
            transaction_type=transaction_type,
            amount=total_amount,
            platform_fee=platform_fee,
            landlord_amount=landlord_amount,
            guest_paid_fee=guest_pays_fee,
            stripe_payment_id='',
            status='pending'
        )
        return {
            'success': True,
            'transaction': transaction,
            'stripe_connect': stripe_connect,
        }
    
    @staticmethod
    def process_transaction_payment(transaction_id, payment_method_id):
        """
        Process payment for a transaction with proper platform fee handling
        
        Args:
            transaction_id: ID of the transaction to process
            payment_method_id: Stripe payment method ID
            
        Returns:
            dict: Payment result with success status and additional data
        """
        try:
            tx = Transaction.objects.select_related(
                'reservation', 
                'landlord', 
                'reservation__property_ref'
            ).get(id=transaction_id)
        except Transaction.DoesNotExist:
            return {'success': False, 'message': 'Transaction not found'}

        amount_cents = int((tx.amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        platform_fee_cents = int((tx.platform_fee * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        landlord_amount_cents = int((tx.landlord_amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

        intent_kwargs = {
            'amount': amount_cents,
            'currency': tx.currency.lower(),
            'payment_method': payment_method_id,
            'confirm': True,
            'automatic_payment_methods': {
                'enabled': True,
                'allow_redirects': 'never'
            },
            'metadata': {
                'transaction_id': str(tx.id),
                'reservation_id': str(tx.reservation.id) if tx.reservation else '',
                'landlord_id': str(tx.landlord.id),
                'guest_paid_platform_fee': str(tx.guest_paid_platform_fee),
                'platform_fee': str(tx.platform_fee),
            },
            'description': f"Payment for reservation {tx.reservation.reservation_code if tx.reservation else 'N/A'}",
        }

        try:
            stripe_connect = StripeConnect.objects.get(landlord=tx.landlord)
            if not stripe_connect.stripe_account_id:
                logger.warning(f"Landlord {tx.landlord.id} has no Stripe account connected")
                return PaymentService._process_platform_only_payment(tx, intent_kwargs)
            
            if tx.guest_paid_platform_fee:
                intent_kwargs.update({
                    'transfer_data': {
                        'destination': stripe_connect.stripe_account_id,
                        # 'amount': landlord_amount_cents,  # Transfer the service amount to landlord
                    },
                    'application_fee_amount': platform_fee_cents,  # Platform keeps the fee
                    'on_behalf_of': stripe_connect.stripe_account_id,
                })
            else:
                intent_kwargs.update({
                    'transfer_data': {
                        'destination': stripe_connect.stripe_account_id,
                        # 'amount': landlord_amount_cents,  # Transfer reduced amount to landlord
                    },
                    'application_fee_amount': platform_fee_cents,  # Platform keeps the fee
                    'on_behalf_of': stripe_connect.stripe_account_id,
                })
                
        except StripeConnect.DoesNotExist:
            logger.warning(f"No Stripe Connect account found for landlord {tx.landlord.id}")
            return PaymentService._process_platform_only_payment(tx, intent_kwargs)

        try:
            intent = StripeService.create_payment_intent(**intent_kwargs)
            return PaymentService._handle_payment_intent_response(tx, intent)
            
        except StripeError as e:
            logger.error(f"Stripe error for transaction {tx.id}: {str(e)}")
            tx.status = "failed"
            tx.error_message = str(e)
            tx.save(update_fields=['status', 'error_message'])
            return {'success': False, 'message': f"Payment processing failed: {str(e)}"}
        except Exception as e:
            logger.error(f"Unexpected error processing transaction {tx.id}: {str(e)}")
            tx.status = "failed"
            tx.error_message = str(e)
            tx.save(update_fields=['status', 'error_message'])
            return {'success': False, 'message': 'Payment processing failed due to an unexpected error'}
    
    @staticmethod
    def _handle_payment_intent_response(transaction, intent):
        """Handle the response from Stripe PaymentIntent creation"""
        
        transaction.stripe_payment_intent_id = intent.id
        
        if intent.status == 'succeeded':
            transaction.status = "succeeded"
            transaction.completed_at = timezone.now()
            if hasattr(intent, 'charges') and intent.charges.data:
                charge = intent.charges.data[0]
                transaction.stripe_charge_id = charge.id
                if hasattr(charge, 'balance_transaction'):
                    bt = stripe.BalanceTransaction.retrieve(charge.balance_transaction)
                    transaction.stripe_processing_fee = Decimal(str(bt.fee / 100))
            
            transaction.save(update_fields=[
                'status', 'stripe_payment_intent_id', 'completed_at', 
                'stripe_charge_id', 'stripe_processing_fee'
            ])
            
            logger.info(f"Payment succeeded for transaction {transaction.id}")
            return {'success': True, 'payment_intent_id': intent.id}
            
        elif intent.status == 'requires_action':
            transaction.save(update_fields=['stripe_payment_intent_id'])
            logger.info(f"Payment requires action for transaction {transaction.id}")
            return {
                'success': True,
                'requires_action': True,
                'payment_intent_client_secret': intent.client_secret,
                'payment_intent_id': intent.id
            }
        else:
            transaction.status = "failed"
            transaction.error_message = f"Payment failed with status: {intent.status}"
            transaction.save(update_fields=['status', 'error_message', 'stripe_payment_intent_id'])
            logger.warning(f"Payment failed for transaction {transaction.id} with status: {intent.status}")
            return {'success': False, 'message': f"Payment failed: {intent.status}"}
    
    @staticmethod
    def confirm_payment_intent(payment_intent_id):
        """
        Confirm a payment intent after 3D Secure authentication
        Used for handling requires_action responses
        """
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            
            transaction = Transaction.objects.get(stripe_payment_intent_id=payment_intent_id)
            
            if intent.status == 'succeeded':
                transaction.status = 'succeeded'
                transaction.completed_at = timezone.now()
                
                if hasattr(intent, 'charges') and intent.charges.data:
                    charge = intent.charges.data[0]
                    transaction.stripe_charge_id = charge.id
                    
                transaction.save(update_fields=[
                    'status', 'completed_at', 'stripe_charge_id'
                ])
                
                return {'success': True}
            elif intent.status == 'canceled' or intent.status == 'payment_failed':
                transaction.status = 'failed'
                transaction.error_message = f"Payment {intent.status}"
                transaction.save(update_fields=['status', 'error_message'])
                return {'success': False, 'message': f"Payment {intent.status}"}
            else:
                logger.warning(f"Unexpected payment intent status: {intent.status}")
                return {'success': False, 'message': f"Unexpected status: {intent.status}"}
                
        except Transaction.DoesNotExist:
            return {'success': False, 'message': 'Transaction not found'}
        except StripeError as e:
            logger.error(f"Stripe error confirming payment intent {payment_intent_id}: {str(e)}")
            return {'success': False, 'message': str(e)}
    
    @staticmethod
    def refund_transaction(transaction_id, amount=None, reason=None):
        """
        Refund a transaction
        
        Args:
            transaction_id: ID of transaction to refund
            amount: Amount to refund (None for full refund)
            reason: Reason for refund
        """
        try:
            transaction = Transaction.objects.get(id=transaction_id)
            
            if transaction.status != Transaction.Status.SUCCEEDED:
                return {'success': False, 'message': 'Can only refund succeeded transactions'}
                
            if not transaction.stripe_payment_intent_id:
                return {'success': False, 'message': 'No Stripe payment intent found'}

            refund_amount_cents = None
            if amount:
                refund_amount_cents = int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

            refund = stripe.Refund.create(
                payment_intent=transaction.stripe_payment_intent_id,
                amount=refund_amount_cents,
                reason=reason or 'requested_by_customer',
                metadata={
                    'transaction_id': str(transaction.id),
                    'original_amount': str(transaction.amount)
                }
            )

            if refund.status == 'succeeded':
                refund_amount_decimal = Decimal(str(refund.amount / 100))
                transaction.refund_amount += refund_amount_decimal
                transaction.refunded_at = timezone.now()
                
                if transaction.refund_amount >= transaction.amount:
                    transaction.status = Transaction.Status.REFUNDED
                    
                transaction.save(update_fields=['refund_amount', 'refunded_at', 'status'])
                
                return {'success': True, 'refund_id': refund.id, 'refund_amount': refund_amount_decimal}
            else:
                return {'success': False, 'message': f'Refund failed: {refund.status}'}
                
        except Transaction.DoesNotExist:
            return {'success': False, 'message': 'Transaction not found'}
        except StripeError as e:
            logger.error(f"Refund failed for transaction {transaction_id}: {str(e)}")
            return {'success': False, 'message': str(e)}

    @staticmethod
    def create_payment_intent(transaction, payment_method_id=None):
        """Create a Stripe PaymentIntent for a guest payment."""
        try:
            payment_intent = StripeService.create_payment_intent(transaction, payment_method_id)
            return {
                'id': payment_intent.id,
                'client_secret': payment_intent.client_secret
            }
        except Exception as e:
            raise Exception(f"Failed to create payment intent: {str(e)}")

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
                UpsellPropertyAssignment.objects.create(
                    upsell=upsell,
                    property_id=property_id
                )
        return upsell
    
    @staticmethod
    def assign_upsell_to_properties(upsell_id, property_ids):
        """Assign an upsell to multiple properties"""
        upsell = Upsell.objects.get(id=upsell_id)
        UpsellPropertyAssignment.objects.filter(upsell=upsell).delete()
        assignments = []
        for property_id in property_ids:
            assignment = UpsellPropertyAssignment(
                upsell=upsell,
                property_id=property_id
            )
            assignments.append(assignment)
        UpsellPropertyAssignment.objects.bulk_create(assignments)
        return len(assignments)
    
    @staticmethod
    def get_property_upsells(property_id):
        """Get all upsells available for a property"""
        assignments = UpsellPropertyAssignment.objects.filter(
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
    def initiate_guest_payment(reservation, amount, currency='eur', coupon_code=None):
        """Initiate a guest payment by creating a transaction and PaymentIntent."""
        try:
            stripe_connect = reservation.property_ref.owner.stripe_connect if hasattr(reservation.property_ref.owner, 'stripe_connect') else None
            platform_fee = amount * Decimal('0.012')  # Updated to 1.2% platform fee
            guest_paid_fee = stripe_connect.guest_pays_fee if stripe_connect else True
            total_amount = amount + platform_fee if guest_paid_fee else amount
            landlord_amount = amount - (0 if guest_paid_fee else platform_fee)

            if coupon_code:
                coupon = Coupon.objects.get(code=coupon_code)
                if coupon.is_valid:
                    discount = PaymentService.apply_coupon(total_amount, coupon)
                    total_amount -= discount

            transaction = Transaction.objects.create(
                reservation=reservation,
                landlord=reservation.property_ref.owner,
                amount=total_amount,
                currency=currency,
                platform_fee=platform_fee,
                landlord_amount=landlord_amount,
                guest_paid_fee=guest_paid_fee,
                status='pending'
            )

            metadata = {
                'transaction_id': str(transaction.id),
                'reservation_id': str(reservation.id),
                'landlord_id': str(reservation.property_ref.owner.id)
            }
            transfer_data = {'destination': stripe_connect.stripe_account_id} if stripe_connect else None
            application_fee_amount = int(platform_fee * 100) if stripe_connect and guest_paid_fee else None
            on_behalf_of = stripe_connect.stripe_account_id if stripe_connect else None

            payment_intent = StripeService.create_payment_intent(
                amount=int(total_amount * 100),
                currency=currency,
                metadata=metadata,
                transfer_data=transfer_data,
                application_fee_amount=application_fee_amount,
                on_behalf_of=on_behalf_of
            )

            transaction.stripe_payment_intent_id = payment_intent.id
            transaction.save()

            return {'client_secret': payment_intent.client_secret, 'transaction_id': str(transaction.id)}
        except Exception as e:
            return {"error": f"Invalid payment data: {str(e)}"}

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
    def _calculate_end_date(start_date, billing_cycle):
        if billing_cycle == 'monthly':
            return start_date + relativedelta(months=+1)
        elif billing_cycle == 'yearly':
            return start_date + relativedelta(years=+1)
        else:
            return start_date + relativedelta(days=15)
    
    @staticmethod
    def create_subscription(landlord, **kwargs):
        """Create a new subscription for a landlord with dynamic configuration."""
        
        full_property_count = kwargs.get('full_property_count', 0)
        room_count = kwargs.get('room_count', 0)
        bed_count = kwargs.get('bed_count', 0)
        custom_branding_full_property_count = kwargs.get('custom_branding_full_property_count', 0)
        custom_branding_room_count = kwargs.get('custom_branding_room_count', 0)
        custom_branding_bed_count = kwargs.get('custom_branding_bed_count', 0)
        smart_lock_full_property_count = kwargs.get('smart_lock_full_property_count', 0)
        smart_lock_room_count = kwargs.get('smart_lock_room_count', 0)
        smart_lock_bed_count = kwargs.get('smart_lock_bed_count', 0)
        billing_cycle = kwargs.get('billing_cycle', 'monthly')
        total_price = kwargs.get('total_price', 0)
        subscription_details = kwargs.get('subscription_details', {})
        payment_method = kwargs.get('payment_method')
        
        validation_result = PaymentService._validate_subscription_data(
            room_count, bed_count, full_property_count,
            custom_branding_full_property_count, custom_branding_room_count, custom_branding_bed_count,
            smart_lock_full_property_count, smart_lock_room_count, smart_lock_bed_count
        )
        
        if not validation_result['valid']:
            return {'success': False, 'message': validation_result['message']}

        start_date = timezone.now()
        end_date = PaymentService._calculate_end_date(start_date, billing_cycle)
        
        subscription = LandlordSubscription.objects.create(
            landlord=landlord,
            full_property_count=full_property_count,
            room_count=room_count,
            bed_count=bed_count,
            custom_branding_full_property_count=custom_branding_full_property_count,
            custom_branding_room_count=custom_branding_room_count,
            custom_branding_bed_count=custom_branding_bed_count,
            smart_lock_full_property_count=smart_lock_full_property_count,
            smart_lock_room_count=smart_lock_room_count,
            smart_lock_bed_count=smart_lock_bed_count,
            billing_cycle=billing_cycle,
            total_price=total_price,
            subscription_details=subscription_details,
            start_date=start_date,
            end_date=end_date,
            status='pending'       #LandlordSubscription.STATUS_CHOICE
        )
        
        print('Created subscription:', subscription)

        try:
            result = StripeService.create_subscription(
                subscription=subscription,
                billing_cycle=billing_cycle,
                total_price=subscription_details["final_price"],
                property_counts={
                    'full_property': full_property_count,
                    'room': room_count,
                    'bed': bed_count
                },
                addon_counts={
                    'custom_branding_full_property': custom_branding_full_property_count,
                    'custom_branding_room': custom_branding_room_count,
                    'custom_branding_bed': custom_branding_bed_count,
                    'smart_lock_full_property': smart_lock_full_property_count,
                    'smart_lock_room': smart_lock_room_count,
                    'smart_lock_bed': smart_lock_bed_count
                },
                payment_method=kwargs.get('payment_method')
            )
            
            invoice_obj = getattr(result, "latest_invoice", None)
            if invoice_obj and hasattr(invoice_obj, "id"):
                with transaction.atomic():
                    invoice_amount = Decimal(subscription_details["final_price"])
                    SubscriptionInvoice.objects.create(
                        subscription=subscription,
                        stripe_invoice_id=invoice_obj.id,
                        amount=invoice_amount,
                        status=invoice_obj.status,
                        pdf_url=invoice_obj.invoice_pdf,
                        hosted_invoice_url=invoice_obj.hosted_invoice_url,
                    )
                    if invoice_obj.status == "paid":
                        send_subscription_invoice_email(
                            invoice=invoice_obj,
                            landlord=landlord,
                        )
                    subscription.stripe_subscription_id = result.id
                    subscription.status = result.status
                    subscription.save(update_fields=["stripe_subscription_id", "status"])
            else:
                print("⚠️ No invoice returned from Stripe for subscription:", result.id)

            PaymentService.sync_subscription_from_stripe(subscription)
            subscription.refresh_from_db()
            return {'success': True, 'subscription': subscription, 'stripe_subscription_id': result.id}
        except Exception as e:
            subscription.delete()
            return {'success': False, 'message': str(e)}

    def _handle_payment_intent(subscription, stripe_result) -> Optional[str]:
        """Confirm if needed and activate the local subscription."""
        pi = getattr(stripe_result.latest_invoice, 'payment_intent', None)
        print('pi: ', pi)
        if not pi:
            return None

        client_secret = getattr(pi, 'client_secret', None)

        if pi.status in ('requires_confirmation', 'requires_action'):
            pi = stripe.PaymentIntent.confirm(pi.id)

        if pi.status == 'succeeded':
            print('pi: ', pi)
            subscription.status = 'active'
            subscription.save(update_fields=['status'])

        return client_secret

    @staticmethod
    def _validate_subscription_data(room_count, bed_count, full_property_count,
                                  custom_branding_full_property_count, custom_branding_room_count, custom_branding_bed_count,
                                  smart_lock_full_property_count, smart_lock_room_count, smart_lock_bed_count):
        """Validate subscription data."""
        
        if room_count > 0 and room_count < 10:
            return {'valid': False, 'message': 'Room count must be at least 10 if greater than 0.'}
        
        if bed_count > 0 and bed_count < 10:
            return {'valid': False, 'message': 'Bed count must be at least 10 if greater than 0.'}

        if custom_branding_full_property_count > full_property_count or smart_lock_full_property_count > full_property_count:
            return {'valid': False, 'message': 'Add-on counts for full properties cannot exceed the property count.'}
        
        if custom_branding_room_count > room_count or smart_lock_room_count > room_count:
            return {'valid': False, 'message': 'Add-on counts for rooms cannot exceed the room count.'}
        
        if custom_branding_bed_count > bed_count or smart_lock_bed_count > bed_count:
            return {'valid': False, 'message': 'Add-on counts for beds cannot exceed the bed count.'}
        
        return {'valid': True}
    
    @staticmethod
    def _map_stripe_status(stripe_status):
        """Map Stripe status to our internal status."""
        status_mapping = {
            'active': 'active',
            'past_due': 'past_due',
            'canceled': 'canceled',
            'cancelled': 'canceled',
            'trialing': 'trialing',
            'unpaid': 'past_due',
            'incomplete': 'incomplete',
            'incomplete_expired': 'expired'
        }
        return status_mapping.get(stripe_status, 'pending')

    @staticmethod
    def sync_subscription_from_stripe(subscription):
        if not subscription.stripe_subscription_id:
            return False
        
        try:
            stripe_subscription = stripe.Subscription.retrieve(
                subscription.stripe_subscription_id,
                expand=['latest_invoice', 'latest_invoice.payment_intent']
            )

            update_fields = []
            
            new_status = PaymentService._map_stripe_status(stripe_subscription.status)
            if subscription.status != new_status:
                subscription.status = new_status
                update_fields.append('status')
            
            if hasattr(stripe_subscription, 'current_period_start') and stripe_subscription.current_period_start:
                stripe_start_date = timezone.datetime.fromtimestamp(
                    stripe_subscription.current_period_start, tz=timezone.utc
                )
                if subscription.start_date != stripe_start_date:
                    subscription.start_date = stripe_start_date
                    update_fields.append('start_date')
                    
            if hasattr(stripe_subscription, 'current_period_end'):
                stripe_end_date = timezone.datetime.fromtimestamp(
                    stripe_subscription.current_period_end, tz=timezone.utc
                )
                if subscription.end_date != stripe_end_date:
                    subscription.end_date = stripe_end_date
                    update_fields.append('end_date')
            
            if getattr(stripe_subscription, 'trial_end', None):
                stripe_trial_end = timezone.datetime.fromtimestamp(
                    stripe_subscription.trial_end, tz=timezone.utc
                )
                if hasattr(subscription, 'trial_end_date') and subscription.trial_end_date != stripe_trial_end:
                    subscription.trial_end_date = stripe_trial_end
                    update_fields.append('trial_end_date')
            
            if update_fields:
                subscription.save(update_fields=update_fields)
            return True

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error syncing subscription {subscription.id}: {str(e)}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Error syncing subscription {subscription.id}: {str(e)}", exc_info=True)
            return False