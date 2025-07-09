from datetime import timezone
from decimal import Decimal, InvalidOperation
import os, logging, stripe

from amqp import NotFound
from django.shortcuts import render, get_object_or_404
from checkin.models import Reservation
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from django.utils.translation import gettext as _
from payment.models import (
    LandlordSubscription,
    Coupon,
    SubscriptionInvoice,
    Transaction,
    StripeConnect,
    SubscriptionPlan,
    Upsell,
    UpsellPropertyAssignment
)
from payment.serializers import (
    LandlordSubscriptionSerializer,
    CouponSerializer,
    SubscriptionInvoiceSerializer,
    TransactionSerializer,
    StripeConnectSerializer,
    UpsellSerializer,
    SubscriptionPlanSerializer
)
from payment.services.payment_service import PaymentService
from payment.services.stripe_service import StripeService
from property.models import Property
from django.db import transaction as db_transaction
from django.core.exceptions import PermissionDenied
from rest_framework.serializers import ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse

logger = logging.getLogger(__name__)


@csrf_exempt
def stripe_webhook(request):
    """
    Handle Stripe webhook events
    """
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    
    try:
        # Process webhook through StripeService
        StripeService.process_webhook_event(payload, sig_header)
        return HttpResponse(status=200)
    except Exception as e:
        logger.error(f"Webhook processing failed: {str(e)}")
        return HttpResponse(status=400)


class SubscriptionPlanViewSet(viewsets.ModelViewSet): # ReadOnly viewset for plans
    queryset = SubscriptionPlan.objects.filter(is_active=True)
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAuthenticated()]
        return [IsAdminUser()]

    def perform_create(self, serializer):
        plan = serializer.save()
        try:
            price_ids = StripeService.create_stripe_price_for_plan(plan)
            logger.info(f"Created Stripe prices {price_ids} for plan {plan.id}")
        except Exception as e:
            logger.error(f"Stripe price creation failed: {e}")
            raise serializer.ValidationError("Stripe setup failed. Please check the logs.")

    def perform_update(self, serializer):
        prev = SubscriptionPlan.objects.get(pk=serializer.instance.pk)
        plan = serializer.save()
        needs_update = any([
            plan.billing_cycle != prev.billing_cycle,
            plan.currency_type != prev.currency_type,
            plan.full_property != prev.full_property,
            plan.room != prev.room,
            plan.bed != prev.bed
        ])
        if needs_update:
            try:
                price_ids = StripeService.create_stripe_price_for_plan(plan)
                logger.info(f"Updated Stripe prices {price_ids} for plan {plan.id}")
            except Exception as e:
                logger.error(f"Stripe price update failed: {e}")
                raise serializer.ValidationError("Stripe update failed. Please check the logs.")

    def destroy(self, request, *args, **kwargs):
        plan = self.get_object()
        plan.is_active = False
        plan.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)

