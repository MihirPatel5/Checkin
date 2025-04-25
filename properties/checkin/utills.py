import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from django.conf import settings
from django.utils import timezone
from django.template.loader import render_to_string
from utils.email_services import Email
from .models import CheckIn, PoliceSubmissionLog, CheckInStatus

logger = logging.getLogger(__name__)

class SESHospedajesService:
    """
    Service for interacting with SES.Hospedajes SOAP API
    """
    
    def __init__(self):
        self.wsdl_url = getattr(settings, 'SES_HOSPEDAJES_WSDL_URL', '')
        self.username = getattr(settings, 'SES_HOSPEDAJES_USERNAME', '')
        self.password = getattr(settings, 'SES_HOSPEDAJES_PASSWORD', '')
        self.establishment_code = getattr(settings, 'SES_HOSPEDAJES_ESTABLISHMENT_CODE', '')

    def generate_xml(self, check_in):
        """Generate XML for SES.Hospedajes API"""
        root = ET.Element("Viajeros")
        root.set("version", "1.0")
        establishment = ET.SubElement(root, "Establecimiento")
        ET.SubElement(establishment, "CodigoEstablecimiento").text = self.establishment_code
        registration = ET.SubElement(root, "RegistroEntrada")
        ET.SubElement(registration, "FechaEntrada").text = check_in.check_in_date.strftime("%Y-%m-%dT%H:%M:%S")
        for guest in check_in.guests.all():
            viajero = ET.SubElement(registration, "Viajero")
            ET.SubElement(viajero, "NombreCompleto").text = guest.full_name
            ET.SubElement(viajero, "PrimerApellido").text = guest.first_surname
            if guest.second_surname:
                ET.SubElement(viajero, "SegundoApellido").text = guest.second_surname
            
            ET.SubElement(viajero, "TipoDocumento").text = self._map_document_type(guest.document_type)
            if guest.document_type == 'nie':
                ET.SubElement(viajero, "NumeroIdentificacionExtranjero").text = guest.document_number
            else:
                ET.SubElement(viajero, "NumeroDocumento").text = guest.document_number
            if guest.support_number:
                ET.SubElement(viajero, "NumeroSoporte").text = guest.support_number
            ET.SubElement(viajero, "FechaNacimiento").text = guest.date_of_birth.strftime("%Y-%m-%d")
            ET.SubElement(viajero, "Nacionalidad").text = guest.nationality
            ET.SubElement(viajero, "PaisResidencia").text = guest.country_of_residence
            direccion = ET.SubElement(viajero, "Direccion")
            ET.SubElement(direccion, "DireccionCompleta").text = guest.address
            ET.SubElement(direccion, "CodigoPostal").text = guest.codigo_postal
            ET.SubElement(direccion, "CodigoMunicipio").text = guest.codigo_municipio
            ET.SubElement(direccion, "NombreMunicipio").text = guest.nombre_municipio
            ET.SubElement(direccion, "Provincia").text = guest.provincia
        return ET.tostring(root, encoding='utf-8', method='xml').decode('utf-8')
    
    def _map_document_type(self, document_type):
        """Map our document type to SES.Hospedajes document type"""
        mapping = {
            'passport': 'P',
            'dni': 'NIF',
            'nie': 'NIE',
            'other': 'O'
        }
        return mapping.get(document_type, 'O')
    
    def submit_check_in(self, check_in):
        """Submit check-in data to SES.Hospedajes API"""
        try:
            xml_data = self.generate_xml(check_in)
            headers = {
                'Content-Type': 'application/xml',
                'SOAPAction': 'http://www.policia.es/ses/hospedajes/EnviarParteHospedaje'
            }
            soap_envelope = f"""
            <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" 
                           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" 
                           xmlns:xsd="http://www.w3.org/2001/XMLSchema">
              <soap:Header>
                <AuthHeader xmlns="http://www.policia.es/ses/hospedajes/">
                  <Username>{self.username}</Username>
                  <Password>{self.password}</Password>
                </AuthHeader>
              </soap:Header>
              <soap:Body>
                <EnviarParteHospedaje xmlns="http://www.policia.es/ses/hospedajes/">
                  <xmlDatos>{xml_data}</xmlDatos>
                </EnviarParteHospedaje>
              </soap:Body>
            </soap:Envelope>
            """
            response = requests.post(
                self.wsdl_url,
                data=soap_envelope,
                headers=headers,
                timeout=30
            )
            if response.status_code == 200:
                response_data = response.text
                submission_log = PoliceSubmissionLog.objects.create(
                    check_in=check_in,
                    submitted_type='auto' if check_in.auto_submit_to_police else 'manual',
                    status='success',
                    response_data=response_data
                )
                check_in.status = CheckInStatus.SUBMITTED
                check_in.submission_date = timezone.now()
                check_in.submission_log = f"Successfully submitted to police on {timezone.now()}"
                check_in.save()
                logger.info(f"Successfully submitted check-in {check_in.id} to SES.Hospedajes")
                return {'success': True}
            else:
                error_message = f"Failed to submit check-in: HTTP {response.status_code}: {response.text}"
                submission_log = PoliceSubmissionLog.objects.create(
                    check_in=check_in,
                    submitted_type='auto' if check_in.auto_submit_to_police else 'manual',
                    status='failed',
                    error_message=error_message
                )
                check_in.submission_log = f"Failed submission to police on {timezone.now()}: {error_message}"
                check_in.save()
                logger.error(f"Failed to submit check-in {check_in.id}: {error_message}")
                return {'success': False, 'error': error_message}
        
        except Exception as e:
            error_message = f"Exception submitting check-in: {str(e)}"
            submission_log = PoliceSubmissionLog.objects.create(
                check_in=check_in,
                submitted_type='auto' if check_in.auto_submit_to_police else 'manual',
                status='failed',
                error_message=error_message
            )
            check_in.submission_log = f"Failed submission to police on {timezone.now()}: {error_message}"
            check_in.save


def send_checkin_confirmation(check_in):
    """
    Send confirmation email to lead guest after successful check-in
    """
    subject = f"Check-in Confirmation for {check_in.property.name}"
    html_content = render_to_string('email_templates/checkin_confirmation.html', {
        'check_in': check_in,
        'property': check_in.property,
    })
    try:
        email = Email(subject=subject)  
        email.to(check_in.lead_guest_email)
        email.add_html(html_content)
        if check_in.property.owner.email:
            email.cc(check_in.property.owner.email)
        email.send()
        
    except Exception as e:
        logger.error(f"Failed to send check-in confirmation email: {str(e)}")
        raise

def send_police_submission_notification(check_in, success=True):
    """
    Send notification to property owner about police submission status
    """
    status = "successful" if success else "failed"
    subject = f"Police Submission {status} for {check_in.property.name}"
    html_content = render_to_string('email_templates/police_submission.html', {
        'check_in': check_in,
        'property': check_in.property,
        'success': success,
    })
    try:
        email = Email(subject=subject)
        email.to(check_in.property.owner.email)
        if hasattr(settings, 'EMAIL_HOST_USER') and settings.EMAIL_HOST_USER:
            email.cc(settings.EMAIL_HOST_USER)
        email.add_html(html_content)
        email.send()
    except Exception as e:
        logger.error(f"Failed to send police submission notification: {str(e)}")
        raise

def send_checkin_link_email(check_in, recipient_email, recipient_name=None):
    """
    Send the check-in form link to the lead guest
    """
    subject = f"Check-in Form for {check_in.property_ref.name}"
    html_content = render_to_string('email_templates/checkin_link.html', {
        'check_in': check_in,
        'property': check_in.property_ref,
        'recipient_name': recipient_name,
    })
    try:
        email = Email(subject=subject)
        email.to(recipient_email, name=recipient_name)
        email.add_html(html_content)
        email.send()
    except Exception as e:
        logger.error(f"Failed to send check-in link email: {str(e)}")
        raise
