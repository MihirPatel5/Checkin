from django.urls import path
from .views import (
    GenerateCheckInLinkAPIView,
    CheckInByLinkAPIView,
    SubmitCheckInAPIView,
    MunicipalitySearchAPIView,
    CheckInListByPropertyView,
    GenerateDailyReportAPIView
)

urlpatterns = [
    path(
        'reservations/generate-link/',
        GenerateCheckInLinkAPIView.as_view(),
        name='generate-checkin-link'
    ),
    path(
        'checkin/<str:link_id>/',
        CheckInByLinkAPIView.as_view(),
        name='checkin-by-link'
    ),
    path(
        'checkin/<str:link_id>/submit/',
        SubmitCheckInAPIView.as_view(),
        name='submit-checkin'
    ),
    path(
        'municipalities/',
        MunicipalitySearchAPIView.as_view(),
        name='municipality-search'
    ),
    path(
        'properties/<uuid:property_id>/checkins/',
        CheckInListByPropertyView.as_view(),
        name='property-checkins-list'
    ),
    path(
        'reports/daily-checkins/',
        GenerateDailyReportAPIView.as_view(),
        name='daily-checkins-report'
    ),
]