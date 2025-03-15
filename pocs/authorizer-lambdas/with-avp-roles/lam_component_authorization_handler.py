import logging
from abstract_handler import AbstractHandler

# Configuración de logs
logger = logging.getLogger()
logger.setLevel(logging.INFO)

class ComponentAuthorizationHandler(AbstractHandler):
    """Lambda Authorizer personalizado basado en AbstractHandler."""

    def get_policy_store(self):
        """Retorna el ID del Policy Store de Verified Permissions."""
        return "lexiverse-policy-store-id"

    def get_resources(self, event, subject):
        """Obtiene la lista de recursos para autorización."""
        resource = self.get_resource(event)
        return [resource] if resource else []

    def get_resource(self, event):
        """Obtiene el recurso de la solicitud."""
        logger.info(f"Got event in getResource: {event}")

        path_params = event.get("pathParameters", {})
        org_id = path_params.get("orgId")

        if not org_id:
            return None

        logger.info(f"orgId to send: {org_id}")
        return {"entityId": org_id, "entityType": "ORGANIZATION_CONTAINER"}

# Handler para AWS Lambda
def lambda_handler(event, context):
    handler = ComponentAuthorizationHandler()
    return handler.handle_request(event, context)