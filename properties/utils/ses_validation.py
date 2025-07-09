from datetime import timezone
import requests,logging, base64, os, io, zipfile, xml.dom.minidom
import urllib3
from io import BytesIO
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


logger = logging.getLogger(__name__)
""" SES Hospedajes Connection Validation functionality."""

# SES_URL = "https://hospedajes.pre-ses.mir.es/hospedajes-web/ws/v1/comunicacion"
SES_URL = "https://hospedajes.ses.mir.es/hospedajes-web/ws/v1/comunicacion"


def generate_ses_xml(property_instance, tipo_operacion="A"):
    """
    Generates SES-compatible XML for property validation.
    """
    today = timezone.now().date()
    check_in_date = today + timezone.timedelta(days=1)
    check_out_date = check_in_date + timezone.timedelta(days=3)
    
    contract_date = today.strftime("%Y-%m-%d")
    check_in = check_in_date.strftime("%Y-%m-%dT14:00:00")
    check_out = check_out_date.strftime("%Y-%m-%dT11:00:00")

    property_ref = property_instance.property_reference or f"PROP-{property_instance.code}"
    guests = property_instance.max_guests if property_instance.max_guests else 1
    country_code = (property_instance.country or "ESP").upper()
    postal = property_instance.postal_code or "00000"
    
    owner = property_instance.owner
    owner_name = owner.first_name if hasattr(owner, 'first_name') and owner.first_name else "Property"
    owner_lastname = owner.last_name if hasattr(owner, 'last_name') and owner.last_name else "Owner"
    owner_email = owner.email if hasattr(owner, 'email') and owner.email else ""
    owner_phone = owner.phone if hasattr(owner, 'phone') and owner.phone else "000000000"

    municipality_code = property_instance.city or "38001"
    xml_template = f"""<?xml version="1.0" encoding="UTF-8"?>
<alt:peticion xmlns:alt="http://www.neg.hospedajes.mir.es/altaParteHospedaje">
  <solicitud>
    <codigoEstablecimiento>{property_instance.establishment_code}</codigoEstablecimiento>
    <comunicacion>
      <contrato>
        <referencia>PRUEBA-ESPAÑA-001</referencia>
        <fechaContrato>{contract_date}</fechaContrato>
        <fechaEntrada>2025-04-18T14:00:00</fechaEntrada>
        <fechaSalida>2025-04-21T11:00:00</fechaSalida>
        <numPersonas>{guests}</numPersonas>
        <numHabitaciones>1</numHabitaciones>
        <pago>
          <tipoPago>EFECT</tipoPago>
          <fechaPago>{contract_date}</fechaPago>
          <medioPago>efectivo</medioPago>
          <titular>{property_instance.name}</titular>
        </pago>
      </contrato>
      <persona>
        <rol>VI</rol>
        <nombre>{owner_name}</nombre>
        <apellido1>{owner_lastname}</apellido1>
        <apellido2>CONEXION</apellido2>
        <tipoDocumento>NIF</tipoDocumento>
        <numeroDocumento>{property_instance.cif_nif}</numeroDocumento>
        <soporteDocumento>123456789</soporteDocumento>
        <fechaNacimiento>1990-01-01</fechaNacimiento>
        <nacionalidad>{country_code}</nacionalidad>
        <sexo>H</sexo>
        <direccion>
          <direccion>{property_instance.address or 'Default Address'}</direccion>
          <direccionComplementaria>Planta 1</direccionComplementaria>
          <codigoMunicipio>{municipality_code}</codigoMunicipio>
          <codigoPostal>{postal}</codigoPostal>
          <pais>{country_code}</pais>
        </direccion>
        <telefono>{owner_phone}</telefono>
        <telefono2>922000000</telefono2>
        <correo>{owner_email}</correo>
        <parentesco>PM</parentesco>
      </persona>
    </comunicacion>
  </solicitud>
</alt:peticion>"""
    return xml_template.strip()

def create_soap_request(landlord_code, base64_content):
    """
    Creates the SOAP envelope with the base64 content
    """
    soap_template = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:com="http://www.soap.servicios.hospedajes.mir.es/comunicacion">
  <soapenv:Header/>
  <soapenv:Body>
    <com:comunicacionRequest>
      <peticion>
        <cabecera>
          <codigoArrendador>{landlord_code}</codigoArrendador>
          <aplicacion>TuriCheck</aplicacion>
          <tipoOperacion>A</tipoOperacion>
          <tipoComunicacion>PV</tipoComunicacion>
        </cabecera>
        <solicitud><![CDATA[{base64_content}]]></solicitud>
      </peticion>
    </com:comunicacionRequest>
  </soapenv:Body>
</soapenv:Envelope>"""
    return soap_template

def zip_and_encode_xml(xml_content):
    """
    Zips the XML content and encodes it in base64
    """
    try:
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr('parte.xml', xml_content.encode('utf-8'))
        zip_buffer.seek(0)
        base64_content = base64.b64encode(zip_buffer.read()).decode('ascii')
        return base64_content
    except Exception as e:
        logger.error(f"Error zipping and encoding XML: {e}")
        raise

def send_validation_request(xml_data, ws_user, ws_password, landlord_code):
    """
    Sends the validation request to SES with the provided XML data.
    """
    try:
        base64_content = zip_and_encode_xml(xml_data)
        soap_request = create_soap_request(landlord_code, base64_content)
        auth_token = base64.b64encode(f"{ws_user}:{ws_password}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth_token}",
            "Content-Type": "text/xml; charset=UTF-8",
            "Accept": "application/xml",
            "User-Agent": "TuriCheck/1.0"
        }
        cert_path = ("/home/ts/Downloads/cert.pem", "/home/ts/Downloads/key.pem")
        response = requests.post(
            url=SES_URL,
            data=soap_request.encode("utf-8"),
            headers=headers,
            cert=cert_path,
            verify=False,
        )
        if response.status_code == 200:
            if ("<codigo>0</codigo>" in response.text or 
                ("<codigo>10121</codigo>" in response.text and "Lote duplicado" in response.text)):
                return True, "Valid SES credentials"
            else:
                return False, f"Error in response: {response.text}"
        else:
            return False, f"HTTP Error {response.status_code}: {response.text}"
    except Exception as e:
        logger.error(f"SES validation request failed: {e}")
        return False, str(e)


## FOR PRODUCTION RESTRICT DUPLICATION WITH SAME DATA OF PROPERTY 
# if response.status_code == 200:
#             if ("<codigoRespuesta>0" in response.text or 
#                 "<codigoError>0</codigoError>" in response.text or 
#                 "<codigo>0</codigo>" in response.text):
#                 return True, "Valid SES credentials"
#             else:
#                 return False, f"Error in response: {response.text}"