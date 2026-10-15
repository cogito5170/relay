# Port control-plane configuration

Terraform creates one `AI Job` blueprint and one `Run AI Relay` self-service action.
The action invokes `.github/workflows/cloud-relay.yml`; Port is the UI and run view,
while GitHub Actions remains the execution backend.

## Prerequisites

1. Create a Port organization.
2. Install Port's hosted GitHub Ocean integration for `cogito5170/effiency` with
   permission to dispatch Actions workflows.
3. Create Port API credentials for Terraform.
4. Read the connector label. For `Github-ocean - github-ops`, the Port integration
   identifier is `github-ops` (not the numeric GitHub App installation ID).
5. Export credentials only in the shell or CI secret store:

```bash
export PORT_CLIENT_ID='...'
export PORT_CLIENT_SECRET='...'
```

This repository defaults `port_github_installation_id` to `github-ops`. Override it
with `TF_VAR_port_github_installation_id` only if the connector is renamed.

Never put these values in `*.tf`, `*.tfvars`, workflow inputs, relay instructions,
logs, or artifacts.

Terraform state can contain sensitive values. This repository ignores local state,
plans, and `*.auto.tfvars`; use an encrypted remote backend before CI applies are
enabled. Commit `.terraform.lock.hcl` after the first successful `terraform init`.

## Apply

```bash
cd infra/port
terraform init
terraform fmt -check
terraform validate
terraform plan
terraform apply
```

This configuration targets the verified EU tenant (`app.port.io`) and therefore
defaults to `https://api.port.io`. For a US tenant (`app.us.port.io`), set
`TF_VAR_port_base_url=https://api.us.port.io`.

After apply, open Port's **Self-service** page and run **Run AI Relay**. The first
release intentionally invokes the current dry-run workflow; it proves the UI →
policy → remote runner path without model credentials.

## GitOps boundary

Changes to this directory use pull requests and reviewed Terraform plans. Transient
AI jobs are events and do not become YAML files in Git. When the project gains a
Kubernetes runtime, Argo CD will reconcile long-lived Deployments, configuration,
and policies from `gitops/`; it will not treat Git as a work queue.

