from decimal import Decimal, ROUND_HALF_UP
import json
import math
import stripe, logging
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from stripe.error import StripeError
from django.shortcuts import get_object_or_404
from payment.models import (
    LandlordSubscription,
    SubscriptionInvoice,
    Transaction,
    PaymentFailureLog,
    StripeConnect,
    SubscriptionPlan
)
from django.db import transaction
from stripe.error import InvalidRequestError, StripeError
from dateutil.relativedelta import relativedelta
from payment.utils import send_subscription_invoice_email


logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY

def format_stripe_amount(decimal_amount):
    """Convert decimal amount to Stripe cents"""
    return int((decimal_amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def format_decimal_amount(stripe_cents):
    """Convert Stripe cents to decimal amount"""
    return Decimal(str(stripe_cents / 100))


class StripeService:
    @staticmethod
    def _get_connected_id(landlord):
        """
        Raise a clear error if the landlord has not connected a Stripe account.
        """
        connect = get_object_or_404(
            StripeConnect,
            landlord=landlord,
            is_active=True
        )
        return connect.stripe_account_id
    
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
            user.save()

            return customer
        except Exception as e:
            logger.error(f"Error creating Stripe customer: {str(e)}")
            raise
    
    @staticmethod
    def create_subscription(subscription, billing_cycle, total_price, property_counts, addon_counts, payment_method):
        """Create a Stripe subscription with dynamic property types and billing cycles."""
        
        customer_id = subscription.landlord.stripe_customer_id
        items = []
        subscription_details = subscription.subscription_details
        final_price = Decimal(subscription_details.get('final_price', '0'))
        base_price = Decimal(subscription_details.get('base_price', '0'))
        discount_applied = subscription_details.get('discount_applied', '0%')
        
        print(f"Creating Stripe subscription for billing_cycle: {billing_cycle}")
        print(f"Property counts: {property_counts}")
        print(f"Addon counts: {addon_counts}")
        print(f"Base Price: {base_price}")
        print(f"Final Price after discount ({discount_applied}): {final_price}")
        
        plan = SubscriptionPlan.objects.filter(is_active=True, billing_cycle=billing_cycle).first()
        if not plan:
            raise Exception(f"No active subscription plan found for billing cycle: {billing_cycle}")

        property_prices = {
            'full_property': Decimal(subscription_details.get('full_property_price', '0')),
            'room': Decimal(subscription_details.get('room_price', '0')),
            'bed': Decimal(subscription_details.get('bed_price', '0'))
        }

        addon_prices = {
            'custom_branding_full_property': Decimal(subscription_details.get('custom_branding_full_property_price', '0')),
            'custom_branding_room': Decimal(subscription_details.get('custom_branding_room_price', '0')),
            'custom_branding_bed': Decimal(subscription_details.get('custom_branding_bed_price', '0')),
            'smart_lock_full_property': Decimal(subscription_details.get('smart_lock_full_property_price', '0')),
            'smart_lock_room': Decimal(subscription_details.get('smart_lock_room_price', '0')),
            'smart_lock_bed': Decimal(subscription_details.get('smart_lock_bed_price', '0'))
        }

        for property_type, count in property_counts.items():
            if count > 0:
                try:
                    base_unit_price = property_prices[property_type]
                    discount_percentage = Decimal(discount_applied.strip('%')) / 100
                    discounted_price = base_unit_price * (1 - discount_percentage)
                    price_in_cents = int(discounted_price * 100)

                    price_description = f"{property_type.replace('_', ' ').title()} - {billing_cycle.title()}"
                    if discount_applied != '0%':
                        price_description += f" (Discount: {discount_applied})"
                    price = stripe.Price.create(
                        unit_amount=price_in_cents,
                        currency='eur',
                        recurring={'interval': 'year' if billing_cycle == 'yearly' else 'month'},
                        product_data={
                            'name': price_description,
                            'metadata': {
                                'original_price': str(base_unit_price),
                                'discount_applied': discount_applied,
                                'discounted_price': str(discounted_price)
                            }
                        },
                        metadata={
                            'property_type': property_type,
                            'billing_cycle': billing_cycle,
                            'plan_id': str(plan.id),
                            'discount_applied': discount_applied
                        }
                    )
                    items.append({
                        'price': price.id,
                        'quantity': count
                    })
                except stripe.error.StripeError as e:
                    raise Exception(f"Failed to create price for {property_type}: {str(e)}")
        for addon_type, count in addon_counts.items():
            if count > 0:
                try:
                    base_unit_price = addon_prices[addon_type]
                    price_in_cents = int(base_unit_price * 100)

                    addon_description = f"{addon_type.replace('_', ' ').title()} - {billing_cycle.title()}"
                    price = stripe.Price.create(
                        unit_amount=price_in_cents,
                        currency='eur',
                        recurring={'interval': 'year' if billing_cycle == 'yearly' else 'month'},
                        product_data={
                            'name': addon_description,
                            'metadata': {
                                'original_price': str(base_unit_price),
                                'type': 'addon'
                            }
                        },
                        metadata={
                            'addon_type': addon_type,
                            'billing_cycle': billing_cycle,
                            'plan_id': str(plan.id),
                            'type': 'addon'
                        }
                    )
                    items.append({
                        'price': price.id,
                        'quantity': count
                    })
                except stripe.error.StripeError as e:
                    raise Exception(f"Failed to create price for {addon_type}: {str(e)}")

        metadata = {
            'subscription_id': str(subscription.id),
            'landlord_id': str(subscription.landlord.id),
            'billing_cycle': billing_cycle,
            'base_price': str(base_price),
            'final_price': str(final_price),
            'discount_applied': discount_applied,
            'subscription_details': json.dumps(subscription_details)
        }

        try:
            stripe.PaymentMethod.attach(
                payment_method,
                customer=customer_id,
            )
            stripe.Customer.modify(
                customer_id,
                invoice_settings={'default_payment_method': payment_method}
            )
            stripe_subscription = stripe.Subscription.create(
                customer=customer_id,
                items=items,
                metadata=metadata,
                default_payment_method=payment_method,
                expand=['latest_invoice.payment_intent']
            )
            return stripe_subscription
        except stripe.error.StripeError as e:
            raise Exception(f"Stripe subscription creation failed: {str(e)}")
        
    @staticmethod
    def _get_or_create_stripe_price(plan, property_type, billing_cycle):
        """Get or create Stripe price ID for property types."""
        
        price_mapping = {
            'full_property': plan.full_property,
            'room': plan.room,
            'bed': plan.bed
        }
        
        price_amount = price_mapping.get(property_type)
        if not price_amount:
            return None
        
        price_in_cents = int(price_amount * 100)
        
        setting_key = f'STRIPE_{property_type.upper()}_PRICE_ID_{billing_cycle.upper()}'
        stored_price_id = getattr(settings, setting_key, None)
        
        if stored_price_id:
            return stored_price_id
        
        try:
            interval = 'month' if billing_cycle == 'monthly' else 'year'
            price = stripe.Price.create(
                unit_amount=price_in_cents,
                currency='eur',
                recurring={'interval': interval},
                product_data={
                    'name': f'{property_type.replace("_", " ").title()} - {billing_cycle.title()}'
                },
                metadata={
                    'property_type': property_type,
                    'billing_cycle': billing_cycle,
                    'plan_id': str(plan.id)
                }
            )
            return price.id
        except stripe.error.StripeError as e:
            print(f"Failed to create Stripe price for {property_type}: {str(e)}")
            return None

    @staticmethod
    def _get_or_create_addon_stripe_price(plan, addon_type, billing_cycle):
        """Get or create Stripe price ID for add-ons."""
        
        addon_price_mapping = {
            'custom_branding_full_property': plan.custom_branding,
            'custom_branding_room': plan.custom_branding,
            'custom_branding_bed': plan.custom_branding,
            'smart_lock_full_property': plan.smart_lock_full_property,
            'smart_lock_room': plan.smart_lock_room,
            'smart_lock_bed': plan.smart_lock_room  # assuming same price for bed as room
        }
        
        price_amount = addon_price_mapping.get(addon_type)
        if not price_amount:
            return None
        
        price_in_cents = int(price_amount * 100)
        setting_key = f'STRIPE_{addon_type.upper()}_PRICE_ID_{billing_cycle.upper()}'
        stored_price_id = getattr(settings, setting_key, None)
        if stored_price_id:
            return stored_price_id
        try:
            interval = 'month' if billing_cycle == 'monthly' else 'year'
            price = stripe.Price.create(
                unit_amount=price_in_cents,
                currency='eur',
                recurring={'interval': interval},
                product_data={
                    'name': f'{addon_type.replace("_", " ").title()} - {billing_cycle.title()}'
                },
                metadata={
                    'addon_type': addon_type,
                    'billing_cycle': billing_cycle,
                    'plan_id': str(plan.id)
                }
            )
            print(f"Created new Stripe price: {price.id} for {addon_type}")
            return price.id
        except stripe.error.StripeError as e:
            print(f"Failed to create Stripe price for {addon_type}: {str(e)}")
            return None
    
    @staticmethod
    def cancel_subscription(stripe_subscription_id: str, landlord):
        """
        Cancel a Stripe subscription under the connected account.
        """
        connected_id = StripeService._get_connected_id(landlord)
        try:
            subscription = stripe.Subscription.retrieve(
                stripe_subscription_id,
                stripe_account=connected_id
            )
            if subscription.status != 'canceled':
                cancelled = stripe.Subscription.delete(
                    stripe_subscription_id,
                    stripe_account=connected_id
                )
                if cancelled.status == 'canceled':
                    return {"success": True}
                return {"success": False, "message": "Stripe cancellation failed"}
            return {"success": True, "message": "Subscription already canceled"}
        except stripe.error.InvalidRequestError as e:
            if 'No such subscription' in str(e):
                return {"success": True, "message": "Subscription already canceled"}
            logger.error(f"Stripe error: {e}")
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error(f"Stripe connection error: {e}")
            return {"success": False, "message": str(e)}

    @staticmethod
    def update_subscription_quantity(subscription, property_counts, addon_counts):
        """
        Update quantity on an existing Stripe subscription under the connected account.
        """
        stripe_sub_id = subscription.stripe_subscription_id
        connected_id = StripeService._get_connected_id(subscription.landlord)
        stripe_sub = stripe.Subscription.retrieve(
            stripe_sub_id,
            stripe_account=connected_id
        )

        existing_items = stripe_sub['items']['data']
        existing_map = { item['price']['id']: item['id'] for item in existing_items }

        new_items = []
        plans = SubscriptionPlan.objects.filter(is_active=True, billing_cycle=subscription.billing_cycle)

        for property_type, count in property_counts.items():
            if count <= 0:
                continue

            search_property_type = 'full' if property_type == 'full_property' else property_type
            plan_obj = plans.filter(property_type=search_property_type).first()
            if not plan_obj:
                continue

            price_id = plan_obj.stripe_price_id
            if price_id in existing_map:
                new_items.append({
                    'id': existing_map[price_id],
                    'price': price_id,
                    'quantity': count
                })
            else:
                new_items.append({
                    'price': price_id,
                    'quantity': count
                })

        addon_price_mapping = {
            'custom_branding_full_property': getattr(settings, f'STRIPE_CUSTOM_BRANDING_FULL_PRICE_ID_{subscription.billing_cycle.upper()}', None),
            'custom_branding_room': getattr(settings, f'STRIPE_CUSTOM_BRANDING_ROOM_PRICE_ID_{subscription.billing_cycle.upper()}', None),
            'custom_branding_bed': getattr(settings, f'STRIPE_CUSTOM_BRANDING_BED_PRICE_ID_{subscription.billing_cycle.upper()}', None),
            'smart_lock_full_property': getattr(settings, f'STRIPE_SMART_LOCK_FULL_PRICE_ID_{subscription.billing_cycle.upper()}', None),
            'smart_lock_room': getattr(settings, f'STRIPE_SMART_LOCK_ROOM_PRICE_ID_{subscription.billing_cycle.upper()}', None),
            'smart_lock_bed': getattr(settings, f'STRIPE_SMART_LOCK_BED_PRICE_ID_{subscription.billing_cycle.upper()}', None)
        }

        for addon_type, count in addon_counts.items():
            if count <= 0:
                continue

            price_id = addon_price_mapping.get(addon_type)
            if not price_id:
                continue

            if price_id in existing_map:
                new_items.append({
                    'id': existing_map[price_id],
                    'price': price_id,
                    'quantity': count
                })
            else:
                new_items.append({
                    'price': price_id,
                    'quantity': count
                })

        try:
            updated_sub = stripe.Subscription.modify(
                stripe_sub_id,
                items=new_items,
                stripe_account=connected_id
            )
            return {"success": True, "updated_subscription": updated_sub}
        except Exception as e:
            logger.error(f"Stripe error updating subscription {stripe_sub_id}: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    @staticmethod
    def create_connect_account(landlord):
        """Create a stripe connect account for a landlord."""
        try:
            account = stripe.Account.create(
                type="express",
                country="ES",
                email=landlord.email,
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
                business_type="individual",
                metadata={
                    "user_id": str(landlord.id),
                    "username": landlord.username
                }
            )
            landlord.stripe_account_id = account.id
            landlord.save(update_fields=["stripe_account_id"])
            StripeConnect.objects.get_or_create(
                landlord=landlord,
                defaults={"stripe_account_id": account.id, "is_active": True}
            )
            return account.id
        except Exception as e:
            logger.error(f"Error creating Stripe Connect account: {e}")
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
            logger.error(f"Error creating account link: {e}")
            raise

    @staticmethod
    def create_payment_intent(amount, currency, metadata, description=None, 
                            payment_method=None, confirm=False, transfer_data=None, 
                            application_fee_amount=None, on_behalf_of=None,
                            automatic_payment_methods=None):
        """
        Create a Stripe PaymentIntent with comprehensive parameter support
        
        Args:
            amount: Amount in cents
            currency: Currency code (e.g., 'eur', 'usd')
            metadata: Dictionary of metadata
            description: Description of the payment
            payment_method: Payment method ID
            confirm: Whether to confirm immediately
            transfer_data: Transfer data for Connect payments
            application_fee_amount: Platform fee amount in cents
            on_behalf_of: Stripe Connect account ID
            
        Returns:
            stripe.PaymentIntent: Created payment intent
        """
        params = {
            'amount': amount,
            'currency': currency.lower(),
            'metadata': metadata or {},
        }
        
        if description:
            params['description'] = description
        if payment_method:
            params['payment_method'] = payment_method
        if confirm:
            params['confirm'] = confirm
        if transfer_data:
            params['transfer_data'] = transfer_data
        if application_fee_amount and application_fee_amount > 0:
            params['application_fee_amount'] = application_fee_amount
        if on_behalf_of:
            params['on_behalf_of'] = on_behalf_of
        if automatic_payment_methods is  not None:
            params['automatic_payment_methods'] = automatic_payment_methods

        try:
            payment_intent = stripe.PaymentIntent.create(**params)
            logger.info(f"Created payment intent {payment_intent.id} for amount {amount} {currency}")
            return payment_intent
        except StripeError as e:
            logger.error(f"Failed to create payment intent: {str(e)}")
            raise

    @staticmethod
    def retrieve_payment_intent(payment_intent_id):
        """Retrieve a payment intent by ID"""
        try:
            return stripe.PaymentIntent.retrieve(payment_intent_id)
        except StripeError as e:
            logger.error(f"Failed to retrieve payment intent {payment_intent_id}: {str(e)}")
            raise

    @staticmethod
    def create_setup_intent(customer_id=None, metadata=None):
        """Create a setup intent for saving payment methods"""
        params = {}
        if customer_id:
            params['customer'] = customer_id
        if metadata:
            params['metadata'] = metadata
            
        try:
            return stripe.SetupIntent.create(**params)
        except StripeError as e:
            logger.error(f"Failed to create setup intent: {str(e)}")
            raise   

    @staticmethod
    def create_customer(user, name=None, metadata=None):
        """Create a Stripe customer"""
        try:
            params = {
                "email": user.email,
                **({"name": user.get_full_name()} if user.get_full_name() else {}),
            }
            if metadata:
                params["metadata"] = metadata
            customer = stripe.Customer.create(**params)
            user.stripe_customer_id = customer.id
            user.save(update_fields=["stripe_customer_id"])
            return customer

        except InvalidRequestError as e:
            logger.warning(f"Stripe refused email {user.email}: {e.user_message or str(e)}")
            return None
        except StripeError as e:
            logger.error(f"StripeError while creating customer for {user.id}: {str(e)}")
            raise

    @staticmethod
    def get_balance_transaction(balance_transaction_id):
        """Get balance transaction details including fees"""
        try:
            return stripe.BalanceTransaction.retrieve(balance_transaction_id)
        except StripeError as e:
            logger.error(f"Failed to retrieve balance transaction {balance_transaction_id}: {str(e)}")
            raise

    @staticmethod
    def validate_webhook_signature(payload, signature, webhook_secret):
        """Validate Stripe webhook signature"""
        try:
            return stripe.Webhook.construct_event(payload, signature, webhook_secret)
        except ValueError as e:
            logger.error(f"Invalid webhook payload: {str(e)}")
            raise
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {str(e)}")
            raise
    
    @staticmethod
    def create_express_account(user):
        """Create Stripe Express Connect account"""
        try:
            account = stripe.Account.create(
                type="express",
                country="ES",
                email=user.email,
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
                business_type="individual",
                metadata={
                    "user_id": str(user.id),
                    "username": user.username
                }
            )
            user.stripe_account_id = account.id
            user.save()
            StripeConnect.objects.get_or_create(
                landlord=user.id,
                defaults={"stripe_account_id": account.id, "is_active": True}
            )
            return account.id
        except Exception as e:
            logger.error(f"Error creating Stripe Connect account: {e}")
            raise

    @staticmethod
    def create_stripe_price_for_plan(plan):
        print('plan: ', plan)
        """
        Create a Stripe product and price for the given SubscriptionPlan.
        Stores the price ID in plan.stripe_price_id.
        """
        try:
            interval = 'month' if plan.billing_cycle == 'monthly' else 'year'
            currency = (plan.currency_type or 'eur').lower()

            product = stripe.Product.create(
                name=f"Subscription Plan #{plan.id} ({plan.get_billing_cycle_display()})",
                metadata={
                    'model': 'SubscriptionPlan',
                    'plan_id': str(plan.id)
                }
            )

            unit_definitions = [
                ('full_property', plan.full_property, plan.min_units_full_property),
                ('room', plan.room, plan.min_units_room),
                ('bed', plan.bed, plan.min_units_bed),
            ]
            created_price_ids = []

            for unit_name, rate, min_units in unit_definitions:
                if rate is None:
                    continue
                amount_cents = int(rate * 100)
                price = stripe.Price.create(
                    product=product.id,
                    unit_amount=amount_cents,
                    currency=currency,
                    recurring={'interval': interval, 'usage_type': 'licensed'},
                    metadata={
                        'unit_type': unit_name,
                        'min_units': str(min_units or 1)
                    }
                )
                created_price_ids.append(price.id)

            plan.stripe_price_id = ','.join(created_price_ids)
            plan.save(update_fields=['stripe_price_id'])

            return created_price_ids
        except Exception as e:
            logger.error(f"Failed to create Stripe price for plan {plan.id}: {str(e)}")
            raise

    @staticmethod
    def attach_payment_method_to_customer(customer_id, payment_method_id):
        """
        Attach a payment method to a Stripe customer and set it as default.
        """
        try:
            stripe.PaymentMethod.attach(
                payment_method_id,
                customer=customer_id
            )
            stripe.Customer.modify(
                customer_id,
                invoice_settings={"default_payment_method": payment_method_id}
            )
            return {'success': True}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @staticmethod
    def process_webhook_event(payload, sig_header):
        """process stripe webhook event"""
        try:
            event = stripe.Webhook.construct_event(
                payload=payload,
                sig_header=sig_header,
                secret=settings.STRIPE_WEBHOOK_SECRET
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

    @staticmethod
    def list_payment_methods(customer_id):
        try:
            payment_methods = stripe.PaymentMethod.list(
                customer=customer_id,
                type="card"
            )
            return {'success': True, 'payment_methods': payment_methods.data}
        except stripe.error.StripeError as e:
            return {'success': False, 'message': str(e)}
    
    @staticmethod
    def update_payment_method(customer_id, old_payment_method_id, new_payment_method_id):
        try:
            stripe.PaymentMethod.detach(old_payment_method_id)
            stripe.PaymentMethod.attach(
                new_payment_method_id,
                customer=customer_id
            )
            stripe.Customer.modify(
                customer_id,
                invoice_settings={'default_payment_method': new_payment_method_id}
            )
            updated_pm = stripe.PaymentMethod.retrieve(new_payment_method_id)

            return {'success': True, 'payment_method': updated_pm}
        except stripe.error.StripeError as e:
            return {'success': False, 'message': str(e)}

    @staticmethod
    def delete_payment_method(customer_id, payment_method_id):
        try:
            payment_method = stripe.PaymentMethod.retrieve(payment_method_id)

            if payment_method.customer != customer_id:
                return {'success': False, 'message': 'Payment method does not belong to this customer.'}

            stripe.PaymentMethod.detach(payment_method_id)
            return {'success': True}
        except stripe.error.StripeError as e:
            return {'success': False, 'message': str(e)}