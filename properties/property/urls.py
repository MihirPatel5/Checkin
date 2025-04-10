from django.urls import path
from .views import (
    PropertyListCreateAPIView,
    PropertyDetailAPIView,
    ConnectSESAPIView,
    TestSESConnectionAPIView
)

urlpatterns = [
    path("properties/", PropertyListCreateAPIView.as_view(), name="property-list-create"),
    path("properties/<int:property_id>/", PropertyDetailAPIView.as_view(), name="property-detail"),
    path("properties/<int:property_id>/connect-ses/", ConnectSESAPIView.as_view(), name="connect-ses"),
    path("properties/<int:property_id>/test-connection/", TestSESConnectionAPIView.as_view(), name="test-ses"),
]