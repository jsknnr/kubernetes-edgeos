import requests
import json
import logging

from urllib3.exceptions import InsecureRequestWarning
from urllib3 import disable_warnings

# EdgeOS has a self-signed certificate and I am not installing a signed certificate on it
disable_warnings(InsecureRequestWarning)

# Setup Logging
logger = logging.getLogger('edgeos')
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
logger.addHandler(handler)

class EdgeOS:
    def __init__(self, address, user, password):
        self.address = address
        self.session = requests.Session() 
        self.session.post(f"https://{self.address}",
                          headers={'Content-Type': 'application/x-www-form-urlencoded'},
                          data=f"username={user}&password={password}",
                          verify=False
                          )

    def get_config(self):
        logger.info("Retrieving EdgeOS configuration")
        config = self.session.get(f"https://{self.address}/api/edge/get.json")
        return json.loads(config.text)
    
    def create_fw_rule(self, fw_name, rule_id, description, dest_address, dest_port, protocol):
        logger.info(f"Creating rule {rule_id} on firewall {fw_name} allowing {dest_port} to {dest_address} for {protocol}")
        payload = {"SET": {"firewall":{"name":{f"{fw_name}":{"rule":{f"{rule_id}":{"description":f"{description}","action":"accept","log":"disable","protocol":f"{protocol}","destination":{"address":f"{dest_address}","port":f"{dest_port}"}}}},"__FORCE_ASSOC":True}}},"GET":{"firewall":{"name":{f"{fw_name}":{"rule":{f"{rule_id}":{}}},"__FORCE_ASSOC":True}}}}
        headers = {'Content-type': 'application/json', 'X-CSRF-TOKEN': self.session.cookies.get('X-CSRF-TOKEN')}
        response = self.session.post(f"https://{self.address}/api/edge/batch.json",
                        headers=headers,
                        data=json.dumps(payload)
                    )
        return response.status_code
    
    def delete_fw_rule(self, fw_name, rule_id):
        logger.info(f"Removing rule {rule_id} from firewall {fw_name}")
        payload = {"DELETE":{"firewall":{"name":{f"{fw_name}":{"rule":{f"{rule_id}":"''"}},"__FORCE_ASSOC":True}}},"GET":{"firewall":None}}
        headers = {'Content-type': 'application/json', 'X-CSRF-TOKEN': self.session.cookies.get('X-CSRF-TOKEN')}
        response = self.session.post(f"https://{self.address}/api/edge/batch.json",
                        headers=headers,
                        data=json.dumps(payload)
                    )
        return response.status_code
    
    def create_dnat_rule(self, rule_id, description, inbound_interface, protocol, dest_address, dest_port, trans_address, trans_port):
        logger.info(f"Creating DNAT rule {rule_id} for {dest_address}:{dest_port} on inbound-interface {inbound_interface} to {trans_address}:{trans_port} using {protocol}")
        payload = {"SET":{"service":{"nat":{"rule":{f"{rule_id}":{"type":"destination","description":f"{description}","log":"disable","protocol":f"{protocol}","destination":{"address":f"{dest_address}","port":f"{dest_port}"},"inbound-interface":f"{inbound_interface}","inside-address":{"address":f"{trans_address}","port":f"{trans_port}"}}}}}},"GET":{"service":{"nat":{"rule":{f"{rule_id}":{}}}}}}
        headers = {'Content-type': 'application/json', 'X-CSRF-TOKEN': self.session.cookies.get('X-CSRF-TOKEN')}
        response = self.session.post(f"https://{self.address}/api/edge/batch.json",
                        headers=headers,
                        data=json.dumps(payload)
                    )
        return response.status_code
    
    def delete_dnat_rule(self, rule_id):
        logger.info(f"Removing rule {rule_id} from NAT")
        payload = {"DELETE":{"service":{"nat":{"rule":{f"{rule_id}":None}}}},"GET":{"service":{"nat": {}}}}
        headers = {'Content-type': 'application/json', 'X-CSRF-TOKEN': self.session.cookies.get('X-CSRF-TOKEN')}
        response = self.session.post(f"https://{self.address}/api/edge/batch.json",
                        headers=headers,
                        data=json.dumps(payload)
                    )
        return response.status_code
