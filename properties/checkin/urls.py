from django.urls import path, include, re_path
from rest_framework.routers import DefaultRouter
from .views import (
    ReservationListCreateAPIView,
    CheckInCreateAPIView,
    ReservationSearchAPIView,
    ReservationRetrieveUpdateDestroyAPIView,
    PublicCheckInAPIView,
    PropertyICalViewSet,
    DataRetentionPolicyAPIView,
    PoliceSubmissionLogViewSet,
    PoliceSubmissionRetryAPIView,
    DailyPoliceReportAPIView,
    GuestListCreateView,
    GuestRetrieveUpdateView,
    CompleteCheckInView,
)

router = DefaultRouter()
router.register(r'police-submissions', PoliceSubmissionLogViewSet, basename='police-submission')

urlpatterns = [
    path(
        'reservations/', 
        ReservationListCreateAPIView.as_view(),
        name='reservation-list-create'
    ),
    path(
        'reservations/<str:reservation_code>/',
        ReservationRetrieveUpdateDestroyAPIView.as_view(),
        name='reservation-detail'
    ),
    path(
        'checkin/<str:check_in_link>/guests/',
        GuestListCreateView.as_view(),
        name='guest-create'
    ),
    path(
        'checkin/<str:check_in_link>/guests/<str:pk>/',
        GuestRetrieveUpdateView.as_view(),
        name='guest-retrieve-update'
    ),
    path(
        'checkin/<str:check_in_link>/complete/',
        CompleteCheckInView.as_view(),
        name='checkin-complete'
    ),
    path(
        'reservation/search/',
        ReservationSearchAPIView.as_view(),
        name='reservation-search'
    ),
    path(
        'checkin/<str:check_in_link>/',
        CheckInCreateAPIView.as_view(),
        name='checkin-create'
    ),
    path(
        'public/checkin/<str:check_in_link>/', 
        PublicCheckInAPIView.as_view(), 
        name='public-checkin-detail'
    ),
    path(
        'properties/<uuid:property_id>/ical/',
        PropertyICalViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='property-ical-list-create'
    ),
    path(
        'properties/<uuid:property_id>/ical/<int:pk>/',
        PropertyICalViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='property-ical-detail'
    ),
    path(
        'properties/<uuid:property_id>/data-retention/',
        DataRetentionPolicyAPIView.as_view(),
        name='data-retention-policy'
    ),
    path('', include(router.urls)),
    path(
        'police-submissions/<uuid:submission_id>/retry/', 
        PoliceSubmissionRetryAPIView.as_view(), 
        name='police-submission-retry'
    ),
    path(
        'police-report/daily/', 
        DailyPoliceReportAPIView.as_view(), 
        name='daily-police-report'
    ),

]