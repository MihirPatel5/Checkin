from django.urls import path
from .views import (
    ReservationCreateAPIView,
    CheckInCreateAPIView,
    ReservationSearchAPIView,
    PropertyICalViewSet
)

urlpatterns = [
    path(
        'reservations/', 
        ReservationCreateAPIView.as_view()
    ),
    path(
        'checkin/<str:check_in_link>/', 
        CheckInCreateAPIView.as_view()
    ),
    path(
        'ical/<uuid:property_id>/', 
        PropertyICalViewSet.as_view({'get': 'list', 'post': 'create'})
    ),
    path(
        'search/', 
        ReservationSearchAPIView.as_view()
    ),

]