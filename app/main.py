import edgeos
import k8s
import s3
import argparse
import json
import copy
import logging

# Setup Logging
logger = logging.getLogger('main')
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
logger.addHandler(handler)

# Retrieve all services from Kubernetes cluster with type LoadBalancer
def _locate_lb_services(services):
    for key, value in services.items():
        if isinstance(value, dict):
            yield from _locate_lb_services(value)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from _locate_lb_services(item)
        if key == "type" and value == "LoadBalancer":
            yield True

# Find the next rule number available in EdgeOS for dnat or firewall
def _find_next_rule(config, type, firewall_name=None):
    next_rule = None
    max_rule = 1
    if type == "dnat":
        dnat_rules = config["GET"]["service"]["nat"]["rule"]
        for key, value in dnat_rules.items():
            # I keep my masquarade rules >= 5000 which dnat rules cannot be in this range
            if int(key) > max_rule and value['type'] == 'destination':
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
def create_running_state(k8s_client, edgeos_config, dest_ip, fw_names=[], inbound_interfaces=[]):
    running_state = {}
    services = k8s_client.get_services()
    for service in services["items"]:
        for value in _locate_lb_services(service):
            if value:
                name = f"{service['metadata']['namespace']}/{service['metadata']['name']}"
                running_state[f"{name}"] = {}
                running_state[f"{name}"]['lb_ip'] = service['status']['load_balancer']['ingress'][0]['ip']
                running_state[f"{name}"]['dest_ip'] = dest_ip
                running_state[f"{name}"]['dnat_rules'] = {}
                iterator = 0
                udp_ports = []
                tcp_ports = []
                for port in service['spec']['ports']:
                    for interface in inbound_interfaces:
                        running_state[f"{name}"]['dnat_rules'][f"pending-{iterator}-{interface}"] = {}
                        running_state[f"{name}"]['dnat_rules'][f"pending-{iterator}-{interface}"]['port'] = str(service['spec']['ports'][iterator]['port'])
                        running_state[f"{name}"]['dnat_rules'][f"pending-{iterator}-{interface}"]['protocol'] = service['spec']['ports'][iterator]['protocol'].lower()
                        running_state[f"{name}"]['dnat_rules'][f"pending-{iterator}-{interface}"]['inbound_interface'] = interface
                    if service['spec']['ports'][iterator]['protocol'].lower() == 'udp':
                        udp_ports.append(service['spec']['ports'][iterator]['port'])
                    elif service['spec']['ports'][iterator]['protocol'].lower() == 'tcp':
                        tcp_ports.append(service['spec']['ports'][iterator]['port'])
                    else:
                        raise Exception("Only tcp or udp ports are accepted")
                    iterator += 1
                running_state[f"{name}"]['fw_rules'] = {}
                for fw_name in fw_names:
                    if udp_ports:
                        running_state[f"{name}"]['fw_rules']["pending-udp"] = {}
                        running_state[f"{name}"]['fw_rules']["pending-udp"]['protocol'] = 'udp'
                        running_state[f"{name}"]['fw_rules']["pending-udp"]['ports'] = ','.join(str(n) for n in udp_ports)
                        running_state[f"{name}"]['fw_rules']["pending-udp"]['fw_name'] = fw_name
                    if tcp_ports:
                        running_state[f"{name}"]['fw_rules']["pending-tcp"] = {}
                        running_state[f"{name}"]['fw_rules']["pending-tcp"]['protocol'] = 'tcp'
                        running_state[f"{name}"]['fw_rules']["pending-tcp"]['ports'] = ','.join(str(n) for n in tcp_ports)
                        running_state[f"{name}"]['fw_rules']["pending-tcp"]['fw_name'] = fw_name
    return running_state

def retrieve_state(path):
    try:
        with open(f"{path}/edgeos_state.json", 'r') as state_file:
            state = json.load(state_file)
    except FileNotFoundError:
        logger.warning("Persisted state not found, creating a new state")
        state = json.dumps({})
    return state

def save_state(path, state):
    try:
        with open(f"{path}/edgeos_state.json", 'w') as state_file:
            state_file.write(json.dumps(state, indent=2))
    except Exception as error:
        logger.error("Unable to write new state file. Dumping state contents to log.")
        logger.error(json.dumps(state))
        raise error
    
def check_dnat_port_in_use(service, dnat_rules):
    for rule, config in service['dnat_rules'].items():
        port = config['port']
        protocol = config['protocol']
        for dnat_rule, dnat_config in dnat_rules.items():
            if dnat_config['type'] == "destination":
                if protocol == dnat_config['protocol'] and port in dnat_config['destination']['port']:
                    logger.info(f"Destination NAT port {port} {protocol} already in use on EdgeOS NAT rule {dnat_rule} with description: {dnat_config['description']}")
                    yield True
                    
def check_for_cleanup(running_state, persisted_state):
    services_to_remove = []
    for service in persisted_state.keys():
        if service not in running_state.keys():
           logger.info(f"Persisted service {service} not found in running state, marking for removal")
           services_to_remove.append(service)
    return services_to_remove

