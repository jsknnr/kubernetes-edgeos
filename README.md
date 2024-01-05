# kubernetes-edgeos
Scrape Kubernetes services and automatically create DNAT and Firewall rules in Ubiquiti EdgeOS for type Load Balancer services

### Kubernetes
Make use of a service account with limited access to the API for this app to use, for example:

1) Create a service account for the user: `kubectl create serviceaccount edgeos`
2) Create a cluster role that defines the permissions for listing services: `kubectl create clusterrole list-services --verb=list --resource=services`
3) Create a cluster role binding that associates the service account with the cluster role: `kubectl create clusterrolebinding edgeos-list-services --serviceaccount=default:edgeos --clusterrole=list-services`
4) Get the token from the secret of the service account: `kubectl get secret $(kubectl get serviceaccount edgeos -o jsonpath='{.secrets[0].name}') -o jsonpath='{.data.token}' | base64 -d`

The token will get passed into the app along with the k8s API endpoint address and port
