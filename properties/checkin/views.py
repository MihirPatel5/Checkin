from decimal import Decimal
from checkin.tasks import submit_ses_report
from payment.models import Transaction, Upsell
from payment.services.payment_service import PaymentService
from rest_framework import generics, status, permissions, viewsets, serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import transaction
from django.utils.translation import gettext as _
from datetime import datetime, time, timedelta
from .models import (
    CheckInStatus, GuardianRelationship, Reservation, PoliceSubmissionLog, Guest,
    PropertyICal, DataRetentionPolicy, ReservationStatus, CheckIn, SelectedUpsell
)
from .serializers import (
    CompleteCheckInSerializer, ReservationSerializer, CheckInSerializer,
    PropertyICalSerializer, DataRetentionPolicySerializer,
    PoliceSubmissionLogSerializer, SingleGuestSerializer
)
from property.models import Property
from utils.translation_services import translate_dict
from .utils import (
    send_checkin_confirmation,
    send_checkin_link_email, 
    SESHospedajesService,
    send_police_submission_notification
)
# from .tasks import submit_ses_report
import logging
logger = logging.getLogger(__name__)

class ReservationListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ReservationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return Reservation.objects.all()
        return Reservation.objects.filter(property_ref__owner=user)

    def perform_create(self, serializer):
        property_id = self.request.data.get('property_id')
        if not property_id:
            raise ValidationError("property_id is required")
        property = get_object_or_404(Property, id=property_id)
        if not (self.request.user.is_staff or property.owner == self.request.user):
            raise PermissionDenied("You don't have permission to create reservations for this property")
            
        reservation = serializer.save(property_ref=property)
        try:
            send_checkin_link_email(
                reservation=reservation,
                recipient_email=reservation.lead_guest_email,
                recipient_name=reservation.lead_guest_name
            )
            logger.info(f"Check-in email sent for reservation {reservation.reservation_code}")
        except Exception as e:
            logger.error(f"Failed to send check-in email: {str(e)}")
      

class ReservationRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ReservationSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'reservation_code'
    lookup_url_kwarg = 'reservation_code'

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return Reservation.objects.all()
        return Reservation.objects.filter(property_ref__owner=user)

    def perform_update(self, serializer):
        instance = self.get_object()
        property_id = self.request.data.get('property_id')
        
        if property_id and property_id != instance.property_ref.id:
            if not self.request.user.is_staff:
                raise PermissionDenied("Only staff can change property assignments")
                
            new_property = get_object_or_404(Property, id=property_id)
            serializer.save(property_ref=new_property)
        else:
            serializer.save()

    def perform_destroy(self, instance):
        instance.delete()
        logger.info(f"Reservation {instance.reservation_code} deleted by {self.request.user}")

class GuestListCreateView(generics.ListCreateAPIView):
    """
    GET  /checkin/<check_in_link>/guests/  -> list all guests
    POST /checkin/<check_in_link>/guests/  -> add a new guest
    """
    serializer_class = SingleGuestSerializer
    # parser_classes = [MultiPartParser, FormParser]
    permission_classes = []

    def get_reservation(self):
        return get_object_or_404(
            Reservation.objects.select_related('property_ref'),
            check_in_link=self.kwargs['check_in_link']
        )

    def get_checkin(self):
        reservation = self.get_reservation()
        checkin = CheckIn.objects.filter(
            reservation=reservation,
            status=CheckInStatus.IN_PROGRESS
        ).first()
        if not checkin:
            checkin = CheckIn.objects.create(
                reservation=reservation,
                status=CheckInStatus.IN_PROGRESS,
                initiated_at=timezone.now()
            )
        return checkin

    def get_queryset(self):
        checkin = self.get_checkin()
        return Guest.objects.filter(check_in=checkin)

    def perform_create(self, serializer):
        reservation = self.get_reservation()
        if hasattr(reservation, 'check_in_process') and reservation.check_in_process.status == CheckInStatus.COMPLETED:
            raise serializers.ValidationError("Check-in already completed.")
        if reservation.status != ReservationStatus.CONFIRMED:
            raise serializers.ValidationError("Reservation is not confirmed.")
        checkin = self.get_checkin()
        current_count = Guest.objects.filter(check_in=checkin).count()
        if current_count >= reservation.total_guests:
            raise serializers.ValidationError(f"Maximum number of guests ({reservation.total_guests}) reached.")
        serializer.save(check_in=checkin)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            self.perform_create(serializer)
            checkin = self.get_checkin()
            total = Guest.objects.filter(check_in=checkin).count()
            remaining = self.get_reservation().total_guests - total
            return Response({
                'guest': serializer.data,
                'progress': {
                    'total_guests_required': self.get_reservation().total_guests,
                    'guests_added': total,
                    'remaining_guests': remaining
                }
            }, status=status.HTTP_201_CREATED)


