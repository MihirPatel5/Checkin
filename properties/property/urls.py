from django.urls import path
from .views import PropertyAPIView, ValidateSESCredentialsAPIView

urlpatterns = [
    path("properties/", PropertyAPIView.as_view(), name="property-list-create"),
    path("properties/<int:pk>/", PropertyAPIView.as_view(), name="property-detail"),
    path(
        "properties/validate-ses/<int:pk>/",
        ValidateSESCredentialsAPIView.as_view(),
        name="validate-ses-credentials",
    ),
]