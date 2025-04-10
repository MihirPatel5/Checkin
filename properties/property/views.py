import json
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated

from .models import *
from .serializers import PropertySerializer
from utils.translation_services import generate_translations
from utils.ses_validation import generate_ses_xml, send_validation_request


class PropertyListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsLanlordOrAdmin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        properties = Property.objects.prefetch_related('translations').all()
        serializer = PropertySerializer(properties, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


    def post(self, request):
        try:
            original_data = request.data.copy()
            translations_str = original_data.get("translations", "")
            try:
                translations_dict = json.loads(translations_str)
            except json.JSONDecodeError:
                return Response({"error": "Invalid JSON in 'translations' field."}, status=400)
            if not translations_dict:
                return Response({"error": "Missing translation data."}, status=400)
            source_lang, source_fields = list(translations_dict.items())[0]
            full_translations = generate_translations(source_fields, source_lang)
            original_data["translations"] = full_translations
            mutable_data = original_data.dict()
            mutable_data["translations"] = full_translations
            serializer = PropertySerializer(data=original_data, context={"request": request})
            if serializer.is_valid():
                property_instance = serializer.save()
                return Response(
                    PropertySerializer(property_instance, context={"request": request}).data,
                    status=status.HTTP_201_CREATED
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class PropertyDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsLanlordOrAdmin]

    def get_object(self, property_id):
        return get_object_or_404(Property, id=property_id)

    def put(self, request, property_id):
        property_instance = self.get_object(property_id)
        serializer = PropertySerializer(
            property_instance,
            data=request.data,
            partial=True,
            context={"request": request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, property_id):
        property_instance = self.get_object(property_id)
        property_instance.delete()
        return Response({"message": "Property deleted successfully"}, status=status.HTTP_204_NO_CONTENT)


class ConnectSESAPIView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def post(self, request, property_id):
        property_instance = get_object_or_404(Property, id=property_id)
        try:
            property_instance.validate_ses_credentials()
            property_instance.save()
            return Response({"message": "SES Connected Successfully", "ses_status": property_instance.ses_status}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class TestSESConnectionAPIView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request, property_id):
        property_instance = get_object_or_404(Property, id=property_id)

        ws_user = property_instance.ses_user
        ws_password = property_instance.ses_password
        est_code = property_instance.establishment_code
        landlord_code = property_instance.landlord_code

        xml_data = generate_ses_xml(
            ws_user=ws_user,
            ws_password=ws_password,
            est_code=est_code,
            landlord_code=landlord_code,
            tipo_operacion="ALTA"
        )

        success, response_msg = send_validation_request(ws_user, ws_password, xml_data)

        property_instance.ses_status = "SUCCESS" if success else "FAILED"
        property_instance.save()

        return Response({
            "ses_status": property_instance.ses_status,
            "ses_response": response_msg
        }, status=status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST)