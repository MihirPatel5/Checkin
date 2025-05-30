import base64
from io import BytesIO
import logging
import zipfile
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
    """Unified service for SES/Hospedajes integration handling both property validation and guest check-ins"""
    
    def __init__(self):
        self.wsdl_url = settings.SES_HOSPEDAJES_WSDL_URL
        self.landlord_code = settings.SES_LANDLORD_CODE
        self.auth = (settings.SES_WS_USER, settings.SES_WS_PASSWORD)
        self.cert = (settings.SES_CERT_PATH, settings.SES_KEY_PATH)
        
    def _create_soap_envelope(self, operation_type, xml_content):
        """Create SOAP envelope with base64 encoded ZIP content"""
        base64_content = self._zip_and_encode_xml(xml_content)
        
        return f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:com="http://www.soap.servicios.hospedajes.mir.es/comunicacion">
  <soapenv:Header/>
  <soapenv:Body>
    <com:comunicacionRequest>
      <peticion>
        <cabecera>
          <codigoArrendador>{self.landlord_code}</codigoArrendador>
          <aplicacion>TuriCheck</aplicacion>
          <tipoOperacion>{operation_type}</tipoOperacion>
          <tipoComunicacion>PV</tipoComunicacion>
        </cabecera>
        <solicitud><![CDATA[{base64_content}]]></solicitud>
      </peticion>
    </com:comunicacionRequest>
  </soapenv:Body>