class GuestRetrieveUpdateView(generics.RetrieveUpdateAPIView):
    """
    GET    /checkin/<check_in_link>/guests/<pk>/   -> retrieve a guest
    PUT    /checkin/<check_in_link>/guests/<pk>/   -> full update
    PATCH  /checkin/<check_in_link>/guests/<pk>/   -> partial update
    """
    serializer_class = SingleGuestSerializer
    # parser_classes = [MultiPartParser, FormParser]
    permission_classes = []

    def get_reservation(self):
        return get_object_or_404(
            Reservation.objects.select_related('property_ref'),
            check_in_link=self.kwargs['check_in_link']
        )

    def get_checkin(self):
        reservation = self.get_reservation()
        return get_object_or_404(
            CheckIn,
            reservation=reservation,
            status=CheckInStatus.IN_PROGRESS
        )

    def get_queryset(self):
        checkin = self.get_checkin()
        return Guest.objects.filter(check_in=checkin)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            self.perform_update(serializer)
        return Response(serializer.data)


class CompleteCheckInView(generics.CreateAPIView):
    serializer_class = CompleteCheckInSerializer
    permission_classes = []

    def get_checkin(self):
        reservation = get_object_or_404(
            Reservation.objects.select_related('property_ref'),
            check_in_link=self.kwargs['check_in_link']
        )
        checkin = get_object_or_404(
            CheckIn,
            reservation=reservation,
            status=CheckInStatus.IN_PROGRESS
        )
        return checkin

    def create(self, request, *args, **kwargs):
        checkin = self.get_checkin()
        serializer = self.get_serializer(data=request.data, context={'checkin': checkin})
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        with transaction.atomic():
            relationships_data = validated_data.get('guardian_relationships', [])
            for rel_data in relationships_data:
                GuardianRelationship.objects.create(
                    minor=rel_data['minor'],
                    guardian=rel_data['guardian'],
                    relationship_type=rel_data['relationship_type']
                )
            selected_upsells_data = validated_data.get('selected_upsells_data', [])
            upsell_total_amount = Decimal('0.00')
            for upsell_data in selected_upsells_data:
                upsell_id = upsell_data.get('upsell_id')
                quantity = upsell_data.get('quantity', 1)
                try:
                    upsell = Upsell.objects.get(id=upsell_id, is_active=True)
                    selected_upsell = SelectedUpsell.objects.create(
                        check_in=checkin,
                        upsell=upsell,
                        quantity=quantity,
                        price_at_selection=upsell.price
                    )
                    upsell_total_amount += selected_upsell.price_at_selection * selected_upsell.quantity
                except Upsell.DoesNotExist:
                    raise serializers.ValidationError(f"Selected upsell (ID: {upsell_id}) is invalid or inactive.")

            total_amount_due = checkin.reservation.outstanding_amount
            total_amount_for_payment = total_amount_due + upsell_total_amount
            checkin.total_amount_charged = total_amount_for_payment
            checkin.save()

            payment_token = validated_data.get('payment_token')
            if total_amount_for_payment > Decimal('0.00'):
                if not payment_token:
                    checkin.status = CheckInStatus.PAYMENT_PENDING
                    checkin.save()
                    return Response({"message": "Payment required"}, status=status.HTTP_402_PAYMENT_REQUIRED)
                try:
                    payment_result = PaymentService.process_payment(
                        amount=total_amount_for_payment,
                        currency=checkin.reservation.property_ref.currency,
                        token=payment_token,
                        description=f"Payment for reservation {checkin.reservation.reservation_code}",
                        landlord=checkin.reservation.property_ref.owner
                    )
                    if not payment_result['success']:
                        raise serializers.ValidationError("Payment processing failed.")
                    Transaction.objects.create(
                        check_in=checkin,
                        reservation=checkin.reservation,
                        guest_email=checkin.reservation.lead_guest_email,
                        landlord=checkin.reservation.property_ref.owner,
                        transaction_type='addon_payment' if upsell_total_amount > Decimal('0.00') else 'reservation_payment',
                        amount=total_amount_for_payment,
                        currency=checkin.reservation.property_ref.currency,
                        status='succeeded',
                        stripe_payment_intent_id=payment_result.get('payment_intent_id'),
                        completed_at=timezone.now()
                    )
                except Exception as e:
                    logger.error(f"Payment processing error for check-in {checkin.id}: {str(e)}")
                    checkin.status = CheckInStatus.FAILED
                    checkin.save()
                    raise serializers.ValidationError("Payment processing failed.")

            checkin.status = CheckInStatus.COMPLETED
            checkin.completed_at = timezone.now()
            checkin.digital_signature = validated_data.get('digital_signature')
            checkin.save()

            reservation = checkin.reservation
            if total_amount_for_payment >= reservation.outstanding_amount:
                reservation.amount_paid += reservation.outstanding_amount
                reservation.is_fully_paid = True
            else:
                reservation.amount_paid += total_amount_for_payment
                reservation.is_fully_paid = False
            reservation.save()

            try:
                send_checkin_confirmation(checkin)
            except Exception as e:
                logger.error(f"Failed to send check-in confirmation email for {checkin.id}: {e}")

            if reservation.is_auto_submit:
                try:
                    checkin_date_local = timezone.localtime(reservation.check_in_date).date()
                    scheduled_date = checkin_date_local - timezone.timedelta(days=1)
                    scheduled_time = timezone.make_aware(
                        timezone.datetime.combine(
                            scheduled_date,
                            timezone.datetime.strptime("21:00", "%H:%M").time()
                        ),
                        timezone.get_current_timezone()
                    )
                    if scheduled_time <= timezone.now():
                        scheduled_time = timezone.now() + timezone.timedelta(minutes=5)
                        logger.warning(f"Check-in date {checkin_date_local} is in the past or today. Scheduling police submission for {scheduled_time}")
                    from checkin.tasks import submit_ses_report
                    submit_ses_report.apply_async(
                        args=[checkin.id],
                        eta=scheduled_time,
                    )
                    logger.info(f"Scheduled police submission for check-in {checkin.id} at {scheduled_time}")
                except Exception as e:
                    logger.error(f"Failed to schedule police submission for check-in {checkin.id}: {e}")
                    send_police_submission_notification(checkin, False)

        return Response({"message": "Check-in completed successfully"}, status=status.HTTP_200_OK)


