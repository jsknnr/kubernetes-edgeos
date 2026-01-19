import argparse
import copy
import logging

from app import edgeos, k8s, s3

logger = logging.getLogger(__name__)


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


def _next_rule_id(config, rule_type, firewall_name=None):
    max_rule = 0
    if rule_type == "dnat":
        dnat_rules = config["GET"]["service"]["nat"].get("rule", {})
        for key, value in dnat_rules.items():
            # Keep masquarade rules >= 5000; only consider destination NAT.
            if value.get("type") == "destination" and int(key) > max_rule:
                max_rule = int(key)
    elif rule_type == "firewall":
        if not firewall_name:
            raise ValueError("firewall_name is required for firewall rules")
        firewall_rules = config["GET"]["firewall"]["name"][firewall_name].get(
            "rule", {}
        )
        for key in firewall_rules.keys():
            if int(key) > max_rule:
                max_rule = int(key)
    else:
        raise ValueError("rule_type must be dnat or firewall")
    return max_rule + 1


def _port_in_spec(port, spec):
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            if int(start) <= port <= int(end):
                return True
        elif int(part) == port:
            return True
    return False


# Build running state of current services in Kubernetes cluster to check against last state
def create_running_state(k8s_client, dest_ip, fw_names=None, inbound_interfaces=None):
    fw_names = fw_names or []
    inbound_interfaces = inbound_interfaces or []
    running_state = {}
    services = k8s_client.get_services()
    for service in services["items"]:
        for value in _locate_lb_services(service):
            if value:
                name = (
                    f"{service['metadata']['namespace']}/{service['metadata']['name']}"
                )
                running_state[name] = {}
                running_state[name]["lb_ip"] = service["status"]["load_balancer"][
                    "ingress"
                ][0]["ip"]
                running_state[name]["dest_ip"] = dest_ip
                running_state[name]["dnat_rules"] = {}
                udp_ports = []
                tcp_ports = []
                for index, port in enumerate(service["spec"]["ports"]):
                    for interface in inbound_interfaces:
                        running_state[name]["dnat_rules"][
                            f"pending-{index}-{interface}"
                        ] = {
                            "port": str(port["port"]),
                            "protocol": port["protocol"].lower(),
                            "inbound_interface": interface,
                        }
                    protocol = port["protocol"].lower()
                    if protocol == "udp":
                        udp_ports.append(port["port"])
                    elif protocol == "tcp":
                        tcp_ports.append(port["port"])
                    else:
                        raise Exception("Only tcp or udp ports are accepted")
                running_state[name]["fw_rules"] = {}
                for fw_name in fw_names:
                    if udp_ports:
                        running_state[name]["fw_rules"][
                            f"pending-udp-{fw_name}"
                        ] = {
                            "protocol": "udp",
                            "ports": ",".join(str(n) for n in udp_ports),
                            "fw_name": fw_name,
                        }
                    if tcp_ports:
                        running_state[name]["fw_rules"][
                            f"pending-tcp-{fw_name}"
                        ] = {
                            "protocol": "tcp",
                            "ports": ",".join(str(n) for n in tcp_ports),
                            "fw_name": fw_name,
                        }
    return running_state


def check_dnat_port_in_use(service, dnat_rules):
    for rule, config in service["dnat_rules"].items():
        port = int(config["port"])
        protocol = config["protocol"].lower()
        for dnat_rule, dnat_config in dnat_rules.items():
            if dnat_config["type"] == "destination":
                if (
                    protocol == dnat_config["protocol"].lower()
                    and _port_in_spec(port, dnat_config["destination"]["port"])
                ):
                    logger.info(
                        f"Destination NAT port {port} {protocol} already in use on EdgeOS NAT rule {dnat_rule} with description: {dnat_config['description']}"
                    )
                    yield True


def check_for_cleanup(running_state, persisted_state):
    services_to_remove = []
    for service in persisted_state.keys():
        if service not in running_state.keys():
            logger.info(
                f"Persisted service {service} not found in running state, marking for removal"
            )
            services_to_remove.append(service)
    return services_to_remove


def create_rules(running_state, edgeos_client, edgeos_config):
    configured_services = copy.deepcopy(running_state)
    next_dnat_rule = _next_rule_id(edgeos_config, "dnat")
    fw_names = {
        fw_config["fw_name"]
        for service in running_state.values()
        for fw_config in service["fw_rules"].values()
    }
    next_fw_rule = {
        fw_name: _next_rule_id(edgeos_config, "firewall", firewall_name=fw_name)
        for fw_name in fw_names
    }
    for service, config in running_state.items():
        for dnat_rule, dnat_config in config["dnat_rules"].items():
            if "pending" in dnat_rule:
                next_rule = str(next_dnat_rule)
                next_dnat_rule += 1
                configured_services[service]["dnat_rules"][next_rule] = (
                    configured_services[service]["dnat_rules"].pop(dnat_rule)
                )
                edgeos_client.create_dnat_rule(
                    next_rule,
                    f"Automated rule for {service} service in kubernetes",
                    dnat_config["inbound_interface"],
                    dnat_config["protocol"],
                    config["dest_ip"],
                    dnat_config["port"],
                    config["lb_ip"],
                    dnat_config["port"],
                )
        for fw_rule, fw_config in config["fw_rules"].items():
            if "pending" in fw_rule:
                next_rule = str(next_fw_rule[fw_config["fw_name"]])
                next_fw_rule[fw_config["fw_name"]] += 1
                configured_services[service]["fw_rules"][next_rule] = (
                    configured_services[service]["fw_rules"].pop(fw_rule)
                )
                edgeos_client.create_fw_rule(
                    fw_config["fw_name"],
                    next_rule,
                    f"Automated rule for {service} service in kubernetes",
                    config["lb_ip"],
                    fw_config["ports"],
                    fw_config["protocol"],
                )
    return configured_services


