import json
import boto3
import random
import psycopg2
import os
import logging
import uuid
from datetime import datetime

# Configuración de logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# Configuración de AWS
sqs = boto3.client('sqs')
queue_url_email = os.environ['SQS_QUEUE_URL_EMAIL']
queue_url_sms = os.environ['SQS_QUEUE_URL_SMS']
queue_url_push = os.environ['SQS_QUEUE_URL_PUSH']
queue_url_audit = os.environ['SQS_COTO_AUDIT_QUEUE']

# Configuración de Aurora
DB_HOST = os.environ['DB_HOST']
DB_USER = os.environ['DB_USER']
DB_PASS = os.environ['DB_PASS']
DB_NAME = os.environ['DB_NAME']

def get_db_connection():
    """Establece y devuelve una conexión a la base de datos."""
    return psycopg2.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        dbname=DB_NAME
    )

def send_audit_event(transaction_id, path, request_body, transaction_output, request_id):
    """Envía un mensaje de auditoría a SQS."""
    audit_message = {
        "transaction_id": transaction_id,
        "type": "prepare-notification",
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d-%H.%M.%S.%f"),
        "path": path,
        "request_body": request_body,
        "transaction_output": transaction_output,
        "aws_request_id": request_id
    }
    sqs.send_message(QueueUrl=queue_url_audit, MessageBody=json.dumps(audit_message))
    logger.info(f"Evento de auditoría enviado: {audit_message}")

def get_user_data(query, user_id, field_name):
    """Consulta la vista de Aurora para obtener un dato específico del usuario."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(query, (user_id,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        if result:
            logger.info(f"Datos obtenidos para user_id {user_id}: {field_name} = {result[0]}")
            return result[0]
        else:
            logger.warning(f"No se encontró {field_name} para user_id {user_id}")
            return None
    except Exception as e:
        logger.error(f"Error obteniendo {field_name} para user_id {user_id}: {str(e)}")
        return None

def get_user_email(user_id):
    return get_user_data("SELECT email FROM UsersTable WHERE user_id = %s;", user_id, "email")

def get_user_phone(user_id):
    return get_user_data("SELECT phone FROM UsersTable WHERE user_id = %s;", user_id, "phone")

def get_user_device_token(user_id):
    return get_user_data("SELECT device_token FROM UsersTable WHERE user_id = %s;", user_id, "device_token")

def generate_otp():
    """Genera un OTP de 6 dígitos."""
    otp = str(random.randint(100000, 999999))
    logger.info(f"OTP generado: {otp}")
    return otp

def store_otp(user_id, otp, transaction_type):
    """Guarda el OTP en la base de datos Aurora."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO otps (otp, user_id, created_at, status, transaction_type) VALUES (%s, %s, NOW(), %s, %s);",
            (otp, user_id, 'PENDING', transaction_type)
        )
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"OTP almacenado para user_id {user_id}, transaction_type: {transaction_type}")
    except Exception as e:
        logger.error(f"Error almacenando OTP para user_id {user_id}: {str(e)}")

def lambda_handler(event, context):
    """Maneja la solicitud de API Gateway."""
    request_id = context.aws_request_id if context else "N/A"
    transaction_id = str(uuid.uuid4())
    
    logger.info(f"Lambda ejecutada. Request ID: {request_id}, Transaction ID: {transaction_id}")
    
    try:
        if event.get('httpMethod') != 'POST':
            logger.warning("Método no permitido")
            return {"statusCode": 400, "body": json.dumps({"error": "Método no permitido"})}

        path = event.get('path')
        body = json.loads(event['body'])

        request_type = body.get("type", "")
        transaction_type = body.get("transaction-type", "")
        to_users = body.get("to", [])

        processed_recipients = []
        transaction_output = {}

        logger.info(f"Procesando petición en {path}. Usuarios: {to_users}")

        if path == "/users/emails":
            subject = body.get("subject", "Sin asunto")
            message_body = body.get("body", "")
            sender = body.get("from", "no-reply@miempresa.com")

            for user_id in to_users:
                email = get_user_email(user_id)
                if email:
                    recipient_info = {"user_id": user_id, "email": email}
                    processed_recipients.append(recipient_info)

            transaction_output = {"recipients": processed_recipients}

            if processed_recipients:
                message = {"transaction_id": transaction_id, "aws_request_id": request_id, "subject": subject, "body": message_body, "from": sender, "recipients": processed_recipients}
                sqs.send_message(QueueUrl=queue_url_email, MessageBody=json.dumps(message))
                logger.info(f"Mensaje enviado a SQS Email: {message}")

        elif path == "/users/sms":
            message_body = body.get("message", "")
            sender_id = body.get("senderId", "MiEmpresa")

            for user_id in to_users:
                phone = get_user_phone(user_id)
                if phone:
                    recipient_info = {"user_id": user_id, "phone": phone}
                    processed_recipients.append(recipient_info)

            transaction_output = {"recipients": processed_recipients}

            if processed_recipients:
                message = {"transaction_id": transaction_id, "aws_request_id": request_id, "message": message_body, "senderId": sender_id, "recipients": processed_recipients}
                sqs.send_message(QueueUrl=queue_url_sms, MessageBody=json.dumps(message))
                logger.info(f"Mensaje enviado a SQS SMS: {message}")

        elif path == "/users/push":
            title = body.get("title", "Notificación")
            message_body = body.get("body", "")
            priority = body.get("priority", "normal")
            data = body.get("data", {})

            for user_id in to_users:
                device_token = get_user_device_token(user_id)
                if device_token:
                    recipient_info = {"user_id": user_id, "device_token": device_token}
                    processed_recipients.append(recipient_info)

            transaction_output = {"recipients": processed_recipients}

            if processed_recipients:
                message = {"transaction_id": transaction_id, "aws_request_id": request_id, "title": title, "body": message_body, "priority": priority, "data": data, "recipients": processed_recipients}
                sqs.send_message(QueueUrl=queue_url_push, MessageBody=json.dumps(message))
                logger.info(f"Mensaje enviado a SQS Push: {message}")

        else:
            logger.warning(f"Ruta no permitida: {path}")
            transaction_output = {"error": "Ruta no permitida"}
            send_audit_event(transaction_id, path, body, transaction_output, request_id)
            return {"statusCode": 400, "body": json.dumps(transaction_output)}

        send_audit_event(transaction_id, path, body, transaction_output, request_id)

        return {"statusCode": 200, "body": json.dumps({"message": "Mensaje enviado a SQS", "recipients": processed_recipients})}

    except Exception as e:
        logger.error(f"Error en lambda_handler: {str(e)}", exc_info=True)
        transaction_output = {"error": str(e)}
        send_audit_event(transaction_id, path, body, transaction_output, request_id)
        return {"statusCode": 500, "body": json.dumps(transaction_output)}