# Carbontrace — AWS Carbon-Footprint Profiler

Carbontrace is a small, infrastructure-as-code pipeline that runs a deliberately inefficient Python workload on EC2, measures its CPU and memory behavior, models its energy and CO2e impact, and publishes the results to a Terraform-managed CloudWatch dashboard.

It is a research-adjacent instrumentation proof of concept, not a hardware power meter or a general-purpose carbon-accounting product. Its purpose is to make workload efficiency observable and provide a foundation for comparing a future LLM inference workload against a controlled baseline.

## What this demonstrates

- Terraform-managed application infrastructure with administrator-created, narrowly scoped runtime identities
- least-privilege workload identity and IMDSv2-only instance metadata access
- process-level CPU and memory sampling with custom CloudWatch metrics
- modeled energy and CO2e reporting through CodeCarbon
- automated, bounded recurring workload runs with `systemd`
- explicit teardown and cost-control discipline

## Architecture

```text
Terraform
   |
   +--> Read pre-existing EC2/Lambda runtime roles (no IAM mutation)
   +--> Default VPC + SSH-only security group
   +--> EC2 (explicit reviewed Ubuntu 22.04 AMI, encrypted EBS, IMDSv2 required)
   |       |
   |       +--> systemd timer --> Python workload + reporter
   |       |                         |
   |       |                         +--> CodeCarbon estimate
   |       |                         +--> CloudWatch PutMetricData
   |       |
   |       +--> CloudWatch agent --> host metrics + application log group
   |
   +--> CloudWatch dashboard (Carbontrace/App)
   +--> EventBridge rule --> Lambda --> state/age/lease checks --> stop this EC2 instance
```

## Metrics and methodology

The reporter samples the current Python process at one-second intervals and publishes aggregate values in the `Carbontrace/App` namespace. It validates every value as finite and non-negative before creating the CloudWatch client, so one invalid value cannot silently poison the five-metric batch.

| Metric | CloudWatch unit | Meaning |
|---|---|---|
| `CPUUtilizationCustom` | `Percent` | Average sampled process CPU usage (%) |
| `MemoryUtilizationPercent` | `Percent` | Average sampled process memory usage (%) |
| `EstimatedEnergyWh` | `None` | CodeCarbon modeled energy consumption, converted from kWh to Wh |
| `EstimatedWatts` | `None` | `EstimatedEnergyWh / elapsed hours`; an average modeled power value |
| `EstimatedCO2Grams` | `None` | CodeCarbon modeled CO2e, converted from kg to grams |

