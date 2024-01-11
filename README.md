# kubernetes-edgeos
Scrape Kubernetes services and automatically create DNAT and Firewall rules in Ubiquiti EdgeOS for type Load Balancer services.

I have no idea if this will ever be useful for anyone other than me, but here it is. I run a Kubernetes cluster in my home lab. My core router is a Ubiquity EdgeRouter. I got tired of manually creating firewall rules and destination nat rules on my router every time I spun up a service that I wanted to expose externally. So I wrote this little Python app that can scrape the current list of services in a Kubernetes cluster, check that they are type LoadBalancer (the type I am interested in), and build DNAT and firewall rules on an EdgeRouter based on the data from the Kubernetes service. Once it does the apply, it saves it's running state that it uses to create the rules as a json file to persist to S3. I persist it's last state so I can compare the current running state and see if any services need to be removed from the router if they are no longer present on the cluster.

## Usage
To use the app, make sure the Python dependencies are installed and simply call `main.py` with the following arguments (there are a lot)

```
usage: main.py [-h] [--edge_user EDGE_USER] [--edge_password EDGE_PASSWORD] [--edge_address EDGE_ADDRESS] [--k8s_sa_token K8S_SA_TOKEN] [--k8s_api_address K8S_API_ADDRESS] [--k8s_api_port K8S_API_PORT] [--s3_access_key S3_ACCESS_KEY]
               [--s3_secret_access_key S3_SECRET_ACCESS_KEY] [--s3_bucket S3_BUCKET] [--dest_ip DEST_IP] [--edge_inbound_interface EDGE_INBOUND_INTERFACE [EDGE_INBOUND_INTERFACE ...]]
               [--edge_fw_names EDGE_FW_NAMES [EDGE_FW_NAMES ...]] [--excluded_services EXCLUDED_SERVICES [EXCLUDED_SERVICES ...]] [--dry_run]

options:
  -h, --help            show this help message and exit
  --edge_user EDGE_USER
                        EdgeOS Username for auth
  --edge_password EDGE_PASSWORD
                        EdgeOS user password for auth
  --edge_address EDGE_ADDRESS
                        IP Address to reach EdgeOS API
  --k8s_sa_token K8S_SA_TOKEN
                        Service Account auth token for Kubernetes API
  --k8s_api_address K8S_API_ADDRESS
                        Kubernetes API address
  --k8s_api_port K8S_API_PORT
                        Kubernetes API port
  --s3_access_key S3_ACCESS_KEY
                        Service account access key for AWS S3
  --s3_secret_access_key S3_SECRET_ACCESS_KEY
                        Service account secret access key for AWS S3
  --s3_bucket S3_BUCKET
                        S3 bucket to store EdgeOS state
  --dest_ip DEST_IP     Destination IP used for DNAT rules
  --edge_inbound_interface EDGE_INBOUND_INTERFACE [EDGE_INBOUND_INTERFACE ...]
                        Which inbound interfaces on EdgeOS to create DNAT rules for
  --edge_fw_names EDGE_FW_NAMES [EDGE_FW_NAMES ...]
                        Which EdgeOS firewall names to create firewall rules for
  --excluded_services EXCLUDED_SERVICES [EXCLUDED_SERVICES ...]
                        List of services to exclude from auto provisioning
  --dry_run             Read only run of app. No changes will be made
```

Most of the arugments are self explanatory. My home network is segmented into multiple networks and I use zone based firewall policies. Due to this, the arguments `edge_inbound_interface` and `edge_fw_names` can take multiple values if you want to have DNAT rules created for multiple inbound interfaces or firewall rules created for more than 1 firewall in EdgeOS. The `dry_run` argument will run the app but not actually make any changes, it will just list the ones it would have made.

### S3
Although this app is capable of storing it's last run state locally, that isn't ideal. I recommend you create a service account and create an access token and secret that gives the service account acess to ONLY read and write to the bucket where you wish to store the state.

### Kubernetes
Make use of a service account with limited access to the API for this app to use, for example:

1) Create a service account for the user: `kubectl create serviceaccount edgeos`
2) Create a cluster role that defines the permissions for listing services: `kubectl create clusterrole list-services --verb=list --resource=services`
3) Create a cluster role binding that associates the service account with the cluster role: `kubectl create clusterrolebinding edgeos-list-services --serviceaccount=default:edgeos --clusterrole=list-services`
4) Create a token for the service account: 
```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: edgeos-sa-token
  annotations:
    kubernetes.io/service-account.name: edgeos
type: kubernetes.io/service-account-token
EOF
```
5) Get the token from the secret of the service account: `kubectl get secret $(kubectl get serviceaccount edgeos -o jsonpath='{.secrets[0].name}') -o jsonpath='{.items[0].data.token}' | base64 -d`

The token will get passed into the app along with the k8s API endpoint address and port

## To Do

* Handle updates to rules if K8s services values change (currently you'd want to remove and then re-add)
* Handle partial implementation of services, similar to the above
* Better checking and reporting of rule conflicts (currently if there is any conflict at all for a given service I just abort implementing the service and report as such in log)
* Build and publish quick container image for running this app as a container (probably the next thing I do)
