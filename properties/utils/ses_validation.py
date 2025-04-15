import requests,logging, base64
import certifi
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


logger = logging.getLogger(__name__)
""" SES Hospedajes Connection Validation functionality."""

SES_URL = "https://hospedajes.pre-ses.mir.es/hospedajes-web/ws/v1/comunicacion"

def generate_ses_xml(ws_user, ws_password, est_code, landlord_code, tipo_operacion="ALTA"):
    """
    Generates SES-compatible XML for property validation.
    """
    xml_template = f"""<?xml version="1.0" encoding="UTF-8"?>
    <comunicacion>
    <cabecera>
        <usuario>{ws_user}</usuario>
        <password>{ws_password}</password>
        <codigoEstablecimiento>{est_code}</codigoEstablecimiento>
        <codigoArrendador>{landlord_code}</codigoArrendador>
        <tipoOperacion>{tipo_operacion}</tipoOperacion>
    </cabecera>
    <partes>
        <viajero>
        <nombre>Nombre Test</nombre>
        <apellido1>Apellido1</apellido1>
        <apellido2>Apellido2</apellido2>
        <tipoDocumento>DNI</tipoDocumento>
        <numeroDocumento>12345678A</numeroDocumento>
        <fechaNacimiento>1990-01-01</fechaNacimiento>
        <sexo>H</sexo>
        <nacionalidad>ESP</nacionalidad>
        <fechaEntrada>2025-04-08</fechaEntrada>
        </viajero>
    </partes>
    </comunicacion>
    """
    return xml_template.strip()


def send_validation_request(xml_data, ws_user, ws_password, verify_ssl=True):
    """
    Sends the validation request to SES with the provided XML data.
    Uses HTTP Basic Authentication.
    """
    try:
        auth_token = base64.b64encode(f"{ws_user}:{ws_password}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth_token}",
            "Content-Type": "application/xml",
            "Accept": "application/xml"
        }
        verify = certifi.where() if verify_ssl else False

        response = requests.post(SES_URL, data=xml_data, headers=headers, verify=False)

        logger.info(f"SES request sent. Status: {response.status_code}")
        logger.debug(f"Request headers: {headers}")
        logger.debug(f"Request XML: {xml_data}")
        logger.debug(f"Response: {response.text}")

        if response.status_code == 200 and "<Success>" in response.text:
            return True, "Valid SES credentials"
        return False, response.text

    except Exception as e:
        logger.error(f"SES validation request failed: {e}")
        return False, str(e)