def delete_rules(persisted_state, services_to_remove, edgeos_client):
    state = persisted_state
    for service, config in persisted_state.items():
        if service in services_to_remove:
            for dnat_rule, dnat_config in config["dnat_rules"].items():
                edgeos_client.delete_dnat_rule(dnat_rule)
            for fw_rule, fw_config in config["fw_rules"].items():
                edgeos_client.delete_fw_rule(fw_config["fw_name"], fw_rule)
    for service in services_to_remove:
        state.pop(service)
    return state


def reconcile_state(running_state, persisted_state):
    reconciled_state = running_state | persisted_state
    return reconciled_state


def configure_logging(level):
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )


def main(args):
    s3_client = s3.S3(args.s3_access_key, args.s3_secret_access_key, args.s3_bucket)
    persisted_state = s3_client.get_object("edgeos_state.json")
    edgeos_client = edgeos.EdgeOS(
        args.edge_address,
        args.edge_user,
        args.edge_password,
        verify_ssl=args.edge_verify_ssl,
        timeout=args.edge_timeout,
    )
    edgeos_config = edgeos_client.get_config()
    k8s_client = k8s.K8s(
        args.k8s_sa_token,
        args.k8s_api_address,
        args.k8s_api_port,
        verify_ssl=args.k8s_verify_ssl,
    )
    running_state = create_running_state(
        k8s_client,
        args.dest_ip,
        args.edge_fw_names,
        args.edge_inbound_interface,
    )

    services_to_cleanup = check_for_cleanup(running_state, persisted_state)

    if args.excluded_services:
        for service in args.excluded_services:
            if service in running_state.keys():
                logger.info(
                    f"Service {service} is present in the exclusion list, removing from running state"
                )
                running_state.pop(service)

    conflicted_services = []
    for service, data in running_state.items():
        for value in check_dnat_port_in_use(
            data, edgeos_config["GET"]["service"]["nat"]["rule"]
        ):
            if value:
                if service not in conflicted_services:
                    conflicted_services.append(service)
    for service in conflicted_services:
        running_state.pop(service)
    logger.info(
        f"The following services will not be configured due to conflicting dnat ports: {conflicted_services}"
    )

    if not args.dry_run:
        p_state = delete_rules(persisted_state, services_to_cleanup, edgeos_client)
        r_state = create_rules(running_state, edgeos_client, edgeos_config)
        final_state = reconcile_state(r_state, p_state)
        logger.info("Created services: %s", ", ".join(r_state.keys()))
        s3_client.put_object("edgeos_state.json", final_state)
    else:
        logger.info("Dry Run state:")
        logger.info(running_state)


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--edge_user", help="EdgeOS Username for auth", required=True)
    parser.add_argument(
        "--edge_password", help="EdgeOS user password for auth", required=True
    )
    parser.add_argument(
        "--edge_address", help="IP Address to reach EdgeOS API", required=True
    )
    parser.add_argument(
        "--edge_verify_ssl",
        help="Verify EdgeOS SSL certificate",
        action="store_true",
    )
    parser.add_argument(
        "--edge_timeout",
        help="EdgeOS API timeout in seconds",
        type=float,
        default=10,
    )
    parser.add_argument(
        "--k8s_sa_token",
        help="Service Account auth token for Kubernetes API",
        required=True,
    )
    parser.add_argument(
        "--k8s_api_address", help="Kubernetes API address", required=True
    )
    parser.add_argument(
        "--k8s_api_port", help="Kubernetes API port", type=int, required=True
    )
    parser.add_argument(
        "--k8s_verify_ssl",
        help="Verify Kubernetes SSL certificate",
        action="store_true",
    )
    parser.add_argument(
        "--s3_access_key", help="Service account access key for AWS S3", required=True
    )
    parser.add_argument(
        "--s3_secret_access_key",
        help="Service account secret access key for AWS S3",
        required=True,
    )
    parser.add_argument(
        "--s3_bucket", help="S3 bucket to store EdgeOS state", required=True
    )
    parser.add_argument(
        "--dest_ip", help="Destination IP used for DNAT rules", required=True
    )
    parser.add_argument(
        "--edge_inbound_interface",
        nargs="+",
        help="Which inbound interfaces on EdgeOS to create DNAT rules for. Accepts multiple interfaces (e.g. --edge_inbound_interface eth0 eth1)",
        required=True,
    )
    parser.add_argument(
        "--edge_fw_names",
        nargs="+",
        help="Which EdgeOS firewall names to create firewall rules for. Accepts multiple firewall names (e.g. --edge_fw_names LAN_IN WAN_IN)",
        required=True,
    )
    parser.add_argument(
        "--excluded_services",
        nargs="+",
        help="List of services to exclude from auto provisioning. List as namespace_name/service_name. Accepts multiple services (e.g. --excluded_services default/nginx namespace2/service2)",
    )
    parser.add_argument(
        "--dry_run",
        help="Read only run of app. No changes will be made",
        action="store_true",
    )
    parser.add_argument(
        "--log_level",
        help="Log level (e.g. INFO, DEBUG)",
        default="INFO",
    )

    args = parser.parse_args()
    configure_logging(args.log_level.upper())

    main(args)


if __name__ == "__main__":
    cli()