</soapenv:Envelope>"""

    def _zip_and_encode_xml(self, xml_content):
        """Zip XML content and return base64 encoded string"""
        try:
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.writestr('parte.xml', xml_content.encode('utf-8'))
            return base64.b64encode(zip_buffer.getvalue()).decode('ascii')
        except Exception as e:
            logger.error(f"XML zip/encode error: {str(e)}")
            raise

    def _generate_guest_xml(self, check_in):
        """Generate XML structure for guest check-ins"""
        root = ET.Element("alt:peticion", xmlns="http://www.neg.hospedajes.mir.es/altaParteHospedaje")
        
        solicitud = ET.SubElement(root, "solicitud")
        ET.SubElement(solicitud, "codigoEstablecimiento").text = self.landlord_code
        
        comunicacion = ET.SubElement(solicitud, "comunicacion")
        
        # Contract information
        contrato = ET.SubElement(comunicacion, "contrato")
        ET.SubElement(contrato, "referencia").text = check_in.reservation.reservation_code
        ET.SubElement(contrato, "fechaEntrada").text = check_in.reservation.check_in_date.strftime("%Y-%m-%dT%H:%M:%S")
        ET.SubElement(contrato, "fechaSalida").text = check_in.reservation.check_out_date.strftime("%Y-%m-%dT%H:%M:%S")
        ET.SubElement(contrato, "numPersonas").text = str(check_in.reservation.guest_count)
        
        # Guest information
        for guest in check_in.guests.all():
            persona = ET.SubElement(comunicacion, "persona")
            ET.SubElement(persona, "rol").text = "VI" if guest.is_lead else "VG"
            
            # Name components
            ET.SubElement(persona, "nombre").text = guest.first_name
            ET.SubElement(persona, "apellido1").text = guest.last_name
            ET.SubElement(persona, "apellido2").text = guest.last_name2 or ""
            
            # Document information
            doc_type = self._map_document_type(guest.document_type)
            ET.SubElement(persona, "tipoDocumento").text = doc_type
            ET.SubElement(persona, "numeroDocumento").text = guest.document_number
            if guest.support_number:
                ET.SubElement(persona, "soporteDocumento").text = guest.support_number
                
            # Personal details
            ET.SubElement(persona, "fechaNacimiento").text = guest.date_of_birth.strftime("%Y-%m-%d")
            ET.SubElement(persona, "nacionalidad").text = guest.nationality
            ET.SubElement(persona, "sexo").text = guest.gender[0].upper()
            
            # Address information
            direccion = ET.SubElement(persona, "direccion")
            ET.SubElement(direccion, "direccion").text = guest.address
            ET.SubElement(direccion, "codigoPostal").text = guest.codigo_postal
            if guest.municipality:
                ET.SubElement(direccion, "codigoMunicipio").text = guest.municipality.codigo_municipio
            ET.SubElement(direccion, "pais").text = guest.country_of_residence
            
            # Contact information
            ET.SubElement(persona, "telefono").text = guest.phone
            ET.SubElement(persona, "correo").text = check_in.reservation.lead_guest_email

        return ET.tostring(root, encoding='utf-8', method='xml').decode('utf-8')

    def _map_document_type(self, doc_type):
        """Map internal document types to SES codes"""
        return {
            'passport': 'PAS',
            'dni': 'NIF',
            'nie': 'NIE',
            'other': 'OTR'
        }.get(doc_type.lower(), 'OTR')

    def submit_check_in(self, check_in):
        """Submit guest check-in data to Spanish police system"""
        try:
            # Generate XML content
            xml_content = self._generate_guest_xml(check_in)
            soap_envelope = self._create_soap_envelope("A", xml_content)
            
            # Prepare headers
            headers = {
                "Content-Type": "text/xml; charset=UTF-8",
                "SOAPAction": "http://www.soap.servicios.hospedajes.mir.es/comunicacion/comunicacionRequest"
            }
            
            # Send request
            response = requests.post(
                self.wsdl_url,
                data=soap_envelope,
                headers=headers,
                auth=self.auth,
                cert=self.cert,
                verify=False,
                timeout=30
            )
            
            return self._handle_response(response, check_in)
            
        except Exception as e:
            error_msg = f"SES submission failed: {str(e)}"
            logger.error(error_msg)
            self._log_submission(check_in, False, error_msg)
            return {'success': False, 'error': error_msg}

    def _handle_response(self, response, check_in):
        """Process SES API response"""
        if response.status_code == 200:
            if "<codigo>0</codigo>" in response.text:
                self._log_submission(check_in, True, response.text)
                return {'success': True, 'response': response.text}
            
            error_msg = self._extract_error(response.text)
            self._log_submission(check_in, False, error_msg)
            return {'success': False, 'error': error_msg}
        
        error_msg = f"HTTP Error {response.status_code}: {response.text}"
        self._log_submission(check_in, False, error_msg)
        return {'success': False, 'error': error_msg}

    def _extract_error(self, response_text):
        """Extract error message from XML response"""
        try:
            root = ET.fromstring(response_text)
            ns = {'ns': 'http://www.soap.servicios.hospedajes.mir.es/comunicacion'}
            error_code = root.find('.//ns:codigo', ns).text
            error_msg = root.find('.//ns:descripcion', ns).text
            return f"SES Error {error_code}: {error_msg}"
        except Exception as e:
            return f"Error parsing response: {str(e)}"

    def _log_submission(self, check_in, success, message):
        """Create police submission log entry"""
        PoliceSubmissionLog.objects.create(
            check_in=check_in,
            status='success' if success else 'failed',
            raw_request=message,
            submitted_at=timezone.now()
        )
        if success:
            check_in.status = CheckInStatus.SUBMITTED
            check_in.submission_date = timezone.now()
            check_in.save()


def send_checkin_confirmation(check_in):
    """
    Send confirmation email to lead guest after successful check-in
    """
    reservation = check_in.reservation
    property = reservation.property_ref
    
    subject = f"Reservatiopn Confirmation for {property.name}"
    html_content = render_to_string('email_templates/checkin_confirmation.html', {
        'check_in': check_in,
        'reservation': reservation,
        'property': property,
    })
    try:
        email = Email(subject=subject)  
        email.to(reservation.lead_guest_email)
        if property.owner.email:
            email.cc(property.owner.email)
        email.add_html(html_content)
        email.send()
    except Exception as e:
        logger.error(f"Failed to send check-in confirmation email: {str(e)}")
        raise

def send_police_submission_notification(check_in, success=True):
    """
    Send notification to property owner about police submission status
    """
    status = "successful" if success else "failed"
    subject = f"Police Submission {status} for {check_in.reservation.property_ref.name}"
    html_content = render_to_string('email_templates/police_submission.html', {
        'check_in': check_in,
        'property': check_in.reservation.property_ref,
        'success': success,
    })
    try:
        email = Email(subject=subject)
        email.to(check_in.reservation.property_ref.owner.email)
        if hasattr(settings, 'EMAIL_HOST_USER') and settings.EMAIL_HOST_USER:
            email.cc(settings.EMAIL_HOST_USER)
        email.add_html(html_content)
        email.send()
    except Exception as e:
        logger.error(f"Failed to send police submission notification: {str(e)}")
        raise

def send_checkin_link_email(reservation, recipient_email, recipient_name=None):
    """
    Send the check-in form link to the lead guest
    """
    property = reservation.property_ref
    subject = f"Check-in Form for {property.name}"
    html_content = render_to_string('email_templates/checkin_link.html', {
        'reservation': reservation,
        'property': property,
        'recipient_name': recipient_name,
        'check_in_link': reservation.check_in_link,
        "FRONTEND_URL":settings.FRONTEND_URL
    })
    try:
        email = Email(subject=subject)
        email.to(recipient_email, name=recipient_name)
        if property.owner.email:
            email.cc(property.owner.email)
        email.add_html(html_content)
        email.send()
    except Exception as e:
        logger.error(f"Failed to send check-in link email: {str(e)}")
        raise
