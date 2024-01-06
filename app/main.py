import edgeos
import k8s

# Retrieve all services from Kubernetes cluster with type LoadBalancer
def locate_lb_services(services):
    for key, value in services.items():
        if isinstance(value, dict):
            yield from locate_lb_services(value)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from locate_lb_services(item)
        if key == "type" and value == "LoadBalancer":
            yield True

# Find the next rule number available in EdgeOS for dnat or firewall
def find_next_rule(config, type, firewall_name=None):
    next_rule = None
    max_rule = 1
    if type == "dnat":
        dnat_rules = config["GET"]["service"]["nat"]["rule"]
        for key, value in dnat_rules.items():
            if int(key) > max_rule:
                max_rule = int(key)
        next_rule = max_rule + 1
        return str(next_rule)
    elif type == "firewall":
        if not firewall_name:
            raise Exception("When type is firewall firewall_name arg must not be none")
        firewall_rules = config["GET"]["firewall"]["name"][f"{firewall_name}"]["rule"]
        for key, value in firewall_rules.items():
            if int(key) > max_rule:
                max_rule = int(key)
        next_rule = max_rule + 1
        return str(next_rule)
    else:
        raise Exception("type must be dnat or firewall")

# Build running state of current services in Kubernetes cluster to check against last state
def create_running_state(client):
    running_state = {}
    services = client.get_services()
    for service in services["items"]:
        for value in locate_lb_services(service):
            if value:
                # For some reason service name has prefix of 'app_name:', not sure where it is coming from but get rid of it
                name = service['metadata']['name'].split(':')[1]
                udp_ports = []
                tcp_ports = []
                running_state[f"{name}"] = {}
                running_state[f"{name}"]['lb_ip'] = service['status']['load_balancer']['ingress'][0]['ip']
                running_state[f"{name}"]['dnat_rules'] = {}
                for port in service['spec']['ports']:
                    iterator = 1
                    running_state[f"{name}"]['dnat_rules'][f"pending-{str(iterator)}"] = {}
                    running_state[f"{name}"]['dnat_rules'][f"pending-{str(iterator)}"]['port'] = service['spec']['ports'][port]['port']
                    running_state[f"{name}"]['dnat_rules'][f"pending-{str(iterator)}"]['protocol'] = service['spec']['ports'][port]['protocol'].lower()
                    if service['spec']['ports'][port]['protocol'].lower() == 'udp':
                        udp_ports.append(service['spec']['ports'][port]['port'])
                    elif service['spec']['ports'][port]['protocol'].lower() == 'tcp':
                        tcp_ports.append(service['spec']['ports'][port]['port'])
                    else:
                        raise Exception("Only tcp or udp ports are accepted")
                    iterator += 1
                running_state[f"{name}"]['fw_rules'] = {}
                if udp_ports:
                    running_state[f"{name}"]['fw_rules']['pending-1'] = {}
                    running_state[f"{name}"]['fw_rules']['pending-1']['protocol'] = 'udp'


for item in services["items"]:
    for value in locate_lb_services(item):
        if value:
            if 
            print("")
            print(f"app_name: {item['metadata']['name']}")
            print(f"ports: {item['spec']['ports']}")
            print(f"lb_ip: {item['status']['load_balancer']['ingress'][0]['ip']}")
