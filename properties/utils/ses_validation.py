import requests,logging, base64, os, io, zipfile, xml.dom.minidom
import urllib3
from io import BytesIO
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


logger = logging.getLogger(__name__)
""" SES Hospedajes Connection Validation functionality."""

# SES_URL = "https://hospedajes.pre-ses.mir.es/hospedajes-web/ws/v1/comunicacion"
SES_URL = "https://hospedajes.ses.mir.es/hospedajes-web/ws/v1/comunicacion"


def generate_ses_xml(est_code, tipo_operacion="A"):
    """
    Generates SES-compatible XML for property validation.
    """
    xml_template = f"""<?xml version="1.0" encoding="UTF-8"?>
<alt:peticion xmlns:alt="http://www.neg.hospedajes.mir.es/altaParteHospedaje">
  <solicitud>
    <codigoEstablecimiento>{est_code}</codigoEstablecimiento>
    <comunicacion>
      <contrato>
        <referencia>PRUEBA-ESPAÑA-001</referencia>
        <fechaContrato>2025-04-17</fechaContrato>
        <fechaEntrada>2025-04-18T14:00:00</fechaEntrada>
        <fechaSalida>2025-04-21T11:00:00</fechaSalida>
        <numPersonas>1</numPersonas>
        <numHabitaciones>1</numHabitaciones>
        <pago>
          <tipoPago>EFECT</tipoPago>
          <fechaPago>2025-04-17</fechaPago>
          <medioPago>efectivo</medioPago>
          <titular>PRUEBA</titular>
        </pago>
      </contrato>
      <persona>
        <rol>VI</rol>
        <nombre>TuriCheck</nombre>
        <apellido1>VALIDAR</apellido1>
        <apellido2>CONEXION</apellido2>
        <tipoDocumento>NIF</tipoDocumento>
        <numeroDocumento>00000000T</numeroDocumento>
        <soporteDocumento>123456789</soporteDocumento>
        <fechaNacimiento>1990-01-01</fechaNacimiento>
        <nacionalidad>ESP</nacionalidad>
        <sexo>H</sexo>
        <direccion>
          <direccion>Calle El Sauce 9</direccion>
          <direccionComplementaria>Planta 1</direccionComplementaria>
          <codigoMunicipio>38001</codigoMunicipio>
          <codigoPostal>38670</codigoPostal>
          <pais>ESP</pais>
        </direccion>
        <telefono>634000000</telefono>
        <telefono2>922000000</telefono2>
        <correo>info@turicheck.com</correo>
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
        # Create zip file in memory
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Use writestr method correctly
            zip_file.writestr('parte.xml', xml_content.encode('utf-8'))
        
        # Get the bytes content and encode in base64
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
        print('base64_content: ', base64_content)
        
        soap_request = create_soap_request(landlord_code, base64_content)
        print('soap_request: ', soap_request)
        
        auth_token = base64.b64encode(f"{ws_user}:{ws_password}".encode()).decode()
        
        headers = {
            "Authorization": f"Basic {auth_token}",
            "Content-Type": "text/xml; charset=UTF-8",
            "Accept": "application/xml",
            "User-Agent": "TuriCheck/1.0"
        }
        
        # Path to certificates - update these paths as needed for your environment
        cert_path = ("/home/ts/Downloads/cert.pem", "/home/ts/Downloads/key.pem")
        
        logger.info(f"Sending SES validation request to {SES_URL}")
        
        response = requests.post(
            url=SES_URL,
            data=soap_request.encode("utf-8"),
            headers=headers,
            cert=cert_path,
            verify=False,  # Consider setting this to True in production with proper CA certs
        )
        
        logger.info(f"SES response status code: {response.status_code}")        
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