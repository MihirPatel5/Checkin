from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SubscriptionPlanViewSet,
    SubscriptionViewSet,
    CouponViewSet,
    TransactionViewSet,
    StripeConnectViewSet,
    UpsellViewSet,
    CreatePaymentIntentView,
    CreateConnectAccountView
)

# Router for ViewSets
router = DefaultRouter()
router.register(r'subscription-plans', SubscriptionPlanViewSet, basename='subscription-plan')
router.register(r'subscriptions', SubscriptionViewSet, basename='subscription')
router.register(r'coupons', CouponViewSet, basename='coupon')
router.register(r'transactions', TransactionViewSet, basename='transaction')
router.register(r'stripe-connect', StripeConnectViewSet, basename='stripe-connect')
router.register(r'upsells', UpsellViewSet, basename='upsell')


urlpatterns = [
    path('', include(router.urls)),
    path('create-payment-intent/', CreatePaymentIntentView.as_view(), name='create-payment-intent'),
    path('create-stripe-account/', CreateConnectAccountView.as_view(), name='create-stripe-account')
]