class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = LandlordSubscription.objects.all()
    serializer_class = LandlordSubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter subscriptions to the current user unless they're staff."""
        if self.request.user.is_staff:
            response = LandlordSubscription.objects.all()
        response = LandlordSubscription.objects.filter(landlord=self.request.user)
        return response
    
    def retrieve(self, request, *args, **kwargs):
        """Retrieve a subscription with real-time sync from Stripe"""
        instance = self.get_object()
        
        if instance.stripe_subscription_id:
            PaymentService.sync_subscription_from_stripe(instance)
            instance.refresh_from_db()
            
        serializer = self.get_serializer(instance)
        return Response(serializer.data)
    
    def create(self, request, *args, **kwargs):
        """Create a new subscription with property type and add-on counts."""
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        subscription_data = {
            'full_property_count': serializer.validated_data.get('full_property_count', 0),
            'room_count': serializer.validated_data.get('room_count', 0),
            'bed_count': serializer.validated_data.get('bed_count', 0),
            'custom_branding_full_property_count': serializer.validated_data.get('custom_branding_full_property_count', 0),
            'custom_branding_room_count': serializer.validated_data.get('custom_branding_room_count', 0),
            'custom_branding_bed_count': serializer.validated_data.get('custom_branding_bed_count', 0),
            'smart_lock_full_property_count': serializer.validated_data.get('smart_lock_full_property_count', 0),
            'smart_lock_room_count': serializer.validated_data.get('smart_lock_room_count', 0),
            'smart_lock_bed_count': serializer.validated_data.get('smart_lock_bed_count', 0),
            'billing_cycle': serializer.validated_data['billing_cycle'],
            'total_price': serializer.validated_data['total_price'],
            'subscription_details': serializer.validated_data.get('subscription_details', {}),
            'payment_method': serializer.validated_data.get('payment_method')
        }
        result = PaymentService.create_subscription(
            landlord=request.user,
            **subscription_data
        )
        if not result.get('success'):
            return Response(
                {'error': result.get('message', 'Subscription creation failed')},
                status=status.HTTP_400_BAD_REQUEST
            )

        subscription = result['subscription']
        if subscription.status == 'active':
            message = 'Subscription created and activated successfully'
        else:
            message = 'Subscription created; awaiting payment confirmation'

        return Response({
            'subscription': self.get_serializer(subscription).data,
            'stripe_subscription_id': result.get('stripe_subscription_id'),
            'client_secret': result.get('client_secret'),
            'status': subscription.status,
            'message': message
        }, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        subscription = self.get_object()
        if not (request.user.is_staff or subscription.landlord == request.user):
            raise PermissionDenied('You do not have permission to cancel the subscription')
        try:
            result = PaymentService.cancel_subscription(subscription)
            if result['success']:
                # PaymentService.sync_subscription_from_stripe(subscription)
                subscription.status = 'canceled'
                subscription.end_date = timezone.now()
                subscription.save(update_fields=['start_date', 'end_date', 'status'])
                return Response({"status": "canceled", "detail": _("Subscription canceled successfully.")})
            return Response({"error": result.get('error', _("Failed to cancel subscription."))}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error canceling subscription {pk}: {e}")
            return Response({"error": _("An internal error occurred while canceling the subscription.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # @action(detail=True, methods=['post'])
    def update(self, request, *args, **kwargs):
        """Update an existing subscription's property type and add-on counts."""
        subscription = self.get_object()
        if not (request.user.is_staff or subscription.landlord == request.user):
            raise PermissionDenied("You do not have permission to update this subscription.")
        serializer = self.get_serializer(subscription, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        full_property_count = serializer.validated_data.get('full_property_count', subscription.full_property_count)
        room_count = serializer.validated_data.get('room_count', subscription.room_count)
        bed_count = serializer.validated_data.get('bed_count', subscription.bed_count)

        custom_branding_full_property_count = serializer.validated_data.get(
            'custom_branding_full_property_count', subscription.custom_branding_full_property_count
        )
        custom_branding_room_count = serializer.validated_data.get(
            'custom_branding_room_count', subscription.custom_branding_room_count
        )
        custom_branding_bed_count = serializer.validated_data.get(
            'custom_branding_bed_count', subscription.custom_branding_bed_count
        )
        smart_lock_full_property_count = serializer.validated_data.get(
            'smart_lock_full_property_count', subscription.smart_lock_full_property_count
        )
        smart_lock_room_count = serializer.validated_data.get(
            'smart_lock_room_count', subscription.smart_lock_room_count
        )
        smart_lock_bed_count = serializer.validated_data.get(
            'smart_lock_bed_count', subscription.smart_lock_bed_count
        )
        billing_cycle = serializer.validated_data.get('billing_cycle', subscription.billing_cycle)

        result = PaymentService.update_subscription(
            subscription=subscription,
            full_property_count=full_property_count,
            room_count=room_count,
            bed_count=bed_count,
            custom_branding_full_property_count=custom_branding_full_property_count,
            custom_branding_room_count=custom_branding_room_count,
            custom_branding_bed_count=custom_branding_bed_count,
            smart_lock_full_property_count=smart_lock_full_property_count,
            smart_lock_room_count=smart_lock_room_count,
            smart_lock_bed_count=smart_lock_bed_count,
            billing_cycle=billing_cycle
        )
        if result['success']:
            PaymentService.sync_subscription_from_stripe(subscription)
            updated_serializer = self.get_serializer(subscription)
            return Response(updated_serializer.data, status=status.HTTP_200_OK)
        else:
            return Response({'error': result['message']}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def sync_status(self, request, pk=None):
        """Manually Sync subscription status from stripe"""
        subscription = self.get_object()
        if not (request.user.is_staff or subscription.landlord == request.user):
            raise PermissionDenied("You do not have permission to sync this subscription")
        try:
            PaymentService.sync_subscription_from_stripe(subscription)
            serializer = self.get_serializer()
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error syncing susbcription {pk}: {e}")
            return Response({'error': _("Failed to sync subscription status.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CouponViewSet(viewsets.ModelViewSet):
    queryset = Coupon.objects.all()
    serializer_class = CouponSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @action(detail=False, methods=['post'])
    def validate(self, request):
        code = request.data.get('code')
        price_str = request.data.get('price')
        if not code or not price_str:
            return Response({"error": _("Coupon code and price are required.")}, status=status.HTTP_400_BAD_REQUEST)
        try:
            price = Decimal(price_str)
            coupon = get_object_or_404(Coupon, code__iexact=code)
            if not coupon.is_valid:
                 return Response({"error": _("Invalid or expired coupon code.")}, status=status.HTTP_400_BAD_REQUEST)
            result = PaymentService.apply_coupon(price, coupon.code)
            if not result.get("success", False):
                return Response(
                    {"error": result.get("message", _("Failed to apply coupon."))},
                    status=status.HTTP_400_BAD_REQUEST
                )
            original_price = result["original_price"]
            discounted_price = result["discounted_price"]
            discount_amount = original_price - discounted_price

            return Response({
                "code": coupon.code,
                "is_valid": True,
                "discount_type": coupon.discount_type,
                "discount_value": coupon.discount_value,
                "discount_amount": discount_amount,
                "discounted_price": discounted_price
            })
        except Coupon.DoesNotExist:
            return Response({"error": _("Invalid coupon code.")}, status=status.HTTP_400_BAD_REQUEST)
        except InvalidOperation:
             return Response({"error": _("Invalid price format.")}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error validating coupon {code}: {e}")
            return Response({"error": _("An internal error occurred while validating the coupon.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Transaction.objects.all().select_related('reservation', 'check_in', 'landlord', 'guest_user')
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['status', 'transaction_type', 'currency']
    search_fields = ['description', 'guest_email']

    def get_queryset(self):
        if self.request.user.is_staff:
             return Transaction.objects.all().select_related('reservation', 'check_in', 'landlord', 'guest_user')
        return Transaction.objects.filter(landlord=self.request.user).select_related('reservation', 'check_in', 'landlord', 'guest_user')

class StripeConnectViewSet(viewsets.ModelViewSet):
    queryset = StripeConnect.objects.all().select_related('landlord')
    serializer_class = StripeConnectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:
             return StripeConnect.objects.all().select_related('landlord')
        return StripeConnect.objects.filter(landlord=self.request.user).select_related('landlord')

    def perform_create(self, serializer):
        if hasattr(self.request.user, 'stripe_connect_account'):
             raise ValidationError(_("This landlord already has a Stripe Connect account."))
        try:
            stripe_account_id = serializer.validated_data['stripe_account_id']
            guest_pays_fee = serializer.validated_data.get('guest_pays_fee', True)
            result = PaymentService.setup_stripe_connect(
                self.request.user,
                stripe_account_id,
                guest_pays_fee
            )
            stripe_connect_account = serializer.save(landlord=self.request.user, is_active=True)
            return stripe_connect_account

        except Exception as e:
            logger.error(f"Error setting up Stripe Connect for {self.request.user}: {e}")
            raise ValidationError(_("Failed to set up Stripe Connect account."))

    def perform_update(self, serializer):
        if not (self.request.user.is_staff or serializer.instance.landlord == self.request.user):
            raise PermissionDenied
        serializer.save()

    def perform_destroy(self, instance):
        if not (self.request.user.is_staff or instance.landlord == self.request.user):
            raise PermissionDenied
        try:
            PaymentService.disconnect_stripe_account(instance.stripe_account_id)
        except Exception as e:
            logger.error(f"Failed to disconnect Stripe account {instance.stripe_account_id}: {e}")
            pass
        instance.delete()


class UpsellViewSet(viewsets.ModelViewSet):
    serializer_class = UpsellSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['is_active', 'currency', 'charge_type']
    search_fields = ['name', 'description']

    def get_queryset(self):
        qs = Upsell.objects.select_related('landlord')
        if self.request.user.is_staff:
            return qs
        return qs.filter(landlord=self.request.user)

    def perform_create(self, serializer):
        serializer.save(landlord=self.request.user, is_active=True)

    def perform_update(self, serializer):
        if not (self.request.user.is_staff or serializer.instance.landlord == self.request.user):
            raise PermissionDenied("You do not have permission to update this upsell.")
        serializer.save()

    def perform_destroy(self, instance):
        if not (self.request.user.is_staff or instance.landlord == self.request.user):
            raise PermissionDenied("You do not have permission to delete this upsell.")
        instance.delete()


    @action(detail=True, methods=['post'])
    def assign_properties(self, request, pk=None):
        upsell = self.get_object()
        upsell.is_active = True
        upsell.save()
        if not (request.user.is_staff or upsell.landlord == request.user):
            raise PermissionDenied("You do not have permission to assign properties to this upsell.")

        property_ids = request.data.get('property_ids', [])
        if not isinstance(property_ids, list):
            return Response({"error": _("property_ids must be a list.")}, status=status.HTTP_400_BAD_REQUEST)

        allowed_properties = Property.objects.all()
        if not request.user.is_staff:
            allowed_properties = allowed_properties.filter(owner=request.user)

        valid_properties = allowed_properties.filter(id__in=property_ids)
        valid_ids = set(str(p.id) for p in valid_properties)
        invalid_ids = [pid for pid in property_ids if str(pid) not in valid_ids]

        if invalid_ids:
            return Response({
                "error": _("Invalid or unauthorized property IDs: %(ids)s") % {'ids': ', '.join(map(str, invalid_ids))}
            }, status=status.HTTP_400_BAD_REQUEST)

        with db_transaction.atomic():
            UpsellPropertyAssignment.objects.filter(upsell=upsell).delete()
            assignments = [
                UpsellPropertyAssignment(upsell=upsell, property_ref=prop)
                for prop in valid_properties
            ]
            UpsellPropertyAssignment.objects.bulk_create(assignments)

        return Response({'assigned_property_count': len(assignments)})

    @action(detail=True, methods=['get'])
    def assigned_properties(self, request, pk=None):
        upsell = self.get_object()
        if not (request.user.is_staff or upsell.landlord == request.user):
            raise PermissionDenied("You do not have permission to view assigned properties.")

        assigned_props = upsell.property_assignments.select_related('property_ref__owner')
        property_data = [{
            'id': str(assignment.property_ref.id),
            'name': assignment.property_ref.name,
        } for assignment in assigned_props]

        return Response({'assigned_properties': property_data})


class SubscriptionInvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SubscriptionInvoice.objects.all()
    serializer_class = SubscriptionInvoiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        subscription_id = self.request.query_params.get('subscription')
        if subscription_id:
            qs = qs.filter(subscription_id=subscription_id)
        return qs
        
class CreatePaymentIntentView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        try:
            reservation_id = request.data.get('reservation_id')
            amount = request.data.get('amount')
            coupon_code = request.data.get('coupon_code')
            
            if not reservation_id or not amount:
                return Response({'error': 'reservation_id and amount are required'}, status=status.HTTP_400_BAD_REQUEST)
            amount = Decimal(str(amount))
            reservation = get_object_or_404(Reservation, id=reservation_id)
            payment_data = PaymentService.initiate_guest_payment(
                reservation=reservation, 
                amount=amount, 
                coupon_code=coupon_code
            )
            return Response(payment_data, status=status.HTTP_200_OK)
        except Reservation.DoesNotExist:
            return Response({'error': 'Reservation not found'}, status=status.HTTP_404_NOT_FOUND)
        except (ValueError, InvalidOperation):
            return Response({'error': 'Invalid amount format'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error creating payment intent: {str(e)}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CreateConnectAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if hasattr(user, 'stripe_connect_account'):
            return Response({'error': 'User already has a Stripe Connect account'}, 
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            account = StripeService.create_connect_account(user)
            user.stripe_account_id = account.id
            user.save(update_fields=["stripe_account_id"])
            StripeConnect.objects.create(
                landlord=user,
                stripe_account_id=account.id,
                guest_pays_fee=True,  # default
                is_active=True
            )
            return Response({'account_id': account.id}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error creating Stripe Connect account: {str(e)}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CreateStripeConnectAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            user = request.user
            account_id = StripeService.create_express_account(user)
            if not user.stripe_customer_id:
                cust = StripeService.create_customer(
                    user,
                    metadata={"user_id": user.id, "username": user.username}
                )
                if not cust:
                    logger.info(f"Skipped creating customer for user {user.id}")
            base = os.getenv("NEXT_PUBLIC_BASE_URL", "http://localhost:3000")
            onboarding_url = StripeService.create_account_link(
                account_id=account_id,
                refresh_url=f"{base}/dashboard?refresh=true",
                return_url=f"{base}/payment/account?success=true",
            )

            return Response({"url": onboarding_url, "accountId": account_id})

        except Exception as e:
            logger.error(f"Failed to create Stripe Connect onboarding link: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AttachPaymentMethodView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        payment_method_id = request.data.get("payment_method_id")
        if not payment_method_id:
            return Response({'error': 'Payment method ID is required.'}, status=status.HTTP_400_BAD_REQUEST)
        customer_id = request.user.stripe_customer_id
        if not customer_id:
            return Response({'error': 'User has no Stripe customer ID.'}, status=status.HTTP_400_BAD_REQUEST)

        result = StripeService.attach_payment_method_to_customer(customer_id, payment_method_id)
        if result['success']:
            return Response({'message': 'Payment method attached successfully.'}, status=status.HTTP_200_OK)
        else:
            return Response({'error': result['message']}, status=status.HTTP_400_BAD_REQUEST)


class ListPaymentMethodsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        customer_id = request.user.stripe_customer_id
        if not customer_id:
            return Response(
                {'error': 'User has no Stripe customer ID.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        result = StripeService.list_payment_methods(customer_id)

        if not result['success']:
            return Response(
                {'error': result.get('message', 'Failed to retrieve payment methods')},
                status=status.HTTP_400_BAD_REQUEST
            )
        payment_methods = result['payment_methods']
        
        serialized = [
            {
                'id': pm.id,
                'brand': pm.card.brand,
                'last4': pm.card.last4,
                'exp_month': pm.card.exp_month,
                'exp_year': pm.card.exp_year,
            }
            for pm in payment_methods
        ]
        
        return Response({'payment_methods': serialized}, status=status.HTTP_200_OK)


class ManagePaymentMethodView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        user = request.user
        customer_id = getattr(user, 'stripe_customer_id', None)
        if not customer_id:
            return Response(
                {'error': 'No Stripe customer ID on the user.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        old_pm = request.data.get('old_payment_method_id')
        new_pm = request.data.get('new_payment_method_id')
        if not old_pm or not new_pm:
            return Response(
                {'error': 'Both old_payment_method_id and new_payment_method_id are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        result = StripeService.update_payment_method(customer_id, old_pm, new_pm)
        if not result['success']:
            return Response({'error': result['message']}, status=status.HTTP_400_BAD_REQUEST)

        pm = result['payment_method']
        response_data = {
            'id': pm.id,
            'brand': pm.card.brand,
            'last4': pm.card.last4,
            'exp_month': pm.card.exp_month,
            'exp_year': pm.card.exp_year,
        }
        if pm.billing_details:
            addr = pm.billing_details.address or {}
            response_data['billing_details'] = {
                'name':pm.billing_details.name,
                'email':pm.billing_details.email,
                'phone':pm.billing_details.phone,
                'address': {
                    'line1':addr.line1,
                    'line2':addr.line2,
                    'city':addr.city,
                    'state':addr.state,
                    'postal_code':addr.postal_code,
                    'country':addr.country,
                } if addr else None
            }

        return Response({
            'message': 'Payment method swapped successfully.',
            'payment_method': response_data
        }, status=status.HTTP_200_OK)

    def delete(self, request):
        payment_method_id = request.data.get('payment_method_id')
        if not payment_method_id:
            return Response({'error': 'Payment method ID is required.'}, status=status.HTTP_400_BAD_REQUEST)

        customer_id = request.user.stripe_customer_id
        if not customer_id:
            return Response({'error': 'User has no Stripe customer ID.'}, status=status.HTTP_400_BAD_REQUEST)

        result = StripeService.delete_payment_method(customer_id, payment_method_id)

        if result['success']:
            return Response({'message': 'Payment method removed successfully.'}, status=status.HTTP_200_OK)
        else:
            return Response({'error': result['message']}, status=status.HTTP_400_BAD_REQUEST)