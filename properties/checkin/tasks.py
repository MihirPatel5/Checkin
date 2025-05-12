from celery import shared_task
from django.db import transaction
from django.utils import timezone
from .models import CheckIn, PoliceSubmissionLog, CheckInStatus
from .utils import (
    SESHospedajesService,
    send_police_submission_notification,
    logger
)
from io import BytesIO
import zipfile
import base64
import requests

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=120,
    retry_jitter=True,
    retry_backoff_max=600
)
def submit_ses_report(self, check_in_id):
    """
    Celery task for submitting check-in data to SES with full XML generation,
    ZIP compression, and Base64 encoding as per client requirements
    """
    try:
        with transaction.atomic():
            check_in = CheckIn.objects.select_for_update().get(id=check_in_id)
            if check_in.status == CheckInStatus.COMPLETED:
                logger.warning(f"Skipping already completed check-in {check_in_id}")
                return {'status': 'skipped', 'check_in_id': check_in_id}

            service = SESHospedajesService()
            xml_content = service._generate_guest_xml(check_in)
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.writestr('parte.xml', xml_content.encode('utf-8'))
            zip_data = zip_buffer.getvalue()
            base64_content = base64.b64encode(zip_data).decode('ascii')
            soap_envelope = service._create_soap_envelope("A", base64_content)
            response = requests.post(
                service.wsdl_url,
                data=soap_envelope,
                headers={
                    "Content-Type": "text/xml; charset=UTF-8",
                    "SOAPAction": "http://www.soap.servicios.hospedajes.mir.es/comunicacion/comunicacionRequest"
                },
                cert=service.cert,
                auth=service.auth,
                timeout=30
            )
            result = service._handle_response(response, check_in)
            
            if result['success']:
                check_in.status = CheckInStatus.COMPLETED
                check_in.completed_at = timezone.now()
                check_in.save()
                PoliceSubmissionLog.objects.create(
                    check_in=check_in,
                    status=PoliceSubmissionLog.SubmissionStatus.SUBMITTED,
                    raw_request=xml_content,
                    raw_response=response.text,
                    xml_version="1.0",
                    retry_count=self.request.retries
                )
                send_police_submission_notification(check_in, True)
                logger.info(f"Successfully submitted check-in {check_in_id} to SES")
                
                return {
                    'status': 'success',
                    'check_in_id': check_in_id,
                    'submitted_at': check_in.completed_at.isoformat()
                }
            else:
                raise Exception(result.get('error', 'Unknown SES error'))

    except CheckIn.DoesNotExist:
        logger.error(f"CheckIn {check_in_id} not found, retrying...")
        raise self.retry(countdown=60, max_retries=2)
        
    except Exception as e:
        logger.error(f"Failed to submit check-in {check_in_id}: {str(e)}")
        try:
            check_in = CheckIn.objects.get(id=check_in_id)
            check_in.status = CheckInStatus.FAILED
            check_in.save()

            PoliceSubmissionLog.objects.create(
                check_in=check_in,
                status=PoliceSubmissionLog.SubmissionStatus.FAILED,
                error_message=str(e),
                retry_count=self.request.retries,
                xml_version="1.0"
            )
            send_police_submission_notification(check_in, False)
        except Exception:
            logger.exception("Failed to log SES submission failure.")
        raise self.retry(exc=e, countdown=2 ** self.request.retries * 30)

@shared_task
def translate_guest_fields(self, guest_id, fields, target_lang):
    """Task for handling DeepL translations"""
    from .models import Guest
    from .translation_service import TranslationService
    
    try:
        guest = Guest.objects.get(id=guest_id)
        return TranslationService.translate_guest_fields(
            guest, fields, target_lang
        )
    except Guest.DoesNotExist:
        logger.error(f"Guest {guest_id} not found for translation")
    except Exception as e:
        logger.error(f"Translation failed for guest {guest_id}: {str(e)}")
        raise self.retry(exc=e, countdown=60)
