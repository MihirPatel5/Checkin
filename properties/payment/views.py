from datetime import timezone
from decimal import Decimal, InvalidOperation
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
    Transaction,
    StripeConnect,
    SubscriptionPlan,
    Upsell,
    UpsellPropertyAssignment
)
from payment.serializers import (
    LandlordSubscriptionSerializer,
    CouponSerializer,
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
from django_filters.rest_framework import DjangoFilterBackend
import logging

logger = logging.getLogger(__name__)

class SubscriptionPlanViewSet(viewsets.ReadOnlyModelViewSet): # ReadOnly viewset for plans
    queryset = SubscriptionPlan.objects.filter(is_active=True)
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [IsAuthenticated]


class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = LandlordSubscription.objects.all()
    serializer_class = LandlordSubscriptionSerializer
    permission_class = [IsAuthenticated]

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        subscription_plan_id = request.data.get('subscription_plan')
        unit_count = serializer.validated_data.get('unit_count', 1)
        payment_method_id = request.data.get('payment_method_id')
        coupon_code = request.data.get('coupon_code')

        subscription_plan = get_object_or_404(SubscriptionPlan, id=subscription_plan_id, is_active=True)

        try:
            result = PaymentService.create_subscription(
                landlord=request.user,
                subscription_plan=subscription_plan,
                unit_count=unit_count,
                payment_method_id=payment_method_id,
                coupon_code=coupon_code
            )
            if result['success']:
                landlord_subscription = LandlordSubscription.objects.get(stripe_subscription_id=result['stripe_subscription_id'])
                return Response(LandlordSubscriptionSerializer(landlord_subscription).data, status=status.HTTP_201_CREATED)
            return Response({"error": result.get('error', _("Failed to create subscription."))}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error creating subscription: {e}")
            return Response({"error": _("An internal error occurred while creating the subscription.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        subscription = self.get_object()
        if not (request.user.is_staff or subscription.landlord == request.user):
            raise PermissionDenied
        try:
            result = PaymentService.cancel_subscription(subscription.stripe_subscription_id)
            if result['success']:
                subscription.status = 'canceled'
                subscription.end_date = timezone.now()
                subscription.save()
                return Response({"status": "canceled", "detail": _("Subscription canceled successfully.")})
            return Response({"error": result.get('error', _("Failed to cancel subscription."))}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error canceling subscription {pk}: {e}")
            return Response({"error": _("An internal error occurred while canceling the subscription.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def update_units(self, request, pk=None):
        subscription = self.get_object()
        if not (request.user.is_staff or subscription.landlord == request.user):
             raise PermissionDenied

        unit_count = request.data.get('unit_count')
        if unit_count is None:
            return Response({"error": _("Unit count is required.")}, status=status.HTTP_400_BAD_REQUEST)
        try:
            unit_count = int(unit_count)
            if unit_count < subscription.subscription_plan.min_units:
                return Response({"error": _("Unit count cannot be less than the minimum allowed for the plan.")}, status=status.HTTP_400_BAD_REQUEST)
            result = PaymentService.update_subscription(
                stripe_subscription_id=subscription.stripe_subscription_id,
                unit_count=unit_count
            )
            if result['success']:
                subscription.unit_count = unit_count
                subscription.save()
                return Response(LandlordSubscriptionSerializer(subscription).data)
            return Response({"error": result.get('error', _("Failed to update subscription units."))}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError:
            return Response({"error": _("Invalid unit count.")}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error updating subscription units for {pk}: {e}")
            return Response({"error": _("An internal error occurred while updating subscription units.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CouponViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Coupon.objects.all()
    serializer_class = CouponSerializer
    permission_classes = [IsAuthenticated]

    #create/update/delete needed, with appropriate permissions
    # @action(detail=False, methods=['post'], permission_classes=[IsAdminUser])
    # def create_coupon(self, request):
    #      serializer = self.get_serializer(data=request.data)
    #      serializer.is_valid(raise_exception=True)
    #      coupon = serializer.save(created_by=request.user)
    #      return Response(self.get_serializer(coupon).data, status=status.HTTP_201_CREATED)


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
            discount_amount = PaymentService.apply_coupon(price, coupon)
            discounted_price = price - discount_amount

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
             raise serializer.ValidationError(_("This landlord already has a Stripe Connect account."))
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
            raise serializer.ValidationError(_("Failed to set up Stripe Connect account."))

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
    filterset_fields = ['is_active', 'currency', 'charge_type', 'price']  # Filter by these fields
    search_fields = ['name', 'description']

    def get_queryset(self):
        qs = Upsell.objects.select_related('landlord')
        if self.request.user.is_staff:
            return qs
        return qs.filter(landlord=self.request.user)

    def perform_create(self, serializer):
        serializer.save(landlord=self.request.user)

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


class CreatePaymentIntentView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        try:
            reservation_id = request.data.get('reservation_id')
            amount = Decimal(request.data.get('amount'))
            coupon_code = request.data.get('coupon_code')
            reservation = Reservation.objects.get(id=reservation_id)
            payment_data = PaymentService.initiate_guest_payment(reservation, amount, coupon_code=coupon_code)
            return Response(payment_data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error creating payment intent: {str(e)}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = LandlordSubscription.objects.all()
    serializer_class = LandlordSubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        property_type = self.request.data.get('property_type')
        billing_cycle = self.request.data.get('billing_cycle')
        unit_count = self.request.data.get('unit_count')
        payment_method_id = self.request.data.get('payment_method_id')
        coupon_code = self.request.data.get('coupon_code')
        subscription = PaymentService.create_subscription(
            landlord=self.request.user,
            property_type=property_type,
            billing_cycle=billing_cycle,
            unit_count=unit_count,
            payment_method_id=payment_method_id,
            coupon_code=coupon_code
        )
        serializer.instance = subscription

class CreateConnectAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        account = StripeService.create_connect_account(user)
        return Response({'account_id':account.id})