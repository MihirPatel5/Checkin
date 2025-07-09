from django.shortcuts import get_object_or_404
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated, AllowAny

from .models import Property
from .serializers import PropertySerializer
from property.permissions import IsAdminOrSuperAdmin, IsLandlordOrAdminOrSuperAdmin, IsOwnerOrAdmin
from utils.translation_services import generate_translations
from utils.ses_validation import generate_ses_xml, send_validation_request
import json


def process_activities_data(original_data):
    activities_data = []
    if "activities_data" in original_data and isinstance(original_data["activities_data"], str):
        try:
            activities_data = json.loads(original_data["activities_data"])
        except json.JSONDecodeError:
            activities_data = []
    elif hasattr(original_data, 'getlist') or isinstance(original_data, dict):
        activities_dict = {}
        for key in original_data:
            if key.startswith('activities_data[') and '][' in key:
                try:
                    index = int(key[len('activities_data['):].split(']')[0])
                    field = key.split('][')[1].rstrip(']')
                    if index not in activities_dict:
                        activities_dict[index] = {}
                    activities_dict[index][field] = original_data[key]
                except (ValueError, IndexError):
                    continue
        activities_data = [data for _, data in sorted(activities_dict.items())]
    return activities_data


class PropertyListCreateAPIView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        """
        Return appropriate permissions based on the HTTP method.
        - GET: Allow anyone to view properties
        - POST: Only landlords, admins, and superadmins can create properties
        """
        if self.request.method == 'GET':
            return [AllowAny()]
        elif self.request.method == 'POST':
            return [IsAuthenticated(), IsLandlordOrAdminOrSuperAdmin()]
        return [IsAuthenticated()]

    def get(self, request):
        queryset = Property.objects.prefetch_related('translations').all()
        search_query = request.query_params.get('search', '')
        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query) |
                Q(address__icontains=search_query) |
                Q(property_type__icontains=search_query) |
                Q(property_reference__icontains=search_query) |
                Q(city__icontains=search_query)
            ).distinct()
        property_types_param = request.query_params.get('property_type')
        if property_types_param:
            property_types = [ptype.strip().lower() for ptype in property_types_param.split(',')]
            queryset = queryset.filter(property_type__in=property_types)
        serializer = PropertySerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


    def post(self, request):
        try:
            original_data = request.data.copy()
            translations_str = original_data.get("translations", "")
            try:
                translations_dict = json.loads(translations_str) if isinstance(translations_str, str) else translations_str
            except json.JSONDecodeError:
                return Response({"error": "Invalid JSON in 'translations' field."}, status=400)
            if not translations_dict:
                return Response({"error": "Missing translation data."}, status=400)
            source_lang, source_fields = list(translations_dict.items())[0]
            full_translations = generate_translations(source_fields, source_lang)
            
            activities_data = []
            if "activities_data" in original_data and isinstance(original_data["activities_data"], str):
                try:
                    activities_data = json.loads(original_data["activities_data"])
                except json.JSONDecodeError:
                    activities_data = []
            elif hasattr(original_data, 'getlist') or isinstance(original_data, dict):
                activities_dict = {}
                for key in original_data:
                    if key.startswith('activities_data[') and '][' in key:
                        try:
                            index = int(key[len('activities_data['):].split(']')[0])
                            field = key.split('][')[1].rstrip(']')
                            if index not in activities_dict:
                                activities_dict[index] = {}
                            activities_dict[index][field] = original_data[key]
                        except (ValueError, IndexError):
                            continue
                activities_data = [data for _, data in sorted(activities_dict.items())]

            mutable_data = {}
            for key in original_data:
                if key not in ['translations', 'activities_data']:
                    if key in ['image', 'upsell_ids'] and hasattr(original_data, 'getlist'):
                        mutable_data[key] = original_data.getlist(key)
                    else:
                        mutable_data[key] = original_data[key]
            mutable_data["translations"] = full_translations
            mutable_data["activities_data"] = activities_data
            serializer = PropertySerializer(data=mutable_data, context={"request": request})
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
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated(), IsOwnerOrAdmin()]

    def get_object(self, property_id):
        return get_object_or_404(Property, id=property_id)
    
    def get(self, request, property_id):
        property_instance = self.get_object(property_id)
        serializer = PropertySerializer(property_instance, context={'request': request})
        return Response(serializer.data)

    def put(self, request, property_id):
        try:
            property_instance = self.get_object(property_id)
            original_data = request.data.copy()
            
            activities_data = process_activities_data(original_data)
            mutable_data = {}
            for key in original_data:
                if key not in ['activities_data']:
                    if key in ['image', 'upsell_ids'] and hasattr(original_data, 'getlist'):
                        mutable_data[key] = original_data.getlist(key)
                    else:
                        mutable_data[key] = original_data[key]
            mutable_data["activities_data"] = activities_data
            serializer = PropertySerializer(
                property_instance,
                data=mutable_data,
                partial=True,
                context={"request": request}
            )
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def delete(self, request, property_id):
        property_instance = self.get_object(property_id)
        property_instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MultipleDeletePropertyAPIView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]

    def delete(self, request):
        property_ids = request.data.get("property_ids")
        if not property_ids:
            return Response({"error": "No property IDs provided"}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(property_ids, list):
            return Response({"error": "property_ids must be a list"}, status=status.HTTP_400_BAD_REQUEST)
        deleted_count = 0
        errors = []
        for prop_id in property_ids:
            try:
                property_instance = get_object_or_404(Property, id=prop_id)
                property_instance.delete()
                deleted_count += 1
            except Exception as e:
                errors.append({"property_id": prop_id, "error": str(e)})
        return Response({
            "message": f"{deleted_count} properties deleted successfully",
            "errors": errors if errors else None
        }, status=status.HTTP_207_MULTI_STATUS if errors else status.HTTP_204_NO_CONTENT)


class ConnectSESAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def post(self, request, property_id):
        property_instance = get_object_or_404(Property, id=property_id)
        try:
            property_instance.validate_ses_credentials()
            property_instance.save()
            return Response({"message": "SES Connected Successfully", "ses_status": property_instance.ses_status}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class TestSESConnectionAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

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