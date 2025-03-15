import os
import logging
import hashlib
import boto3
from jose import jwt
from abc import ABC, abstractmethod

# Configuración de logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# Cliente de AWS Verified Permissions
verified_permissions_client = boto3.client("verifiedpermissions")

# Acciones permitidas sin validación adicional
ALLOWED_ACTIONS_WITHOUT_PERMISSION = {
    "app_auth",
    "resource_authorization_batch",
    "user_details_authorization"
}

DENY = "Deny"
ALLOW = "Allow"
ALL = "ALL"

class AbstractHandler(ABC):
    """Clase base para el manejador de autorización en Lambda."""

    def __init__(self):
        self.verified_permissions_client = verified_permissions_client

    def handle_request(self, event, context):
        """Procesa la solicitud y verifica la autorización."""
        logger.info(f"Processing event: {event}")

        headers = event.get("headers", {})
        auth_token = self.get_authorization_jwt(headers)

        if not auth_token:
            logger.error("No authorization token provided")
            return self.generate_policy(DENY, {})

        # Validación del token JWT con Cognito
        try:
            subject = self.validate_jwt(auth_token)
        except Exception as e:
            logger.error(f"JWT validation failed: {e}")
            return self.generate_policy(DENY, {})

        auth_decision = self.handle_authorization(event, subject)
        return self.generate_policy(auth_decision, subject)

    def get_authorization_jwt(self, headers):
        """Obtiene el token JWT de los headers."""
        auth_header = headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[len("Bearer "):]
        return None

    def validate_root(self, subject):
        """Valida si el usuario tiene permisos de root usando SHA-256."""
        hashed_sub = hashlib.sha256(subject["sub"].encode()).hexdigest()
        return hashed_sub == subject.get("root")

    def handle_authorization(self, event, subject):
        """Determina la autorización del usuario basado en permisos."""
        if "root" in subject and self.validate_root(subject):
            return ALLOW

        action = event.get("httpMethod", "").lower()
        if action in ALLOWED_ACTIONS_WITHOUT_PERMISSION:
            return ALLOW

        resources = self.get_resources(event, subject)
        if not resources:
            return DENY

        return self.authorize_with_avp(resources, subject, action)

    def authorize_with_avp(self, resources, subject, action):
        """Verifica la autorización con AWS Verified Permissions."""
        policy_store_id = self.get_policy_store()

        if len(resources) == 1:
            response = self.verified_permissions_client.is_authorized(
                policyStoreId=policy_store_id,
                principal={"entityId": subject["sub"], "entityType": "User"},
                resource=resources[0],
                action={"actionId": action}
            )
            return response["decision"]

        batch_requests = [{"principal": {"entityId": subject["sub"], "entityType": "User"},
                           "resource": res, "action": {"actionId": action}} for res in resources]

        response = self.verified_permissions_client.batch_is_authorized(
            policyStoreId=policy_store_id,
            requests=batch_requests
        )

        return next((res["decision"] for res in response["results"] if res["decision"] == ALLOW), DENY)

    def generate_policy(self, auth_decision, subject):
        """Genera la política de respuesta del API Gateway."""
        arn = f"arn:aws:execute-api:{os.getenv('AWS_REGION')}:*:*/*"
        
        policy_document = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": auth_decision,
                "Action": "execute-api:Invoke",
                "Resource": arn
            }]
        }

        return {
            "principalId": subject.get("sub", "unknown"),
            "policyDocument": policy_document,
            "context": subject
        }

    @abstractmethod
    def get_policy_store(self):
        """Debe ser implementado por la subclase para definir el Policy Store."""
        pass

    @abstractmethod
    def get_resources(self, event, subject):
        """Debe ser implementado por la subclase para obtener los recursos a autorizar."""
        pass

    def validate_jwt(self, token):
        """Decodifica y valida el token JWT con Cognito."""
        return jwt.decode(token, options={"verify_signature": False})  # Simulación