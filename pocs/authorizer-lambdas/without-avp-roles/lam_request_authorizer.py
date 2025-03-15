import json
import jwt  # PyJWT library (pip install PyJWT)
import boto3
import urllib.request
import logging
import os
import uuid
from datetime import datetime

# Configuración de logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# Configuración de AWS
sqs = boto3.client('sqs')

# Variables de entorno
COGNITO_USERPOOL_ID = os.environ.get("COGNITO_USERPOOL_ID", "us-east-1_example")
COGNITO_REGION = os.environ.get("COGNITO_REGION", "us-east-1")
SQS_AUDIT_QUEUE_URL = os.environ.get("SQS_AUDIT_QUEUE_URL")

COGNITO_ISSUER = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USERPOOL_ID}"
COGNITO_JWKS_URL = f"{COGNITO_ISSUER}/.well-known/jwks.json"

# Obtener claves públicas de Cognito
def get_cognito_public_keys():
    try:
        logger.info(f"Descargando claves públicas de Cognito desde {COGNITO_JWKS_URL}")
        response = urllib.request.urlopen(COGNITO_JWKS_URL)
        keys = json.loads(response.read())["keys"]
        return {key["kid"]: key for key in keys}
    except Exception as e:
        logger.error(f"Error al obtener claves públicas de Cognito: {str(e)}", exc_info=True)
        return {}

COGNITO_KEYS = get_cognito_public_keys()

# Enviar evento de auditoría a SQS
def send_audit_event(transaction_id, path, request_body, transaction_output, request_id):
    """Envía un mensaje de auditoría a SQS."""
    audit_message = {
        "transaction_id": transaction_id,
        "type": "auth-request",
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d-%H.%M.%S.%f"),
        "path": path,
        "request_body": request_body,
        "transaction_output": transaction_output,
        "aws_request_id": request_id
    }
    try:
        sqs.send_message(QueueUrl=SQS_AUDIT_QUEUE_URL, MessageBody=json.dumps(audit_message))
        logger.info(f"Evento de auditoría enviado: {audit_message}")
    except Exception as e:
        logger.error(f"Error al enviar evento de auditoría: {str(e)}", exc_info=True)

# Validar JWT con Cognito
def validate_jwt(token):
    try:
        headers = jwt.get_unverified_header(token)
        key = COGNITO_KEYS.get(headers["kid"])

        if not key:
            raise Exception("No matching key found for JWT validation")

        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
        decoded_token = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=COGNITO_ISSUER
        )

        logger.info(f"JWT validado con éxito para el usuario {decoded_token.get('sub')}")
        return decoded_token

    except Exception as e:
        logger.error(f"Error al validar JWT: {str(e)}", exc_info=True)
        raise

# Función principal del Lambda Authorizer
def lambda_handler(event, context):
    request_id = context.aws_request_id if context else "N/A"
    transaction_id = str(uuid.uuid4())
    logger.info(f"Lambda Authorizer iniciado. Request ID: {request_id}, Transaction ID: {transaction_id}")

    try:
        # Extraer el token de autorización
        token = event["authorizationToken"].split(" ")[1]  # Bearer <token>
        logger.info("Token de autorización recibido, iniciando validación...")

        # Validar y decodificar el JWT
        decoded_token = validate_jwt(token)

        # Extraer datos del JWT
        user_id = decoded_token.get("sub")
        email = decoded_token.get("email", "N/A")
        org_id = decoded_token.get("custom:orgId", "N/A")  # Atributo personalizado

        # Construir la política de autorización
        policy = {
            "principalId": user_id,
            "policyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Action": "execute-api:Invoke",
                        "Effect": "Allow",
                        "Resource": event["methodArn"]
                    }
                ]
            },
            "context": {
                "userId": user_id,
                "email": email,
                "orgId": org_id
            }
        }

        transaction_output = {"status": "ALLOW", "userId": user_id, "orgId": org_id}
        send_audit_event(transaction_id, "/auth/validate", {"token": "***"}, transaction_output, request_id)

        logger.info(f"Autorización concedida para el usuario {user_id}. Contexto generado: {policy['context']}")
        return policy

    except Exception as e:
        logger.warning("Fallo en la autorización. Retornando política de denegación.", exc_info=True)
        transaction_output = {"status": "DENY", "error": str(e)}
        send_audit_event(transaction_id, "/auth/validate", {"token": "***"}, transaction_output, request_id)

        return {
            "principalId": "unauthorized",
            "policyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Action": "execute-api:Invoke",
                        "Effect": "Deny",
                        "Resource": event["methodArn"]
                    }
                ]
            }
        }