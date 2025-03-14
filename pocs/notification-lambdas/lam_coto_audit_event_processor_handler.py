import json
import boto3
import os
import logging
from datetime import datetime

# Configuración de logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# Configuración de AWS
sqs = boto3.client('sqs')
dynamodb = boto3.resource('dynamodb')

# Variables de entorno
SQS_QUEUE_URL = os.environ['SQS_AUDIT_QUEUE_URL']  # URL de la cola SQS
DYNAMODB_TABLE_NAME = os.environ['DYNAMODB_AUDIT_TABLE']  # Nombre de la tabla DynamoDB

# Referencia a la tabla DynamoDB
audit_table = dynamodb.Table(DYNAMODB_TABLE_NAME)

def process_sqs_messages(event, request_id):
    """Procesa los mensajes recibidos de la cola SQS, los guarda en DynamoDB y confirma el ACK."""
    try:
        logger.info(f"Recibiendo {len(event['Records'])} mensajes desde SQS...")

        for record in event['Records']:
            # Extraer el cuerpo del mensaje desde SQS
            sqs_message = json.loads(record['body'])

            # Extraer los datos esperados del mensaje
            transaction_id = sqs_message.get("transaction_id", "N/A")
            request_body = sqs_message.get("request_body", {})
            transaction_output = sqs_message.get("transaction_output", {})
            path = sqs_message.get("path", "N/A")
            request_type = sqs_message.get("type", "unknown")

            # Generar el evento a almacenar en DynamoDB
            audit_event = {
                "transaction_id": transaction_id,
                "type": request_type,
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d-%H.%M.%S.%f"),
                "path": path,
                "request_body": request_body,
                "transaction_output": transaction_output,
                "aws_request_id": request_id
            }

            # Guardar en DynamoDB
            audit_table.put_item(Item=audit_event)
            logger.info(f"Evento audit guardado en DynamoDB: {audit_event}")

            # ACK: Confirmar recepción eliminando el mensaje de la cola SQS
            receipt_handle = record["receiptHandle"]
            sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)
            logger.info(f"Mensaje con transaction_id {transaction_id} eliminado de SQS (ACK enviado)")

        return {"statusCode": 200, "body": "Mensajes procesados y guardados en audit_events"}

    except Exception as e:
        logger.error(f"Error procesando mensajes SQS: {str(e)}", exc_info=True)
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

def lambda_handler(event, context):
    """Lambda Handler para procesar mensajes de la cola SQS."""
    request_id = context.aws_request_id if context else "N/A"
    logger.info(f"Lambda ejecutada. Request ID: {request_id}")
    return process_sqs_messages(event, request_id)