class CheckInCreateAPIView(generics.CreateAPIView):
    serializer_class = CheckInSerializer
    permission_classes = []

    def create(self, request, *args, **kwargs):
        reservation = get_object_or_404(
            Reservation.objects.select_related('property_ref'), 
            check_in_link=self.kwargs['check_in_link']
        )
        if hasattr(reservation, 'check_in_process') and reservation.check_in_process.status == CheckInStatus.COMPLETED:
             return Response(
                 {"error": _("Check-in for this reservation is already completed.")},
                 status=status.HTTP_400_BAD_REQUEST
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
                'request': request,
                'check_in': check_in
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
        read_serializer = CheckInSerializer(check_in, context={'request':request})
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class PoliceSubmissionLogViewSet(viewsets.ReadOnlyModelViewSet):
     queryset = PoliceSubmissionLog.objects.all()
     serializer_class = PoliceSubmissionLogSerializer
     permission_classes = [permissions.IsAdminUser] # Admin can view

     def get_queryset(self):
          if self.request.user.is_staff:
               return PoliceSubmissionLog.objects.all().select_related('check_in__reservation__property_ref')
          else:
               return PoliceSubmissionLog.objects.filter(
                    check_in__reservation__property_ref__owner=self.request.user
               ).select_related('check_in__reservation__property_ref')


class PoliceSubmissionRetryAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, submission_id):
        submission = get_object_or_404(PoliceSubmissionLog, id=submission_id)
        if not request.user.is_staff and submission.check_in.reservation.property_ref.owner != request.user:
            raise PermissionDenied
        if submission.status == PoliceSubmissionLog.SubmissionStatus.ACKNOWLEDGED:
            return Response({"detail": _("Submission already acknowledged as successful.")}, status=status.HTTP_400_BAD_REQUEST)
        try:
            service = SESHospedajesService()
            result = service.retry_submission(submission)
            
            submission.retry_count += 1
            submission.raw_request = result.get('xml_data', submission.raw_request)
            submission.raw_response = result.get('response', submission.raw_response)
            submission.error_message = result.get('error', submission.error_message)
            submission.status = PoliceSubmissionLog.SubmissionStatus.ACKNOWLEDGED if result['success'] else PoliceSubmissionLog.SubmissionStatus.FAILED
            submission.save()
            
            serializer = PoliceSubmissionLogSerializer(submission)
            return Response(serializer.data)
        except Exception as e:
             logger.error(f"Retry police submission failed for log {submission.id}: {str(e)}")
             submission.status = PoliceSubmissionLog.SubmissionStatus.FAILED
             submission.error_message = str(e)
             submission.save()
             return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PropertyICalViewSet(viewsets.ModelViewSet):
    serializer_class = PropertyICalSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:
            return PropertyICal.objects.all().select_related('property')
        else:
            property = get_object_or_404(Property, id=self.kwargs['property_id'], owner=self.request.user)
            return PropertyICal.objects.filter(property=property)

    def perform_create(self, serializer):
        property = get_object_or_404(Property, id=self.kwargs['property_id'])
        if not (self.request.user.is_staff or property.owner == self.request.user):
            raise PermissionDenied
        serializer.save(property=property)
    
    def perform_update(self, serializer):
        property = get_object_or_404(Property, id=self.kwargs['property_id'])
        if not (self.request.user.is_staff or property.owner == self.request.user):
            raise PermissionDenied
        serializer.save()

    def perform_destroy(self, instance):
        if not (self.request.user.is_staff or instance.property.owner == self.request.user):
            raise PermissionDenied
        instance.delete()


