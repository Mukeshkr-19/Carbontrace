# Carbontrace — AWS GreenOps Profiler

Carbontrace is a small, infrastructure-as-code pipeline that runs a deliberately inefficient Python workload on EC2, measures its CPU and memory behavior, models its energy and CO2e impact, and publishes the results to a Terraform-managed CloudWatch dashboard.

It is a research-adjacent instrumentation proof of concept, not a hardware power meter or a general-purpose carbon-accounting product. Its purpose is to make workload efficiency observable and provide a foundation for comparing a future LLM inference workload against a controlled baseline.

## What this demonstrates

- Terraform-managed AWS infrastructure, dashboard, IAM, and shutdown guardrails
- least-privilege workload identity and IMDSv2-only instance metadata access
- process-level CPU and memory sampling with custom CloudWatch metrics
- modeled energy and CO2e reporting through CodeCarbon
- automated, bounded recurring workload runs with `systemd`
- explicit teardown and cost-control discipline

## Architecture

```text
Terraform
   |
   +--> Default VPC + SSH-only security group
   +--> EC2 (Ubuntu 22.04, encrypted EBS, IMDSv2 required)
   |       |
   |       +--> systemd timer --> Python workload + reporter
   |       |                         |
   |       |                         +--> CodeCarbon estimate
   |       |                         +--> CloudWatch PutMetricData
   |       |
   |       +--> CloudWatch agent --> host metrics + application log group
   |
   +--> CloudWatch dashboard (GreenOps/App)
   +--> EventBridge rule --> Lambda --> stop this EC2 instance
```

## Metrics and methodology

The reporter samples the current Python process at one-second intervals and publishes aggregate values in the `GreenOps/App` namespace:

| Metric | Meaning |
|---|---|
| `CPUUtilizationCustom` | Average sampled process CPU usage (%) |
| `MemoryUtilizationPercent` | Average sampled process memory usage (%) |
| `EstimatedEnergyWh` | CodeCarbon modeled energy consumption, converted from kWh to Wh |
| `EstimatedWatts` | `EstimatedEnergyWh / elapsed hours`; an average modeled power value |
| `EstimatedCO2Grams` | CodeCarbon modeled CO2e, converted from kg to grams |

The fixed metric dimensions are `Project`, `InstanceType`, and `WorkloadVersion`. A unique run ID is emitted in the structured application log but deliberately not as a metric dimension, which keeps CloudWatch metric cardinality and cost under control. CloudWatch custom metrics are organized by namespace, metric name, and dimensions; each published metric uses the AWS `PutMetricData` API. [AWS CloudWatch custom-metrics documentation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/publishingMetrics.html)

### Estimation limits

Energy, power, and CO2e values are **modeled estimates**, not readings from physical EC2 power telemetry. CodeCarbon models energy and emissions from measured/estimated compute activity and carbon-intensity information; its documented relationship is energy consumption multiplied by carbon intensity. [CodeCarbon methodology](https://docs.codecarbon.io/3.2/explanation/methodology/)

Treat measurements as useful for comparing controlled workload versions, not as an exact statement of the physical electricity consumed by one EC2 instance. Results may vary with host hardware, underlying cloud infrastructure, region, instance behavior, workload duration, and the estimator’s assumptions.

## Prerequisites

- Terraform 1.6 or later
- AWS CLI credentials for the target AWS account and `us-east-1`
- an existing EC2 key pair in that Region
- a pre-created S3 state bucket
- a public GitHub repository, because the EC2 bootstrap clones a pinned HTTPS revision

### Bootstrap the state backend

The `bootstrap/` directory is a deliberately separate Terraform root module. Run it once before initializing the main stack; it creates a versioned, encrypted, publicly blocked S3 bucket with native S3 lockfile support for Terraform state.

```bash
cd bootstrap
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars and choose a globally unique state_bucket_name.
terraform init
terraform plan -out=bootstrap.tfplan
terraform apply bootstrap.tfplan
cd ..
```

Use the bootstrap output to fill your private `backend.hcl` file. The bootstrap state remains local in `bootstrap/terraform.tfstate`; keep it private, do not commit it, and do not delete it. The S3 bucket has `prevent_destroy = true`, so normal infrastructure teardown intentionally retains the backend rather than treating it as a leaked resource.

`bootstrap/deployment-user-policy.json` documents the temporary, bucket-scoped permissions used to create and verify the backend. Terraform does not attach this policy. After backend verification, replace the temporary bucket-configuration write permissions with an ongoing backend-only policy before provisioning the main stack.

## Deploy

1. Create a private local variable file. It is ignored by Git.

   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```

2. Fill in `my_ip` with your current public address followed by `/32`, your existing `key_name`, and a full reviewed commit SHA for `app_revision`.

   ```bash
   git rev-parse HEAD
   ```

3. Create a private `backend.hcl` from `backend.hcl.example`, using the S3 bucket created by `bootstrap/`. Keep `use_lockfile = true`.

4. Initialize, review, and apply. Never bypass the plan review.

   ```bash
   terraform init -backend-config=backend.hcl
   terraform fmt -check -recursive
   terraform validate
   terraform plan -out=main.tfplan
   terraform apply main.tfplan
   ```

5. Confirm the timer and reporter after the instance boots:

   ```bash
   sudo systemctl status greenops-reporter.timer
   sudo journalctl -u greenops-reporter.service
   ```

The first scheduled run occurs after the five-minute boot delay plus a small randomized delay. The CloudWatch dashboard is created during `terraform apply`; add a screenshot here after at least three real scheduled runs.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.metrics_reporter --duration-seconds 30 --work-size 2000
.venv/bin/python -m unittest discover -s tests -v
```

Do not pass `--publish` locally unless your environment has appropriately scoped AWS credentials and you intend to publish metrics.

## Security controls

- SSH ingress is restricted to the one `/32` address supplied in `terraform.tfvars`; there is no `0.0.0.0/0` inbound rule.
- The EC2 root volume is encrypted, uses `gp3`, and is deleted when the instance terminates.
- The instance requires IMDSv2 and disables instance metadata tags. The bootstrap script acquires an IMDSv2 token before reading instance data. [AWS IMDSv2 documentation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html)
- The application runs as the unprivileged `greenops` system user with a hardened systemd service.
- The instance role can publish only the approved CloudWatch namespaces and write only to the project log group.
- The auto-stop Lambda can stop only the profiler instance, and its execution log group has a 14-day retention policy.
- Terraform state, `.tfvars`, plans, and local virtual environments are excluded from version control. Do not put credentials, private keys, or backend values in tracked files.

## Cost controls and teardown

- Development defaults to `t3.micro`.
- The EventBridge/Lambda circuit breaker is enabled by default and periodically stops the instance. It is a backstop, not a substitute for cleanup.
- A stopped EC2 instance can still incur EBS charges.

When finished, remove the complete stack:

```bash
terraform destroy
```

This destroys the main Carbontrace infrastructure only. It does not destroy the separately bootstrapped state bucket, which is protected by `prevent_destroy = true` and must be retained while any Terraform state depends on it.

Then verify that the instance is gone:

```bash
aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=carbontrace" \
  --query 'Reservations[].Instances[].{Id:InstanceId,State:State.Name}' \
  --output table
```

## Future direction

The intentionally inefficient loop is a controlled baseline. The same reporter, metric schema, dashboard, and teardown controls can later wrap a real model-inference workload, enabling a focused comparison of model efficiency under a consistent measurement harness.
