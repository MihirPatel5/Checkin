from rest_framework import generics, status, permissions, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.core.exceptions import PermissionDenied
from .models import CheckIn, Municipality, CheckInStatus, GuardianRelationship
from .serializers import (
    CheckInCreateSerializer, CheckInSubmitSerializer, CheckInLinkSerializer,
    CheckInDetailSerializer, MunicipalitySerializer
)
from property.models import Property
from .utils import send_checkin_confirmation, send_checkin_link_email, SESHospedajesService
import logging
import shortuuid

logger = logging.getLogger(__name__)

class GenerateCheckInLinkAPIView(APIView):
    permission_classes = []

    def post(self, request):
        try:
            property_id = request.data.get('property_id')
            property = get_object_or_404(Property, id=property_id)
            # if not (request.user.is_staff or property.owner == request.user):
            #     return Response(
            #         {"error": "You don't have permission for this property"},
            #         status=status.HTTP_403_FORBIDDEN
            #     )
            check_in = CheckIn.objects.create(
                property_ref=property,
                lead_guest_name=request.data.get('lead_guest_name'),
                lead_guest_email=request.data.get('lead_guest_email'),
                lead_guest_phone=request.data.get('lead_guest_phone'),
                check_in_date=request.data.get('check_in_date'),
                check_out_date=request.data.get('check_out_date'),
                total_guests=request.data.get('total_guests', 1),
                # reservation_id=reservation_id,
                auto_submit_to_police=property.country == 'ES'
            )
            send_checkin_link_email(check_in, check_in.lead_guest_email, check_in.lead_guest_name)
            serializer = CheckInLinkSerializer(check_in)
            return Response({
                "check_in_link": check_in.check_in_link,
                "check_in_url": serializer.data['check_in_url'],
                "message": "Check-in link generated successfully",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"Error generating check-in link: {str(e)}")
            return Response({"error": f"Failed to generate check-in link: {str(e)}"},
                          status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class CheckInByLinkAPIView(APIView):
    permission_classes = []
    
    def get(self, request, link_id):
        try:
            check_in = get_object_or_404(CheckIn, check_in_link=link_id)
            municipalities = []
            if check_in.property_ref.country == 'ES':
                municipalities = Municipality.objects.all().order_by('nombre_municipio')
            
            serializer = CheckInDetailSerializer(check_in)
            municipality_serializer = MunicipalitySerializer(municipalities, many=True)
            return Response({
                'check_in': serializer.data,
                'municipalities': municipality_serializer.data if municipalities else None,
                'is_spanish_property': check_in.property_ref.country == 'ES'
            })
        except Exception as e:
            logger.error(f"Error getting check-in by link: {str(e)}")
            return Response({"error": f"Failed to get check-in: {str(e)}"},
                          status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SubmitCheckInAPIView(APIView):
    permission_classes = []
    
    def post(self, request, link_id):
        try:
            check_in = get_object_or_404(CheckIn, check_in_link=link_id)
            
            if check_in.status in [CheckInStatus.SUBMITTED, CheckInStatus.CONFIRMED]:
                return Response({"error": "Check-in has already been submitted or confirmed"},
                              status=status.HTTP_400_BAD_REQUEST)
            context = {'property_country': check_in.property_ref.country}
            serializer = CheckInSubmitSerializer(check_in, data=request.data, context=context, partial=True)
            if serializer.is_valid():
                check_in = serializer.save()
                if check_in.property_ref.country == 'ES' and check_in.is_complete:
                    check_in.status = CheckInStatus.CONFIRMED
                    check_in.save()
                    
                    if check_in.auto_submit_to_police:
                        service = SESHospedajesService()
                        print('service: ', service)
                        service.submit_check_in(check_in)
                return Response({
                    "message": "Check-in submitted successfully",
                    "status": check_in.status,
                    "redirect_url": f"/property/{check_in.property_ref.id}/landing"  # Redirect to property landing page
                })
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            logger.error(f"Error submitting check-in: {str(e)}")
            return Response({"error": f"Failed to submit check-in: {str(e)}"},
                          status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class GenerateDailyReportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_staff:
            return Response({"error": "You don't have permission to generate reports"},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            tomorrow = timezone.now() + timezone.timedelta(days=1)
            tomorrow_start = timezone.datetime.combine(tomorrow.date(), timezone.datetime.min.time())
            tomorrow_end = timezone.datetime.combine(tomorrow.date(), timezone.datetime.max.time())
            pending_checkins = CheckIn.objects.filter(
                auto_submit_to_police=True,
                status=CheckInStatus.CONFIRMED,
                submission_date__isnull=True,
                check_in_date__range=(tomorrow_start, tomorrow_end)
            )
            service = SESHospedajesService()
            results = {
                'total': pending_checkins.count(),
                'success': 0,
                'failed': 0,
                'errors': []
            }
            for check_in in pending_checkins:
                if check_in.is_complete:
                    result = service.submit_check_in(check_in)
                    if result.get('success'):
                        results['success'] += 1
                    else:
                        results['failed'] += 1
                        results['errors'].append({
                            'check_in_id': str(check_in.id),
                            'error': result.get('error')
                        })
                else:
                    results['failed'] += 1
                    results['errors'].append({
                        'check_in_id': str(check_in.id),
                        'error': 'Check-in is incomplete'
                    })
            return Response(results)
        except Exception as e:
            logger.error(f"Error generating daily report: {str(e)}")
            return Response({"error": f"Failed to generate report: {str(e)}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class MunicipalitySearchAPIView(APIView):
    def get(self, request):
        postal_code = request.query_params.get('postal_code', None)
        name = request.query_params.get('name', None)
        
        if postal_code:
            municipalities = Municipality.objects.filter(codigo_postal=postal_code)
        elif name:
            municipalities = Municipality.objects.filter(nombre_municipio__icontains=name)
        else:
            return Response({"error": "Must provide postal_code or name parameter"},
                          status=status.HTTP_400_BAD_REQUEST)
        serializer = MunicipalitySerializer(municipalities, many=True)
        return Response(serializer.data)

class PublicCheckInView(APIView):
    permission_classes = []

    def post(self, request, check_in_link):
        check_in = get_object_or_404(CheckIn, check_in_link=check_in_link)
        if check_in.status != CheckInStatus.PENDING:
            return Response(
                {"detail": "This check-in has already been processed"},
                status=status.HTTP_400_BAD_REQUEST
            )
        serializer = CheckInCreateSerializer(
            instance=check_in,
            data=request.data,
            partial=True
        )
        if serializer.is_valid():
            updated_check_in = serializer.save(status=CheckInStatus.CONFIRMED)
            # Send confirmation email
            try:
                send_checkin_confirmation(updated_check_in)
            except Exception as e:
                return Response({"error":f"{e}"})
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CheckInListByPropertyView(generics.ListAPIView):
    """
    List all check-ins for a specific property
    """
    serializer_class = CheckInDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        property_id = self.kwargs['property_id']
        property = get_object_or_404(Property, id=property_id)
        if not (self.request.user.is_staff or property.owner == self.request.user):
            raise PermissionDenied("You don't have permission to view check-ins for this property")
        return CheckIn.objects.filter(property=property).order_by('-check_in_date')