CloudWatch has no `Watts`, watt-hours, or grams standard unit. Those three modeled metrics therefore use the supported `None` unit and carry their semantic unit in the metric name and dashboard label. Supplying an unsupported unit rejects the complete `PutMetricData` request. [AWS CloudWatch metric units](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html#Metric)

The fixed metric dimensions are `Project`, `InstanceType`, and `WorkloadVersion`. Each structured run log records both a unique run ID and the exact deployed Git revision, but deliberately keeps those high-cardinality values out of metric dimensions. CloudWatch custom metrics are organized by namespace, metric name, and dimensions; each published metric uses one bounded `PutMetricData` call with standard retries, three total attempts, a three-second connection timeout, and a five-second read timeout. [AWS CloudWatch custom-metrics documentation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/publishingMetrics.html)

Every run emits a `measurement_complete` JSON record. A publishing run emits `publish_success` only after CloudWatch accepts the entire request. A rejected request emits `publish_failure` with a non-sensitive error type/code and then exits nonzero so systemd records the failure.

### Estimator configuration

The reporter uses CodeCarbon's offline tracker with an explicit, reproducible configuration:

- `tracking_mode="process"` aligns modeled energy and emissions with the process-level CPU and memory measurements.
- AWS deployment region `us-east-1` is recorded in every run and maps to CodeCarbon's bundled Virginia state electricity-mix model (`country_iso_code="USA"`, `region="virginia"`). This is a stable regional approximation, not live grid carbon intensity and not an AWS facility-specific measurement.
- `pue=1.0` is explicit. It intentionally excludes unverified data-center cooling and power-distribution overhead rather than inventing an AWS facility PUE.
- The run record includes the CodeCarbon version, tracking mode, AWS provider/region, carbon country/region and methodology, effective modeled carbon intensity, and PUE.

Only `us-east-1` currently has a reviewed Carbontrace methodology mapping. The reporter fails closed for another AWS Region until its mapping and assumptions are reviewed. See the [CodeCarbon tracker parameters](https://docs.codecarbon.io/latest/reference/api/) and [CodeCarbon methodology](https://docs.codecarbon.io/3.2/explanation/methodology/).

### Estimation limits

Energy, power, and CO2e values are **modeled estimates**, not readings from physical EC2 power telemetry. CPU and memory are sampled from the reporter process; CodeCarbon estimates that process's energy and applies its bundled Virginia electricity-mix model. Average watts are derived from modeled Wh divided by elapsed hours.

Treat measurements as useful for comparing controlled workload versions under the same pinned environment and methodology, not as an exact statement of physical electricity or CO2e consumed by an EC2 instance. Results may vary with host hardware, noisy-neighbor behavior, estimator hardware models, sampling, workload duration, and the age/coverage of the bundled electricity data. PUE 1.0 means the results exclude facility overhead.

## Prerequisites

- Terraform 1.11 or later (required for native S3 `use_lockfile` state locking)
- AWS CLI credentials for the target AWS account and `us-east-1`
- an existing EC2 key pair in that Region
- a pre-created S3 state bucket
- the two administrator-created runtime roles and exact EC2 instance profile documented in `bootstrap/runtime-roles/README.md`
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

Use the bootstrap output to fill your private `backend.hcl` file. The bootstrap state remains local in `bootstrap/terraform.tfstate`; keep it private, do not commit it, and do not delete it. If that local state is lost, recover ownership explicitly before making changes:

```bash
cd bootstrap
terraform import aws_s3_bucket.state carbontrace-tf-562325340670
terraform import aws_s3_bucket_versioning.state carbontrace-tf-562325340670
terraform import aws_s3_bucket_server_side_encryption_configuration.state carbontrace-tf-562325340670
terraform import aws_s3_bucket_public_access_block.state carbontrace-tf-562325340670
```

The S3 bucket has `prevent_destroy = true`, so normal infrastructure teardown intentionally retains the backend rather than treating it as a leaked resource.

`bootstrap/deployment-user-policy.json` documents the temporary, bucket-scoped permissions used to create and verify the backend. Terraform does not attach this policy. After backend verification, replace it with `bootstrap/backend-access-policy.json`, which keeps only bucket discovery, exact state-object read/write, and exact lockfile read/write/delete access. Restoring or modifying the bootstrap itself later requires temporarily restoring the reviewed bootstrap policy.

`bootstrap/main-deployment-policy.json` is the separate, human-reviewed policy for the main application stack. It cannot create, modify, attach, or delete IAM roles, policies, or instance profiles. It can read only the two exact runtime roles and exact instance profile, and each `iam:PassRole` grant is bound to one role and one expected AWS service. It contains no wildcard action names. Review it manually before attachment; Terraform never modifies the deployment user's own permissions.

An administrator must create the narrow runtime identities once using the exact trust and permissions documents under `bootstrap/runtime-roles/`. Terraform treats them as read-only data sources. This separation prevents the deployment user from writing a more powerful role policy and then executing code through EC2 or Lambda.

After the runtime identities and revised deployment architecture are reviewed, the deployment user should have two reviewed inline policies: `CarbontraceTerraformBackend` from `bootstrap/backend-access-policy.json` and `CarbontraceTerraformDeployment` from `bootstrap/main-deployment-policy.json`. Remove the temporary `CarbontraceTerraformBootstrap` policy only after the permanent backend policy is saved and backend maintenance is complete. Do not combine temporary bucket-creation permissions with ongoing deployment access.

## Deploy

Before the evidence deployment, an administrator must reapply the two reviewed inline runtime-policy documents under `bootstrap/runtime-roles/`. Terraform intentionally cannot modify those roles. This adds the EC2 instance's single-key lease permission and the Lambda's Region-limited `DescribeInstances` permission; review the policy diff before applying it.

1. Create a private local variable file. It is ignored by Git.

   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```

2. Fill in `my_ip` with your current public address followed by `/32`, your existing `key_name`, the reviewed explicit `ubuntu_ami_id`, and a full reviewed commit SHA for `app_revision`. The SHA must exist on the public remote because the instance clones the repository over HTTPS.

   ```bash
   git rev-parse HEAD
   ```

   The example pins Canonical Ubuntu 22.04 LTS amd64 release `20260702` in `us-east-1` as `ami-0d28727121d5d4a3c`, sourced from Canonical's public released-image catalog. Future AMI updates require an explicit reviewed source change; Terraform no longer follows `most_recent`.

3. Create a private `backend.hcl` from `backend.hcl.example`, using the S3 bucket created by `bootstrap/`. Keep `use_lockfile = true`.

4. Initialize, review, and apply. Never bypass the plan review.

   ```bash
   export AWS_PROFILE=carbontrace
   export AWS_REGION=us-east-1
   terraform init -backend-config=backend.hcl
   terraform fmt -check -recursive
   terraform validate
   terraform plan -out=main.tfplan
   terraform apply main.tfplan
   ```

5. Confirm the timer and reporter after the instance boots:

   ```bash
   sudo systemctl status carbontrace-reporter.timer
   sudo journalctl -u carbontrace-reporter.service
   ```

The first scheduled run occurs after the five-minute boot delay plus a small randomized delay. The CloudWatch dashboard is created during `terraform apply`; add a screenshot here after at least three real scheduled runs.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
.venv/bin/python -m app.metrics_reporter --duration-seconds 30 --work-size 2000
.venv/bin/python -m unittest discover -s tests -v
```

Do not pass `--publish` locally unless your environment has appropriately scoped AWS credentials and you intend to publish metrics.

When intentionally updating a top-level dependency, edit `requirements.in` and regenerate the Python 3.10-compatible lock with:

```bash
uv pip compile --python-version 3.10 --generate-hashes --no-header requirements.in -o requirements.txt
```

## Security controls

- SSH ingress is restricted to the one `/32` address supplied in `terraform.tfvars`; there is no `0.0.0.0/0` inbound rule.
- The SSH input validation accepts only globally routable public IPv4 `/32` values and rejects private, shared, loopback, link-local, documentation, benchmark, multicast, reserved, IPv6, and wider CIDRs.
- The Region and instance type are fixed by validation to `us-east-1` and `t3.micro`.
- The deployment user cannot create or alter runtime IAM identities; it can pass only `carbontrace-ec2-role` to EC2 and `carbontrace-auto-stop-role` to Lambda.
- The EC2 root volume is encrypted, uses `gp3`, and is deleted when the instance terminates.
- The instance requires IMDSv2 and disables instance metadata tags. The bootstrap script acquires an IMDSv2 token before reading instance data. [AWS IMDSv2 documentation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html)
- The application runs as the unprivileged `carbontrace` system user with a hardened systemd service.
- The EC2 bootstrap installs a version-pinned CloudWatch agent package and verifies its signature against AWS's documented signing-key fingerprint before installation.
- Direct dependencies are declared in `requirements.in`; the complete transitive environment is hash-locked in `requirements.txt`, installed with `--require-hashes`, and audited in CI.
- The instance role can publish only the approved CloudWatch namespaces and write only to the project log group.
- The auto-stop Lambda can stop only the profiler instance, and its execution log group has a 14-day retention policy.
- The reporter creates the expiring `CarbontraceActiveUntil` tag before a published run, confirms the exact value through bounded `DescribeInstances` retries, and removes it afterward. Measurement cannot begin until confirmation succeeds. Auto-stop reads the exact configured instance twice, refuses to stop non-running or recently launched instances, and honors only a numeric lease no more than 600 seconds into the future.
- The auto-stop grace period defaults to 900 seconds and is validated to remain between 300 and 3600 seconds. Its boto3 calls use standard retries with three total attempts, a three-second connection timeout, and a five-second read timeout.
- Terraform state, `.tfvars`, plans, and local virtual environments are excluded from version control. Do not put credentials, private keys, or backend values in tracked files.

## Cost controls and teardown

- Development defaults to `t3.micro`.
- The EventBridge/Lambda circuit breaker is enabled by default and periodically stops the instance. It is a backstop, not a substitute for cleanup.
- A CloudWatch alarm records any auto-stop Lambda error so circuit-breaker failures are visible.
- A stopped EC2 instance can still incur EBS charges.
- [Free Tier eligibility](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html) depends on the account creation date and remaining credits or legacy allowance; it is not a guarantee of zero cost.
- The instance uses a public IPv4 address for SSH and package downloads. [AWS VPC pricing](https://aws.amazon.com/vpc/pricing/) lists `$0.005` per public IPv4 address-hour when the account's applicable free allowance or credits do not cover it.

When finished, create and review a saved destroy plan from the same reviewed configuration and variable inputs used for the evidence deployment. Apply only that saved plan:

```bash
export AWS_PROFILE=carbontrace
export AWS_REGION=us-east-1
terraform init -backend-config=backend.hcl -lockfile=readonly
terraform validate
terraform plan -destroy -out=destroy.tfplan
terraform show destroy.tfplan
terraform apply destroy.tfplan
```

The reviewed destroy plan must remove all Terraform-managed main-stack components:

- the EC2 instance, encrypted root EBS volume, primary network interface, and security group;
- the application and Lambda CloudWatch log groups;
- the Lambda function and its EventBridge invoke permission;
- the EventBridge rule and target;
- the auto-stop error alarm; and
- the Carbontrace dashboard.

Run the read-only orphan verifier after the saved destroy plan completes:

```bash
AWS_PROFILE=carbontrace AWS_REGION=us-east-1 \
  .venv/bin/python scripts/verify_post_destroy.py
```

The verifier uses only describe/get/list operations, prints no credentials or Terraform state, and exits nonzero if a main-stack resource remains. It checks EC2 instances, EBS volumes, network interfaces, security groups, both log groups, the Lambda function and permission, EventBridge rule and target, alarm, and dashboard.

The following setup resources intentionally remain outside the main stack and are reported separately:

- the versioned Terraform backend bucket and its retained object versions;
- the administrator-managed EC2 and Lambda runtime roles;
- the `carbontrace-ec2-profile` instance profile;
- the deployment user and its backend/deployment policies;
- the existing EC2 key pair; and
- the operator's local PEM file.

Do not treat those retained setup resources as destroy failures. Remove them only through a separate, explicitly reviewed administrative cleanup after confirming no state or deployment depends on them.

## Future direction

The intentionally inefficient loop is a controlled baseline. The same reporter, metric schema, dashboard, and teardown controls can later wrap a real model-inference workload, enabling a focused comparison of model efficiency under a consistent measurement harness.
