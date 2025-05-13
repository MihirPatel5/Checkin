from rest_framework import generics, status, permissions, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import PermissionDenied
from datetime import datetime, time, timedelta
from .models import (
    Reservation, PoliceSubmissionLog,
    PropertyICal, DataRetentionPolicy, ReservationStatus
)
from .serializers import (
    ReservationSerializer, CheckInSerializer,
    PropertyICalSerializer, DataRetentionPolicySerializer
)
from property.models import Property
from .utils import (
    send_checkin_confirmation,
    send_checkin_link_email, 
    SESHospedajesService,
    send_police_submission_notification
)
from .tasks import submit_ses_report
import logging
logger = logging.getLogger(__name__)

class ReservationCreateAPIView(generics.CreateAPIView):
    serializer_class = ReservationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        property = get_object_or_404(Property, id=self.request.data.get('property_id'))
        if not (self.request.user.is_staff or property.owner == self.request.user):
            raise PermissionDenied
            
        reservation = serializer.save(property_ref=property)
        send_checkin_link_email(
            reservation=reservation,
            recipient_email=reservation.lead_guest_email,
            recipient_name=reservation.lead_guest_name
        )        
class ReservationDetailAPIView(generics.RetrieveAPIView):
    queryset = Reservation.objects.all()
    serializer_class = ReservationSerializer
    lookup_field = 'reservation_code'
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        reservation = super().get_object()
        if not (self.request.user.is_staff or reservation.property_ref.owner == self.request.user):
            raise PermissionDenied
        return reservation


class CheckInCreateAPIView(generics.CreateAPIView):
    serializer_class = CheckInSerializer
    permission_classes = []

    def create(self, request, *args, **kwargs):
        reservation = get_object_or_404(
            Reservation, 
            check_in_link=self.kwargs['check_in_link']
        )
        
        if reservation.status != ReservationStatus.CONFIRMED:
            return Response(
                {"error": "Reservation is not confirmed"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ip_address = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
        # if ',' in ip_address:
        #     ip_address = ip_address.split(',')[0].strip()
        serializer = self.get_serializer(
            data=request.data,
            context={
                'property_country': reservation.property_ref.country,
                'reservation': reservation,
                'request': request
            }
        )
        serializer.is_valid(raise_exception=True)
        
        with transaction.atomic():
            check_in = serializer.save(
                reservation=reservation,
                # ip_address=ip_address,
                initiated_at=timezone.now(),
                completed_at=timezone.now(),
                status='completed'
            )
            
            send_checkin_confirmation(check_in)
            
            if reservation.is_auto_submit:
                try:
                    scheduled_time = timezone.make_aware(
                        datetime.combine(
                            reservation.check_in_date - timedelta(days=1), 
                            time(hour=21, minute=0)  # 9 previous night
                        )
                    )
                    submit_ses_report.apply_async(
                        args=[check_in.id],
                        eta=scheduled_time,
                    )
                    logger.info(f"Scheduled police submission for check-in {check_in.id} at {scheduled_time}")
                except Exception as e:
                    send_police_submission_notification(check_in, False)
                
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    # def submit_to_police(self, check_in):
    #     try:
    #         service = SESHospedajesService()
    #         result = service.submit_check_in(check_in)
            
    #         PoliceSubmissionLog.objects.create(
    #             check_in=check_in,
    #             status=PoliceSubmissionLog.SubmissionStatus.SUBMITTED if result['success'] else PoliceSubmissionLog.SubmissionStatus.FAILED,
    #             raw_request=result.get('xml_data', ''),
    #             raw_response=result.get('response', ''),
    #             error_message=result.get('error', '')
    #         )
    #         return result
    #     except Exception as e:
    #         logger.error(f"Police submission failed: {str(e)}")
    #         PoliceSubmissionLog.objects.create(
    #             check_in=check_in,
    #             status=PoliceSubmissionLog.SubmissionStatus.FAILED,
    #             error_message=str(e)
    #         )
    #         raise


class PoliceSubmissionRetryAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, submission_id):
        submission = get_object_or_404(PoliceSubmissionLog, id=submission_id)
        
        try:
            service = SESHospedajesService()
            result = service.retry_submission(submission)
            
            submission.retry_count += 1
            submission.status = PoliceSubmissionLog.SubmissionStatus.SUBMITTED if result['success'] else PoliceSubmissionLog.SubmissionStatus.FAILED
            submission.save()
            
            return Response({"status": submission.status})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PropertyICalViewSet(viewsets.ModelViewSet):
    serializer_class = PropertyICalSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        property = get_object_or_404(Property, id=self.kwargs['property_id'])
        if not (self.request.user.is_staff or property.owner == self.request.user):
            raise PermissionDenied
        return PropertyICal.objects.filter(property=property)

    def perform_create(self, serializer):
        property = get_object_or_404(Property, id=self.kwargs['property_id'])
        serializer.save(property=property)


class DataRetentionPolicyAPIView(generics.RetrieveUpdateAPIView):
    serializer_class = DataRetentionPolicySerializer
    permission_classes = [permissions.IsAdminUser]

    def get_object(self):
        property = get_object_or_404(Property, id=self.kwargs['property_id'])
        return DataRetentionPolicy.objects.get_or_create(property=property)[0]


class PublicCheckInAPIView(APIView):
    permission_classes = []

    def get(self, request, check_in_link):
        reservation = get_object_or_404(Reservation, check_in_link=check_in_link)
        check_in = reservation.check_in.first()
        
        response_data = {
            'reservation': ReservationSerializer(reservation).data,
            'check_in': CheckInSerializer(check_in).data if check_in else None
        }
        
        return Response(response_data)

class ReservationSearchAPIView(APIView):
    def get(self, request):
        code = request.query_params.get('code', '')
        try:
            reservation = Reservation.objects.get(reservation_code=code.upper())
            return Response({
                'found': True,
                'reservation': ReservationSerializer(reservation).data
            })
        except Reservation.DoesNotExist:
            return Response({'found': False}, status=status.HTTP_404_NOT_FOUND)


class DailyPoliceReportAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        tomorrow = timezone.now() + timezone.timedelta(days=1)
        reservations = Reservation.objects.filter(
            check_in_date__date=tomorrow.date(),
            is_auto_submit=True,
            status=ReservationStatus.CONFIRMED
        )
        results = []
        service = SESHospedajesService()
        for reservation in reservations:
            check_in = reservation.check_in.first()
            if not check_in:
                continue
            try:
                result = service.submit_check_in(check_in)
                results.append({
                    'reservation': reservation.reservation_code,
                    'success': result['success'],
                    'error': result.get('error')
                })
            except Exception as e:
                results.append({
                    'reservation': reservation.reservation_code,
                    'success': False,
                    'error': str(e)
                })
                
        return Response({'results': results})
