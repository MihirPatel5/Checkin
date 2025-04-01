from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from authentication.models import User
from .models import Property, IsSuperAdmin
from .serializers import PropertySerializer


class PropertyAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk=None):
        language = request.GET.get("language", request.LANGUAGE_CODE)
        if pk:
            try:
                property_instance = (
                    Property.objects.language(language)
                    .prefetch_related("images")
                    .get(pk=pk)
                )
                serializer = PropertySerializer(
                    property_instance, context={"request": request}
                )
                return Response(serializer.data, status=status.HTTP_200_OK)
            except Property.DoesNotExist:
                return Response(
                    {"error": "Property not found"}, status=status.HTTP_404_NOT_FOUND
                )
        properties = Property.objects.language(language).all()
        serializer = PropertySerializer(
            properties, many=True, context={"request": request}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        if not request.user.role in [User.LANDLORD, User.ADMIN, User.SUPERADMIN]:
            return Response(
                {"error": "Permission Denied"}, status=status.HTTP_403_FORBIDDEN
            )
        serializer = PropertySerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            property_instance = serializer.save(owner=request.user)
            property_instance.validate_ses_credentials()
            property_instance.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def put(self, request, pk):
        try:
            property_instance = Property.objects.get(pk=pk)
        except Property.DoesNotExist:
            return Response(
                {"error": "Property not Found"}, status=status.HTTP_404_NOT_FOUND
            )
        if (request.user != property_instance.owner and 
            request.user.role not in ["Admin", "SuperAdmin"]):
            return Response(
                {"error": "Permission Denied"}, status=status.HTTP_403_FORBIDDEN
            )
        serializer = PropertySerializer(
            property_instance,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        if serializer.is_valid():
            try:
                property_instance = serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
            except ValidationError as e:
                return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        try:
            property_instance = Property.objects.get(pk=pk)
        except Property.DoesNotExist:
            return Response(
                {"error": "Property not found"}, status=status.HTTP_404_NOT_FOUND
            )
        if (request.user != property_instance.owner and 
            request.user.role not in ["Admin", "SuperAdmin"]):
            return Response(
                {"error": "Permission Denied"}, status=status.HTTP_403_FORBIDDEN
            )
        property_instance.delete()
        return Response(
            {"message": "Property deleted successfully"},
            status=status.HTTP_204_NO_CONTENT,
        )


class ValidateSESCredentialsAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        try:
            property_instance = Property.objects.get(pk=pk)
            property_instance.validate_ses_credentials()
            property_instance.save()
            return Response(
                {"message": "SES Credentials are valid."}, 
                status=status.HTTP_200_OK
            )
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Property.DoesNotExist:
            return Response(
                {"error": "Property not found"}, status=status.HTTP_404_NOT_FOUND
            )