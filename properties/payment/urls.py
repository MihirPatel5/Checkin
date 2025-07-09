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
    CreateConnectAccountView,
    CreateStripeConnectAccountView,
    stripe_webhook,
    AttachPaymentMethodView,
    ListPaymentMethodsView,
    ManagePaymentMethodView,
    SubscriptionInvoiceViewSet
)

# Router for ViewSets
router = DefaultRouter()
router.register(r'subscription-plans', SubscriptionPlanViewSet, basename='subscription-plan')
router.register(r'subscriptions', SubscriptionViewSet, basename='subscription')
router.register(r'coupons', CouponViewSet, basename='coupon')
router.register(r'transactions', TransactionViewSet, basename='transaction')
router.register(r'stripe-connect', StripeConnectViewSet, basename='stripe-connect')
router.register(r'upsells', UpsellViewSet, basename='upsell')
router.register(r'invoices', SubscriptionInvoiceViewSet, basename='invoice')  # Add this


urlpatterns = [
    path('', include(router.urls)),
    path('webhook/stripe/', stripe_webhook, name='stripe-webhook'),
    path('create-payment-intent/', CreatePaymentIntentView.as_view(), name='create-payment-intent'),
    path("stripe/connect/", CreateStripeConnectAccountView.as_view(), name="stripe-connect"),
    path('create-stripe-account/', CreateConnectAccountView.as_view(), name='create-stripe-account'),
    path('attach-payment-method/', AttachPaymentMethodView.as_view(), name='attach-payment-method'),
    path('list-saved-cards/', ListPaymentMethodsView.as_view()),
    path('manage-saved-cards/', ManagePaymentMethodView.as_view())

]
