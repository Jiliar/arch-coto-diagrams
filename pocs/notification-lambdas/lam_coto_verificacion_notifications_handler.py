import json
import boto3
import os
import logging
import pymysql
from datetime import datetime

# Configuración de logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# Configuración de AWS
sqs = boto3.client('sqs')

# Variables de entorno
AURORA_DB_HOST = os.environ['AURORA_DB_HOST']
AURORA_DB_USER = os.environ['AURORA_DB_USER']
AURORA_DB_PASSWORD = os.environ['AURORA_DB_PASSWORD']
AURORA_DB_NAME = os.environ['AURORA_DB_NAME']
SQS_AUDIT_QUEUE_URL = os.environ['SQS_AUDIT_QUEUE_URL']

# Conexión a Aurora
def get_db_connection():
    return pymysql.connect(
        host=AURORA_DB_HOST,
        user=AURORA_DB_USER,
        password=AURORA_DB_PASSWORD,
        database=AURORA_DB_NAME,
        cursorclass=pymysql.cursors.DictCursor
    )

def send_audit_event(transaction_id, path, request_body, transaction_output):
    """Envía un mensaje de auditoría a SQS."""
    audit_message = {
        "transaction_id": transaction_id,
        "type": "otp-verification",
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d-%H.%M.%S.%f"),
        "path": path,
        "request_body": request_body,
        "transaction_output": transaction_output
    }
    sqs.send_message(QueueUrl=SQS_AUDIT_QUEUE_URL, MessageBody=json.dumps(audit_message))
    logger.info(f"Evento de auditoría enviado: {audit_message}")

def lambda_handler(event, context):
    """Maneja la verificación del OTP desde API Gateway."""
    try:
        request_id = context.aws_request_id if context else "N/A"
        path = event.get("path", "/otp/verify")

        # Obtener headers
        headers = event.get("headers", {})
        user_id = headers.get("X-UserId")
        username = headers.get("X-UserName")

        if not user_id or not username:
            return {
                "statusCode": 400,
                "body": json.dumps({"code": "MISSING_HEADERS", "message": "X-UserId y X-UserName son requeridos."})
            }

        # Obtener el cuerpo de la petición
        body = json.loads(event.get("body", "{}"))
        otp = body.get("otp")
        transaction_type = body.get("transaction_type")

        if not otp or not transaction_type:
            return {
                "statusCode": 400,
                "body": json.dumps({"code": "MISSING_FIELDS", "message": "OTP y transaction_type son requeridos."})
            }

        # Conectar a la base de datos y buscar el OTP
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM otps 
                WHERE user_id = %s AND otp = %s AND transaction_type = %s AND status = TRUE
            """, (user_id, otp, transaction_type))
            otp_record = cursor.fetchone()

        if otp_record:
            # Marcar OTP como usado
            with connection.cursor() as cursor:
                cursor.execute("""
                    UPDATE otps SET status = FALSE 
                    WHERE id = %s
                """, (otp_record["id"],))
                connection.commit()

            connection.close()

            # Enviar evento de auditoría (éxito)
            send_audit_event(request_id, path, body, {"statusCode": 204})

            return {"statusCode": 204}  # No Content (OTP válido)

        else:
            connection.close()
            # Enviar evento de auditoría (fallo)
            error_response = {"code": "OTP_NOT_FOUND", "message": "El OTP es inválido o ha expirado."}
            send_audit_event(request_id, path, body, {"statusCode": 404, "error": error_response})

            return {"statusCode": 404, "body": json.dumps(error_response)}

    except Exception as e:
        logger.error(f"Error en lambda_handler: {str(e)}", exc_info=True)
        error_response = {"code": "INTERNAL_ERROR", "message": "Ocurrió un error en el servidor."}

        # Enviar evento de auditoría (error interno)
        send_audit_event(context.aws_request_id if context else "N/A", "/otp/verify", event.get("body", "{}"), {"statusCode": 500, "error": error_response})

        return {"statusCode": 500, "body": json.dumps(error_response)}