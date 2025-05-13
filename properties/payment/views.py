from decimal import Decimal
from django.shortcuts import render, get_object_or_404
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from payment.models import (
    LandlordSubscription,
    Coupon,
    Transaction,
    StripeConnect,
    Upsell
)
from payment.serializers import (
    LandlordSubscriptionSerializer,
    CouponSerializer,
    TransactionSerializer,
    StripeConnectSerializer,
    UpsellSerializer
)
from payment.services.payment_service import PaymentService


class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = LandlordSubscription.objects.all()
    serializer_class = LandlordSubscriptionSerializer

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = PaymentService.create_subscription(
            landlord=request.user,
            property_type=request.data.get('property_type'),
            billing_cycle=request.data.get('billing_cycle'),
            unit_count=request.data.get('unit_count'),
            payment_method_id=request.data.get('payment_method_id'),
            coupon_code=request.data.get('coupon_code')
        )
        if result['success']:
            return Response(result, status=status.HTTP_201_CREATED)
        return Response(result, status=status.HTTP_400_BAD_REQUEST)
    
    @action(details=True, method=['post'])
    def cancel(self, request, pk=None):
        subscription = self.get_object()
        result = PaymentService.cancel_subscription(subscription.id)
        return Response(result)
    
    @action(detail=True, method=['post'])
    def update_units(self, request, pk=None):
        subscription = self.get_object()
        unit_count = request.data.get('unit_count')
        result = PaymentService.update_subscription(
            subscription_id=subscription.id,
            unit_count=unit_count
        )
        return Response(request)


class CouponViewset(viewsets.ModelViewset):
    queryset = Coupon.objects.all()
    serializer_class = CouponSerializer

    @action(detail=False, methods=['post'])
    def validate(self, request):
        code = request.data.get('code')
        price = Decimal(request.data.get('price'))
        result = PaymentService.apply_coupon(price, code)
        return Response(result)


class TransactionViewSet(viewsets.ModelViewset):
    queryset = Transaction.objects.all()
    serializer_class = TransactionSerializer

    @action(detail=True, methods=['post'])
    def process_payment(self, request, pk=None):
        transaction = self.get_object()
        result = PaymentService.process_transaction_payment(
            transaction_id=transaction.id,
            payment_method_id=request.data.get('payment_method_id')
        )
        return Response(result)


class StripeConnectViewSet(viewsets.ModelViewSet):
    queryset = StripeConnect.objects.all()
    serializer_class = StripeConnectSerializer

    def perform_create(self, serializer):
        result = PaymentService.setup_stripe_connect(
            self.request.user,
            serializer.validated_data['stripe_account_id'],
            serializer.validated_data.get('guest_pays_fee', True)
        )
        return Response(StripeConnectSerializer(result).data)


class UpsellViewSet(viewsets.ModelViewSet):
    queryset = Upsell.objects.all()
    serialzer_class = UpsellSerializer

    @action(detail=True, methods=['post'])
    def assign_properties(self, request, pk=None):
        upsell = self.get_object()
        property_ids = request.data.get('property_ids', [])
        count = PaymentService.assign_upsell_to_properties(upsell.id, property_ids)
        return Response({'assigned_properties': count})