import stripe, logging
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

from payment.models import (
    LandlordSubscription,
    SubscriptionInvoice,
    Transaction,
    PaymentFailureLog
)

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY

class StripeService:
    @staticmethod
    def create_customer(user):
        """Create a stripe customer for a user"""
        try:
            customer = stripe.Customer.create(
                email = user.email,
                name = f"{user.first_name} {user.last_name}",
                metadata ={
                    'user_id': user.id,
                    'username': user.username
                }
            )
            #store user id
            user.stripe_customer_id = customer.id
            user.save(updated_fields=['stripe_customer_id'])

            return customer
        except Exception as e:
            logger.error(f"Error creating Stripe customer: {str(e)}")
            raise
    
    @staticmethod
    def create_subscription(landlord_subscription, payment_method_id=None):
        """Create a Stripe subscription for a landlord"""
        try:
            landlord = landlord_subscription.landlord
            if not landlord.stripe_customer_id:
                customer = StripeService.create_customer(landlord)
                customer_id = customer.id
            else:
                customer_id = landlord.stripe_customer_id
            if payment_method_id:
                stripe.PaymentMethod.attach(
                    payment_method_id,
                    customer=customer_id
                )
                stripe.Customer.modify(
                    customer_id,
                    invoice_settings={
                        'default_payment_method': payment_method_id,
                    }
                )

            product_name = f"{landlord_subscription.subscription_plan.get_property_type_display()} Subscription"
            product = stripe.Product.create(
                name=product_name,
                metadata={
                    'property_type': landlord_subscription.subscription_plan.property_type
                }
            )

            unit_price_cents = int(landlord_subscription.subscription_plan.price_per_unit * 100)
            price = stripe.Price.create(
                unit_amount=unit_price_cents,
                currency='eur',
                recurring={
                    'interval': 'month' if landlord_subscription.subscription_plan.billing_cycle == 'monthly' else 'year',
                },
                product=product.id,
                metadata={
                    'property_type': landlord_subscription.subscription_plan.property_type,
                    'billing_cycle': landlord_subscription.subscription_plan.billing_cycle
                }
            )

            # Create subscription
            subscription_params = {
                'customer': customer_id,
                'items': [{
                    'price': price.id,
                    'quantity': landlord_subscription.unit_count
                }],
                'metadata': {
                    'landlord_id': landlord.id,
                    'subscription_id': landlord_subscription.id
                },
                'payment_behavior': 'default_incomplete',
                'expand': ['latest_invoice.payment_intent']
            }

            if landlord_subscription.status == 'trialing' and landlord_subscription.trial_end_date:
                subscription_params['trial_end'] = int(landlord_subscription.trial_end_date.timestamp())

            stripe_subscription = stripe.Subscription.create(**subscription_params)

            # Update local subscription record
            landlord_subscription.stripe_subscription_id = stripe_subscription.id
            landlord_subscription.save()

            return stripe_subscription

        except Exception as e:
            logger.error(f"Error creating Stripe subscription: {str(e)}")
            raise
    
    @staticmethod
    def update_subscription(landlord_subscription):
        """Update an existing Stripe subscription"""
        try:
            stripe_subscription = stripe.Subscription.retrieve(
                landlord_subscription.stripe_subscription_id
            )
            # Update quantity
            stripe.Subscription.modify(
                landlord_subscription.stripe_subscription_id,
                items =[{
                    'id': stripe.subscription['items']['data'][0].id,
                    'quantity': landlord_subscription.unit_count
                }]
            )
            return True
        except Exception as e:
            logger.error(f"Error cancelling Stripe subscription: {str(e)}")
            raise
    
    @staticmethod
    def cancel_subscription(landlord_subscription):
        """Cancel Stripe Subscription"""
        try:
            stripe.Subscription.delete(
                landlord_subscription.stripe_subscription_id
            )
            landlord_subscription.status = 'canceled'
            landlord_subscription.end_date = timezone.now()
            landlord_subscription.save()
            
            return True
        except Exception as e:
            logger.error(f"Error canceling Stripe subscription: {str(e)}")
            raise

    @staticmethod
    def create_connect_account(landlord):
        """Create a stripe connect account for a landlord."""
        try:
            account = stripe.Account.create(
                type='express',
                coutry='ES',
                email=landlord.email,
                capabilities={
                    "card_payments": {"requested":True},
                    "transfers": {"requested":True},
                },
                business_type="individual",
                metadata={
                    'user_id':landlord.id,
                    'username':landlord.username
                }
            )
            return account
        except Exception as e:
            logger.error(f"Error creating stripe connect account: {str(e)}")
            raise

    @staticmethod
    def create_account_link(account_id, refresh_url, return_url):
        """Create stripe connect onboarding link"""
        try:
            account_link = stripe.AccountLink.create(
                account=account_id,
                refresh_url=refresh_url,
                return_url=return_url,
                type="account_onboarding",
            )
            return account_link.url
        except Exception as e:
            logger.error(f"Error creating account link: {str(e)}")
            raise

    @staticmethod
    def create_payment_intent(transaction, payment_method_id=None):
        """Create a payment intent for guest payments """
        try:
            application_fee_amount = int(transaction.platform_fee * 100)

            payment_intent = stripe.PaymentIntent.create(
                amount=int(transaction.amount * 100),
                currency="eur",
                payment_method=payment_method_id,
                confirmation_method="manual",
                application_fee_amount=application_fee_amount,
                transfer_data={
                    "destination": transaction.lanlord.stripeconnect.stripe_account_id,
                },
                metadata={
                    "transaction_id": transaction.id,
                    "transaction_type":transaction.transaction_type,
                    "guest_id": transaction.guest.id,
                    "landlord_id":transaction.landlord.id
                }
            )

            transaction.stripe_payment_id= payment_intent.id
            transaction.save()
            return payment_intent
        except Exception as e:
            logger.error(f"Error creating payment inetnt: {str(e)}")
            raise
    
    @staticmethod
    def process_webhook_event(payload, sig_header):
        """process stripe webhook event"""
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header = settings.STRIPE_WEBHOOK_SECRET
            )
            event_type =event['type']
            if event_type == "invoice.paid":
                StripeService.handle_invoice_paid(event)
            elif event_type == "invoice.payment_failed":
                StripeService.handle_invoice_payment_failed(event)
            elif event_type == "customer.subscription.deleted":
                StripeService.handle_subscription_deleted(event)
            elif event_type == "payment_intent.succeeded":
                StripeService.handle_payment_intent_succeeded(event)
            elif event_type == "payment_intent.payment_failed":
                StripeService.handle_payment_intent_failed(event)
            
            return True
        except Exception as e:
            logger.error(f"Error processing webhook: {str(e)}")
            raise
    
    @staticmethod
    def handle_invoice_paid(event):
        """Handle invoice paid webhook event"""
        invoice = event['data']['object']
        subscription_id = invoice.get('subscriotion')

        if not subscription_id:
            return
        try:
            landlord_subcription = LandlordSubscription.objects.get(stripe_subscription_id = subscription_id)

            if landlord_subcription.status =='past_due':
                landlord_subcription.status = 'active'
                landlord_subcription.save()

            # invoice record
            invoice_record, created = SubscriptionInvoice.objects.get_or_create(
                stripe_invoice_id = invoice['id'],
                defaults={
                    'subscription':landlord_subcription,
                    'amount':invoice['amount_paid'] /100,
                    'status': 'paid',
                    'paid_at': timezone.now()
                }
            )

            if not created and invoice_record.status != 'paid':
                invoice_record.status = 'paid'
                invoice_record.paid_at = timezone.now()
                invoice_record.save()
        except LandlordSubscription.DoesNotExist:
            logger.error(f"Subscription not found for invoice: {invoice['id']}")
    
    @staticmethod
    def handle_invoice_payment_failed(event):
        """Handle invoice payment failed webhook event"""
        invoice = event['data']['object']
        subscription_id = invoice.get('subscription')
        
        if not subscription_id:
            return
        
        try:
            landlord_subscription = LandlordSubscription.objects.get(stripe_subscription_id=subscription_id)
            
            # Create or update invoice record
            invoice_record, created = SubscriptionInvoice.objects.get_or_create(
                stripe_invoice_id=invoice['id'],
                defaults={
                    'subscription': landlord_subscription,
                    'amount': invoice['amount_due'] / 100,  # Convert from cents
                    'status': 'failed'
                }
            )
            
            if not created:
                invoice_record.status = 'failed'
                invoice_record.save()
            
            # Handle payment failure
            payment_failures = PaymentFailureLog.objects.filter(
                subscription=landlord_subscription
            ).count()
            
            # Create a new failure log
            next_retry_date = None
            if payment_failures == 0:
                next_retry_date = timezone.now() + timedelta(days=3)
            elif payment_failures == 1:
                next_retry_date = timezone.now() + timedelta(days=5)
            elif payment_failures == 2:
                next_retry_date = timezone.now() + timedelta(days=7)
            
            # Create failure log entry
            PaymentFailureLog.objects.create(
                subscription=landlord_subscription,
                stripe_error_code=invoice.get('last_payment_error', {}).get('code'),
                stripe_error_message=invoice.get('last_payment_error', {}).get('message'),
                attempt_number=payment_failures + 1,
                next_retry_date=next_retry_date
            )
            
            # Update subscription status
            landlord_subscription.status = 'past_due'
            
            # If this is the 3rd failure, suspend the subscription
            if payment_failures >= 2:  # This is the 3rd attempt (0-indexed)
                landlord_subscription.status = 'suspended'
                # TODO: Send email notification about suspension
            
            landlord_subscription.save()
            
        except LandlordSubscription.DoesNotExist:
            logger.error(f"Subscription not found for invoice: {invoice['id']}")
    
    @staticmethod
    def handle_subscription_deleted(event):
        """Handle customer.subscription.deleted webhook event"""
        subscription = event['data']['object']
        
        try:
            landlord_subscription = LandlordSubscription.objects.get(stripe_subscription_id=subscription['id'])
            landlord_subscription.status = 'canceled'
            landlord_subscription.end_date = timezone.now()
            landlord_subscription.save()
        except LandlordSubscription.DoesNotExist:
            logger.error(f"Subscription not found: {subscription['id']}")
    
    @staticmethod
    def handle_payment_intent_succeeded(event):
        """Handle payment_intent.succeeded webhook event"""
        payment_intent = event['data']['object']
        
        try:
            transaction = Transaction.objects.get(stripe_payment_id=payment_intent['id'])
            transaction.status = 'completed'
            transaction.completed_at = timezone.now()
            
            # Calculate Stripe fee
            stripe_fee = payment_intent.get('charges', {}).get('data', [{}])[0].get('fee', 0) / 100
            transaction.stripe_fee = stripe_fee
            
            transaction.save()
            
            # TODO: Send confirmation email to guest and landlord
            
        except Transaction.DoesNotExist:
            logger.error(f"Transaction not found for payment intent: {payment_intent['id']}")
    
    @staticmethod
    def handle_payment_intent_failed(event):
        """Handle payment_intent.payment_failed webhook event"""
        payment_intent = event['data']['object']
        
        try:
            transaction = Transaction.objects.get(stripe_payment_id=payment_intent['id'])
            transaction.status = 'failed'
            transaction.save()
            
            # TODO: Send failure notification to guest and landlord
            
        except Transaction.DoesNotExist:
            logger.error(f"Transaction not found for payment intent: {payment_intent['id']}")