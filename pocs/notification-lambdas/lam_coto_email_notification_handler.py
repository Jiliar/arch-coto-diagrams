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
sns = boto3.client('sns')
sqs = boto3.client('sqs')

# Variables de entorno
S3_BUCKET_NAME = os.environ['S3_BUCKET_NAME_EMAIL']
TEMPLATE_FILE_NAME = os.environ['TEMPLATE_FILE_NAME_EMAIL']
SNS_DESTINATION_ARN = os.environ['SNS_DESTINATION_ARN_EMAIL']
SQS_AUDIT_QUEUE_URL = os.environ['SQS_COTO_AUDIT_QUEUE_EMAIL']

def send_audit_event(transaction_id, path, request_body, transaction_output, request_id):
    """Envía un mensaje de auditoría a SQS."""
    audit_message = {
        "transaction_id": transaction_id,
        "type": "email-notification",
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

def process_sns_event(event, request_id):
    """Procesa los mensajes recibidos desde SNS."""
    transaction_id = str(uuid.uuid4())
    path = "/users/emails"

    try:
        # Extraer el mensaje de SNS
        records = event.get("Records", [])
        if not records:
            logger.warning("Evento SNS sin registros")
            return {"statusCode": 400, "body": "No hay registros en el evento SNS"}

        email_template = get_email_template()
        if not email_template:
            error_msg = "Error al obtener la plantilla HTML"
            send_audit_event(transaction_id, path, {}, {"error": error_msg}, request_id)
            return {"statusCode": 500, "body": {"error": error_msg}}

        processed_messages = []

        for record in records:
            sns_message = json.loads(record["Sns"]["Message"])  # Extraer mensaje del SNS
            subject = sns_message.get("subject", "Sin Asunto")
            body = sns_message.get("body", "")
            sender = sns_message.get("from", "no-reply@miempresa.com")
            recipients = sns_message.get("recipients", [])

            if not recipients:
                logger.warning(f"Mensaje con subject '{subject}' no tiene destinatarios. Se descartará.")
                continue

            # Reemplazar %{body}% en la plantilla con el contenido real del email
            email_content = email_template.replace("%{body}%", body)

            processed_message = {
                "subject": subject,
                "body": email_content,
                "from": sender,
                "recipients": recipients
            }

            logger.info(f"Publicando mensaje en SNS destino. Subject: {subject}, Destinatarios: {len(recipients)}")

            # Publicar en otro SNS para entrega final
            sns.publish(
                TopicArn=SNS_DESTINATION_ARN,
                Message=json.dumps(processed_message),
                Subject=subject
            )

            processed_messages.append({"email_processed_message": processed_message, "transaction_id": transaction_id})

        transaction_output = {"processed_messages": processed_messages}
        send_audit_event(transaction_id, path, event, transaction_output, request_id)

        return {"statusCode": 200, "body": "Mensajes procesados y enviados a SNS"}

    except Exception as e:
        logger.error(f"Error en process_sns_event: {str(e)}", exc_info=True)
        transaction_output = {"error": str(e)}
        send_audit_event(transaction_id, path, event, transaction_output, request_id)
        return {"statusCode": 500, "body": transaction_output}

def lambda_handler(event, context):
    """Lambda que procesa eventos SNS y publica en otro SNS."""
    request_id = context.aws_request_id if context else "N/A"
    logger.info(f"Lambda ejecutada. Request ID: {request_id}")

    return process_sns_event(event, request_id)