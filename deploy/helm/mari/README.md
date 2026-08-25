# Mari Helm chart

Mari v0.1.0 deploys one API pod, one web pod, PostgreSQL/pgvector, and persistent
volumes for PostgreSQL and `/data`. The API migration init container brings a
new database to the current schema before traffic starts. The images are public,
multi-architecture releases in GHCR; customers do not need an image
pull credential.

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

All v0.1.0 application files stay on the `mari-api-data` persistent volume:
vectors under `/data/mari/vectors`, Iceberg data under `/data/mari/iceberg`,
repository audit data under `/data/mari/repo-audit`, and caches under `/data/cache`.
No object store is required.

For production, use External Secrets or another secret controller to materialize
the same keys, then install:

```sh
helm upgrade --install mari deploy/helm/mari \
  --namespace mari --create-namespace \
  -f my-company-values.yaml \
  --wait --timeout 10m
```

For EKS, copy `values-aws-example.yaml` to a customer-owned values file and
replace its example hostname and certificate ARN. AWS ingress requires the AWS
Load Balancer Controller. Its optional `external-dns` annotation creates DNS only
when external-dns is installed and authorized for the customer's hosted zone.