def create_rules(running_state, edgeos_client):
    configured_services = copy.deepcopy(running_state)
    for service, config in running_state.items():
        for dnat_rule, dnat_config in config['dnat_rules'].items():
            if 'pending' in dnat_rule:
                edgeos_config = edgeos_client.get_config()
                next_rule = _find_next_rule(edgeos_config, 'dnat')
                configured_services[service]['dnat_rules'][next_rule] = configured_services[service]['dnat_rules'].pop(dnat_rule)
                edgeos_client.create_dnat_rule(
                    next_rule,
                    f"Automated rule for {service} service in kubernetes",
                    dnat_config['inbound_interface'],
                    dnat_config['protocol'],
                    config['dest_ip'],
                    dnat_config['port'],
                    config['lb_ip'],
                    dnat_config['port']
                )
        for fw_rule, fw_config in config['fw_rules'].items():
            if 'pending' in fw_rule:
                edgeos_config = edgeos_client.get_config()
                next_rule = _find_next_rule(edgeos_config, 'firewall', firewall_name=fw_config['fw_name'])
                configured_services[service]['fw_rules'][next_rule] = configured_services[service]['fw_rules'].pop(fw_rule)
                edgeos_client.create_fw_rule(
                    fw_config['fw_name'],
                    next_rule,
                    f"Automated rule for {service} service in kubernetes",
                    config['lb_ip'],
                    fw_config['ports'],
                    fw_config['protocol']
                )
    return configured_services

def delete_rules(persisted_state, services_to_remove, edgeos_client):
    state = persisted_state
    for service, config in persisted_state.items():
        if service in services_to_remove:
            for dnat_rule, dnat_config in config['dnat_rules'].items():
                edgeos_client.delete_dnat_rule(dnat_rule)
            for fw_rule, fw_config in config['fw_rules'].items():
                edgeos_client.delete_fw_rule(fw_config['fw_name'], fw_rule)
    for service in services_to_remove:
        state.pop(service)
    return state

def reconcile_state(running_state, persisted_state):
    reconciled_state = running_state | persisted_state
    return reconciled_state

def main(args):
    s3_client = s3.S3(args.s3_access_key, args.s3_secret_access_key, args.s3_bucket)
    persisted_state = s3_client.get_object('edgeos_state.json')
    edgeos_client = edgeos.EdgeOS(args.edge_address, args.edge_user, args.edge_password)
    edgeos_config = edgeos_client.get_config()
    k8s_client = k8s.K8s(args.k8s_sa_token, args.k8s_api_address, args.k8s_api_port)
    running_state = create_running_state(k8s_client, edgeos_config, args.dest_ip, args.edge_fw_names, args.edge_inbound_interface)

    services_to_cleanup = check_for_cleanup(running_state, persisted_state)

    if args.excluded_services:
        for service in args.excluded_services:
            if service in running_state.keys():
                logger.info(f"Service {service} is present in the exclusion list, removing from running state")
                running_state.pop(service)

    conflicted_services = []
    for service, data in running_state.items():
        for value in check_dnat_port_in_use(data, edgeos_config['GET']['service']['nat']['rule']):
            if value:
                if service not in conflicted_services:
                    conflicted_services.append(service)
    for service in conflicted_services:
        running_state.pop(service)
    logger.info(f"The following services will not be configured due to conflicting dnat ports: {conflicted_services}")

    if not args.dry_run:
        p_state = delete_rules(persisted_state, services_to_cleanup, edgeos_client)
        r_state = create_rules(running_state, edgeos_client)
        final_state = reconcile_state(r_state, p_state)
        logger.info("Created services:")
        s3_client.put_object('edgeos_state.json', final_state)
    else:
        logger.info("Dry Run state:")
        logger.info(running_state)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--edge_user', help="EdgeOS Username for auth")
    parser.add_argument('--edge_password', help="EdgeOS user password for auth")
    parser.add_argument('--edge_address', help="IP Address to reach EdgeOS API")
    parser.add_argument('--k8s_sa_token', help="Service Account auth token for Kubernetes API")
    parser.add_argument('--k8s_api_address', help="Kubernetes API address")
    parser.add_argument('--k8s_api_port', help="Kubernetes API port")
    parser.add_argument('--s3_access_key', help="Service account access key for AWS S3")
    parser.add_argument('--s3_secret_access_key', help="Service account secret access key for AWS S3")
    parser.add_argument('--s3_bucket', help="S3 bucket to store EdgeOS state")
    parser.add_argument('--dest_ip', help="Destination IP used for DNAT rules")
    parser.add_argument('--edge_inbound_interface', nargs='+', help="Which inbound interfaces on EdgeOS to create DNAT rules for")
    parser.add_argument('--edge_fw_names', nargs='+', help="Which EdgeOS firewall names to create firewall rules for")
    parser.add_argument('--excluded_services', nargs='+', help="List of services to exclude from auto provisioning")
    parser.add_argument('--dry_run', help="Read only run of app. No changes will be made", action="store_true")

    args = parser.parse_args()

    main(args)
