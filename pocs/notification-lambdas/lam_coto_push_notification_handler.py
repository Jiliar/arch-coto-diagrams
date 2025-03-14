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
sqs = boto3.client('sqs')
sns = boto3.client('sns')

# Variables de entorno
SQS_QUEUE_URL = os.environ['SQS_QUEUE_URL']  
SNS_TARGET_TOPIC_ARN = os.environ['SNS_TARGET_TOPIC_ARN']
SQS_AUDIT_QUEUE_URL = os.environ['SQS_COTO_AUDIT_QUEUE']

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

def process_sqs_messages(path:str, transaction_id:str, request_id:str):
    """Lee los mensajes de la cola SQS y los publica en el topic SNS de destino."""

    try:
        logger.info(f"Esperando mensajes en la cola SQS: {SQS_QUEUE_URL}")
        messages = sqs.receive_message(
            QueueUrl=SQS_QUEUE_URL,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=5
        )

        if "Messages" not in messages:
            logger.info("No hay mensajes en la cola SQS")
            transaction_output = {"message": "No hay mensajes en la cola"}
            send_audit_event(transaction_id, path, {}, transaction_output, request_id)
            return {"statusCode": 200, "body": transaction_output}

        processed_messages = []

        for message in messages["Messages"]:
            receipt_handle = message["ReceiptHandle"]
            sqs_message = json.loads(message["Body"])  

            transaction_id = sqs_message.get("transaction_id", transaction_id)
            title = sqs_message.get("title", "Notificación")
            message_body = sqs_message.get("body", "Sin contenido")
            priority = sqs_message.get("priority", "normal")
            data = sqs_message.get("data", {})
            recipients = sqs_message.get("recipients", [])

            if not recipients:
                logger.warning(f"Mensaje sin destinatarios. Se descartará: {sqs_message}")
                sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)
                continue

            # Construir el mensaje procesado
            processed_message = {
                "title": title,
                "body": message_body,
                "priority": priority,
                "data": data,
                "recipients": recipients
            }

            logger.info(f"Publicando notificación Push en SNS. Destinatarios: {len(recipients)}")

            # Publicar el mensaje en SNS para su entrega final
            sns.publish(
                TopicArn=SNS_TARGET_TOPIC_ARN,
                Message=json.dumps(processed_message),
                Subject="Push Notification Processed"
            )

            # Eliminar el mensaje de la cola SQS tras procesarlo correctamente
            sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)
            logger.info("Mensaje procesado y eliminado de la cola SQS")

            processed_messages.append({"push_processed_message" : processed_message, "transaction_id": transaction_id})

        transaction_output = {"processed_messages": processed_messages}
        send_audit_event(transaction_id, path, messages, transaction_output, request_id)

        return {"statusCode": 200, "body": "Mensajes de Push procesados y enviados a SNS"}

    except Exception as e:
        logger.error(f"Error en process_sqs_messages: {str(e)}", exc_info=True)
        transaction_output = {"error": str(e)}
        send_audit_event(transaction_id, path, {}, transaction_output, request_id)
        return {"statusCode": 500, "body": transaction_output}

def lambda_handler(event, context):
    """Lambda que procesa los mensajes de SQS y los publica en SNS para entrega final."""
    request_id = context.aws_request_id if context else "N/A"
    transaction_id = str(uuid.uuid4())

    logger.info(f"Lambda ejecutada. Request ID: {request_id}, Transaction ID: {transaction_id}")

    path = "/users/push"
    return process_sqs_messages(path, transaction_id, request_id)