class DataRetentionPolicyAPIView(generics.RetrieveUpdateAPIView):
    serializer_class = DataRetentionPolicySerializer
    permission_classes = [permissions.IsAdminUser]

    def get_object(self):
        property = get_object_or_404(Property, id=self.kwargs['property_id'])
        if not self.request.user.is_staff and property.owner != self.request.user:
            raise PermissionDenied
        obj, created = DataRetentionPolicy.objects.get_or_create(property=property)
        return obj


class PublicCheckInAPIView(APIView):
    permission_classes = []

    def get(self, request, check_in_link):
        reservation = get_object_or_404(
            Reservation.objects.select_related('property_ref').prefetch_related('check_in_process__guests', 'check_in_process__selected_upsells__upsell'), # Pre-fetch related data
            check_in_link=check_in_link
        )
        check_in = getattr(reservation, 'check_in_process', None)
        reservation_data = ReservationSerializer(reservation, context={'request': request}).data
        check_in_data = CheckInSerializer(check_in, context={'request': request}).data if check_in else None
        response_data = {
            'reservation': reservation_data,
            'check_in': check_in_data
        }
        return Response(response_data)


class ReservationSearchAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    # def get(self, request):
    #     code = request.query_params.get('code', '')
    #     try:
    #         reservation = Reservation.objects.get(reservation_code=code.upper())
    #         return Response({
    #             'found': True,
    #             'reservation': ReservationSerializer(reservation).data
    #         })
    #     except Reservation.DoesNotExist:
    #         return Response({'found': False}, status=status.HTTP_404_NOT_FOUND)
    
    def post(self, request):
        code = request.data.get('code')
        email = request.data.get('email')
        check_in_date_str = request.data.get('check_in_date')
        check_out_date_str = request.data.get('check_out_date')
        target_lang = request.query_params.get('lang', 'EN').upper()
        reservations = Reservation.objects.filter(status=ReservationStatus.CONFIRMED)
        if code:
            reservations = reservations.filter(reservation_code__iexact=code)
        elif email and check_in_date_str and check_out_date_str:
            try:
                check_in_date = datetime.strptime(check_in_date_str, '%Y-%m-%d').date()
                check_out_date = datetime.strptime(check_out_date_str, '%Y-%m-%d').date()
                reservations = reservations.filter(
                    lead_guest_email__iexact=email,
                    check_in_date__date__lte=check_out_date,
                    check_out_date__date__gte=check_in_date
                )
            except ValueError:
                return Response({"error": _("Invalid date format. Use YYYY-MM-DD.")}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"error": _("Please provide reservation code or email and check-in/check-out dates.")}, status=status.HTTP_400_BAD_REQUEST)

        if not reservations.exists():
            return Response({'found': False, 'detail': _("Reservation not found.")}, status=status.HTTP_404_NOT_FOUND)
        reservation = reservations.first()
        reservation_data = ReservationSerializer(reservation, context={'request': request}).data
        translated_reservation = translate_dict(reservation_data, target_lang)
        return Response({
            'found': True,
            'reservation': translated_reservation
        })

class DailyPoliceReportAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        target_date_str = request.data.get('target_date')
        if not target_date_str:
            target_date = timezone.now().date() + timezone.timedelta(days=1)
            logger.info(f"No target_date provided, defaulting to tomorrow: {target_date}")
        else:
            try:
                target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
                logger.info(f"Processing police report for target date: {target_date}")
            except ValueError:
                return Response({"error": _("Invalid date format for target_date. Use YYYY-MM-DD.")}, status=status.HTTP_400_BAD_REQUEST)
        reservations = Reservation.objects.filter(
            check_in_date__date=target_date,
            is_auto_submit=True,
            status=ReservationStatus.CONFIRMED
        ).select_related('property_ref')

        results = []
        service = SESHospedajesService()
        for reservation in reservations:
            check_in = getattr(reservation, 'check_in_process', None)
            if not check_in or check_in.status != CheckInStatus.COMPLETED:
                results.append({
                    'reservation': reservation.reservation_code,
                    'success': False,
                    'error': _("No completed check-in found for this reservation or check-in not completed.")
                })
                continue
            if PoliceSubmissionLog.objects.filter(check_in=check_in, status=PoliceSubmissionLog.SubmissionStatus.ACKNOWLEDGED).exists():
                results.append({
                   'reservation': reservation.reservation_code,
                   'success': True,
                   'detail': _("Police submission already acknowledged for this check-in.")
                })
                continue
            try:
                result = service.submit_check_in(check_in)
                PoliceSubmissionLog.objects.create(
                    check_in=check_in,
                    status=PoliceSubmissionLog.SubmissionStatus.ACKNOWLEDGED if result['success'] else PoliceSubmissionLog.SubmissionStatus.FAILED,
                    raw_request=result.get('xml_data', ''),
                    raw_response=result.get('response', ''),
                    error_message=result.get('error', ''),
                    xml_version='1.0'
                )
                results.append({
                    'reservation': reservation.reservation_code,
                    'success': result['success'],
                    'error': result.get('error')
                })
            except Exception as e:
                logger.error(f"Police submission failed for reservation {reservation.reservation_code}: {str(e)}")
                PoliceSubmissionLog.objects.create(
                    check_in=check_in,
                    status=PoliceSubmissionLog.SubmissionStatus.FAILED,
                    error_message=str(e)
                )
                results.append({
                    'reservation': reservation.reservation_code,
                    'success': False,
                    'error': str(e)
                })
                
        return Response({'results': results})
