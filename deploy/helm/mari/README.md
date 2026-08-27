# Mari Helm chart

Mari v0.1.3 deploys one API pod, one web pod, PostgreSQL/pgvector, and persistent
volumes for PostgreSQL and `/data`. The API migration init container brings a
new database to the current schema before traffic starts. The images are public,
multi-architecture releases in Amazon ECR Public; customers do not need an AWS
account or image pull credential.

Install one Mari release per dedicated namespace. The stable internal Service
names (`api`, `web`, and `postgres`) are part of the application contract.

## Required secret

Do not put production credentials in a values file. Create `mari-secrets` in
the release namespace before installing:

```sh
kubectl create namespace mari
kubectl -n mari create secret generic mari-secrets \
  --from-literal=POSTGRES_PASSWORD='REPLACE_WITH_A_LONG_URL_SAFE_PASSWORD' \
  --from-literal=MARI_DB='postgresql://mari:REPLACE_WITH_THE_SAME_PASSWORD@postgres:5432/mari_cloud'
```

Those are the only required secrets, and both values must contain the same
URL-safe password. No cloud or object-storage credentials are required.

Provider and source credentials are configured in Mari after installation; do
not add them to the Kubernetes Secret.

All v0.1.3 application files stay on the `mari-api-data` persistent volume:
vectors under `/data/mari/vectors`, Iceberg data under `/data/mari/iceberg`,
repository audit data under `/data/mari/repo-audit`, and caches under `/data/cache`.
No object store is required.

The API PVC is retained if the Helm release is uninstalled, and Kubernetes also
retains the StatefulSet's PostgreSQL PVC. Back up both volumes. Removing the
namespace or deleting either PVC destroys customer data.

For production, use External Secrets or another secret controller to materialize
the same keys. Install against the customer's existing ingress controller; no
hostname is required:

```sh
helm upgrade --install mari deploy/helm/mari \
  --namespace mari --create-namespace \
  --set secrets.existingSecret=mari-secrets \
  --set ingress.enabled=true \
  --set ingress.className=REPLACE_WITH_THE_CUSTOMER_INGRESS_CLASS \
  --wait --timeout 10m
```

The Ingress accepts any Host header and is reachable at the address assigned by
the customer's ingress controller. To use DNS, set `ingress.host` to a
customer-owned hostname and configure TLS using that controller's normal values.

## First start

Find the address assigned by the customer's ingress controller:

```sh
kubectl -n mari get ingress mari
```

Open that address in a browser. A fresh database redirects to **Welcome to
Mari**, where the customer creates the first workspace owner. After setup, open
**Sources**, choose **Add source**, and enter the selected connector's credentials
in the web UI. Connector and model credentials are application data and do not
belong in the Kubernetes Secret.

For EKS, copy `values-aws-example.yaml` to a customer-owned values file and
replace its example hostname and certificate ARN. AWS ingress requires the AWS
Load Balancer Controller. Its optional `external-dns` annotation creates DNS only
when external-dns is installed and authorized for the customer's hosted zone.
