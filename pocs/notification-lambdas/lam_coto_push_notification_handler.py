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
sns = boto3.client('sns')
sqs = boto3.client('sqs')

# Variables de entorno
SNS_TARGET_TOPIC_ARN = os.environ['SNS_TARGET_TOPIC_ARN']
SQS_AUDIT_QUEUE_URL = os.environ['SQS_COTO_AUDIT_QUEUE']

def send_audit_event(transaction_id, path, request_body, transaction_output, request_id):
    """Envía un mensaje de auditoría a SQS."""
    audit_message = {
        "transaction_id": transaction_id,
        "type": "push-notification",
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d-%H.%M.%S.%f"),
        "path": path,
        "request_body": request_body,
        "transaction_output": transaction_output,
        "aws_request_id": request_id
    }
    sqs.send_message(QueueUrl=SQS_AUDIT_QUEUE_URL, MessageBody=json.dumps(audit_message))
    logger.info(f"Evento de auditoría enviado: {audit_message}")

def process_sns_event(event, request_id):
    """Procesa los mensajes recibidos desde SNS y los publica en otro SNS Topic."""
    transaction_id = str(uuid.uuid4())
    path = "/users/push"

    try:
        # Extraer los registros del evento SNS
        records = event.get("Records", [])
        if not records:
            logger.warning("Evento SNS sin registros")
            return {"statusCode": 400, "body": "No hay registros en el evento SNS"}

        processed_messages = []

        for record in records:
            sns_message = json.loads(record["Sns"]["Message"])  # Extraer el mensaje de SNS
            title = sns_message.get("title", "Notificación")
            message_body = sns_message.get("body", "Sin contenido")
            priority = sns_message.get("priority", "normal")
            data = sns_message.get("data", {})
            recipients = sns_message.get("recipients", [])

            if not recipients:
                logger.warning(f"Mensaje sin destinatarios. Se descartará: {sns_message}")
                continue

            # Construir el mensaje procesado
            processed_message = {
                "title": title,
                "body": message_body,
                "priority": priority,
                "data": data,
                "recipients": recipients
            }

            logger.info(f"Publicando notificación Push en SNS destino. Destinatarios: {len(recipients)}")

            # Publicar en otro SNS Topic para su entrega final
            sns.publish(
                TopicArn=SNS_TARGET_TOPIC_ARN,
                Message=json.dumps(processed_message),
                Subject="Push Notification Processed"
            )

            processed_messages.append({"push_processed_message": processed_message, "transaction_id": transaction_id})

        transaction_output = {"processed_messages": processed_messages}
        send_audit_event(transaction_id, path, event, transaction_output, request_id)

        return {"statusCode": 200, "body": "Mensajes de Push procesados y enviados a SNS"}

    except Exception as e:
        logger.error(f"Error en process_sns_event: {str(e)}", exc_info=True)
        transaction_output = {"error": str(e)}
        send_audit_event(transaction_id, path, event, transaction_output, request_id)
        return {"statusCode": 500, "body": transaction_output}

def lambda_handler(event, context):
    """Lambda que procesa eventos SNS y los publica en otro SNS Topic."""
    request_id = context.aws_request_id if context else "N/A"
    logger.info(f"Lambda ejecutada. Request ID: {request_id}")

    return process_sns_event(event, request_id)