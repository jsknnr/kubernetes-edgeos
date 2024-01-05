import logging

from kubernetes import client

# Setup Logging
logger = logging.getLogger('k8s')
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
logger.addHandler(handler)

class K8s:
    def __init__(self, token, endpoint_ip, endpoint_port):
        self.configuration = client.Configuration()
        self.configuration.host = f"https://{endpoint_ip}:{endpoint_port}"
        self.configuration.api_key["authorization"] = f"Bearer {token}"
        self.configuration.api_key_prefix["authorization"] = "Bearer"
        self.api_client = client.ApiClient(self.configuration)
        self.api = client.CoreV1Api(self.api_client)

    def get_services(self):
        logger.info("Retrieving list of services from kubernetes")
        services = self.api.list_service_for_all_namespaces()
        return services
