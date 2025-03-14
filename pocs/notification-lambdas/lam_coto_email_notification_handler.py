import json
import boto3
import os
import logging
import uuid
from datetime import datetime

# Configuración de logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# Configuración de AWS
s3 = boto3.client('s3')
sqs = boto3.client('sqs')
sns = boto3.client('sns')

# Variables de entorno
S3_BUCKET_NAME = os.environ['S3_BUCKET_NAME_EMAIL']
TEMPLATE_FILE_NAME = os.environ['TEMPLATE_FILE_NAME_EMAIL']
SNS_TOPIC_ARN = os.environ['SNS_TOPIC_ARN_EMAIL']
SQS_QUEUE_URL = os.environ['SQS_QUEUE_URL_EMAIL']
SQS_AUDIT_QUEUE_URL = os.environ['SQS_COTO_AUDIT_QUEUE_EMAIL']

def send_audit_event(transaction_id, path, request_body, transaction_output, request_id):
    """Envía un mensaje de auditoría a SQS."""
    audit_message = {
        "transaction_id": transaction_id,
        "type": "sms-notification",
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d-%H.%M.%S.%f"),
        "path": path,
        "request_body": request_body,
        "transaction_output": transaction_output,
        "aws_request_id": request_id
    }
    sqs.send_message(QueueUrl=SQS_AUDIT_QUEUE_URL, MessageBody=json.dumps(audit_message))
    logger.info(f"Evento de auditoría enviado: {audit_message}")

def get_email_template():
    """Descarga la plantilla HTML desde S3 y la devuelve como string."""
    try:
        logger.info(f"Descargando plantilla HTML '{TEMPLATE_FILE_NAME}' desde S3 bucket '{S3_BUCKET_NAME}'")
        response = s3.get_object(Bucket=S3_BUCKET_NAME, Key=TEMPLATE_FILE_NAME)
        template = response['Body'].read().decode('utf-8')
        logger.info("Plantilla HTML descargada con éxito")
        return template
    except Exception as e:
        logger.error(f"Error al obtener la plantilla HTML de S3: {str(e)}", exc_info=True)
        return None

def process_sqs_messages(path:str, transaction_id:str, request_id:str):
    """Lee los mensajes de la cola SQS y los procesa uno por uno."""

    try:
        logger.info(f"Esperando mensajes en la cola SQS: {SQS_QUEUE_URL}")
        messages = sqs.receive_message(
            QueueUrl=SQS_QUEUE_URL,
            MaxNumberOfMessages=10,  # Leer hasta 10 mensajes a la vez
            WaitTimeSeconds=5
        )

        if "Messages" not in messages:
            logger.info("No hay mensajes en la cola SQS")
            transaction_output = {"message": "No hay mensajes en la cola"}
            send_audit_event(transaction_id, path, {}, transaction_output, request_id)
            return {"statusCode": 200, "body": transaction_output}

        email_template = get_email_template()
        if not email_template:
            logger.error("Error al obtener la plantilla HTML. No se pueden procesar los mensajes")
            transaction_output = {"error": "Error al obtener la plantilla HTML"}
            send_audit_event(transaction_id, path, {}, transaction_output, request_id)
            return {"statusCode": 500, "body": transaction_output}

        processed_messages = []

        for message in messages["Messages"]:
            receipt_handle = message["ReceiptHandle"]
            sns_message = json.loads(message["Body"])  # Extraer contenido del mensaje
            
            transaction_id = sns_message.get("transaction_id", transaction_id)
            subject = sns_message.get("subject", "Sin Asunto")
            body = sns_message.get("body", "")
            sender = sns_message.get("from", "no-reply@miempresa.com")
            recipients = sns_message.get("recipients", [])

            if not recipients:
                logger.warning(f"Mensaje con subject '{subject}' no tiene destinatarios. Se descartará.")
                sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)
                continue

            # Reemplazar %{body}% en la plantilla con el contenido real del email
            email_content = email_template.replace("%{body}%", body)
            
            # Construir el mensaje procesado
            processed_message = {
                "subject": subject,
                "body": email_content,
                "from": sender,
                "recipients": recipients
            }

            logger.info(f"Publicando mensaje en SNS. Subject: {subject}, Destinatarios: {len(recipients)}")

            # Publicar el mensaje en SNS para su entrega final
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=json.dumps(processed_message),
                Subject=subject
            )

            # Eliminar el mensaje de la cola una vez procesado correctamente
            sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)
            logger.info(f"Mensaje procesado y eliminado de la cola SQS: {subject}")

            processed_messages.append({"email_processed_message" : processed_message, "transaction_id": transaction_id})

        transaction_output = {"processed_messages": processed_messages}
        send_audit_event(transaction_id, path, messages, transaction_output, request_id)

        return {"statusCode": 200, "body": "Mensajes procesados y enviados a SNS"}

    except Exception as e:
        logger.error(f"Error en process_sqs_messages: {str(e)}", exc_info=True)
        transaction_output = {"error": str(e)}
        send_audit_event(transaction_id, path, {}, transaction_output, request_id)
        return {"statusCode": 500, "body": transaction_output}

def lambda_handler(event, context):
    """Lambda que procesa los mensajes de SQS y publica en SNS para entrega final."""
    request_id = context.aws_request_id if context else "N/A"
    transaction_id = str(uuid.uuid4())

    logger.info(f"Lambda ejecutada. Request ID: {request_id}, Transaction ID: {transaction_id}")

    path = "/users/emails"
    return process_sqs_messages(path, transaction_id, request_id)