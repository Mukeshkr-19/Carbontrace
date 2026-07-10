# PRD: Carbontrace

**Author:** Sanjay
**Status:** Active build
**Last updated:** 2026-07-10

---

## 1. Summary

A small, fully IaC-provisioned pipeline that runs a deliberately unoptimized workload on EC2, measures its real-time CPU/memory footprint, estimates its energy and CO2 impact, and visualizes all of it on a CloudWatch dashboard. The end deliverable is a clean, well-documented GitHub repo intended as a research-adjacent artifact — evidence that Sanjay can learn unfamiliar cloud systems, make defensible engineering decisions, and connect DevOps practice to a Green AI / Sustainability AI research question.

**Primary audience for the finished repo:** Professor Asif Imran (Sustainability AI / Green AI research). Secondary audience: DevOps/SRE/Platform hiring managers.

**Core narrative:** This is not a claim to directly measure EC2 power consumption. It is a reproducible instrumentation harness that records workload behavior and produces explicitly modeled energy/emissions estimates, ready to compare future workload versions such as LLM inference.

---

## 2. Goals

- Provision all application infrastructure via Terraform — zero manual creation of application resources in the console. Deployment-user policies remain a deliberate human-reviewed prerequisite.
- Run a Python workload that intentionally wastes CPU/memory ("code smell" / unoptimized loop simulating an inefficient LLM script).
- Capture CPU%, memory, estimated wattage, and estimated CO2 emissions per run.
- Push those metrics to CloudWatch as custom metrics.
- Visualize them on a Terraform-provisioned CloudWatch dashboard.
- Document methodology honestly, especially around CO2 estimation (it's modeled, not measured — say so).
- Ship with automatic teardown so nothing runs (or costs money) unattended.
- Keep a detailed private learning journal outside Git, recording decisions, test evidence, failures, fixes, and line-by-line concepts without exposing assistant or editor metadata in the public repository.

## 3. Non-Goals

- No Kubernetes, no auto-scaling, no multi-region.
- No Prometheus/Grafana stack (already demonstrated in a separate project, CloudMind) — this project intentionally uses native AWS observability (CloudWatch) to show breadth, not repetition.
- No production hardening, no HA, no real traffic, and no claim of scientifically precise carbon accounting.
- Not a general-purpose carbon-accounting tool — scoped to a single instance, single workload, single run pattern.

## 4. Constraints

- **Cost:** Free Tier only during development. `t3.micro` for all dev/test runs. `t3.medium` only for a single final "official run" if desired, and only with auto-shutdown active.
- **Security:** SSH restricted to a single IP (`/32` CIDR), no `0.0.0.0/0` inbound ever. Prefer AWS Systems Manager Session Manager if it is added later, then remove SSH entirely. IAM actions are explicitly scoped; where AWS requires resource wildcards (for example, CloudWatch metric publishing), document why.
- **Cost hygiene:** No instance may run unattended without a teardown mechanism (manual `terraform destroy` at minimum; EventBridge + Lambda auto-stop is the stretch goal).
- **Honesty:** CO2/wattage figures must be labeled as estimates in both code comments and README, with the estimation method cited.

## 5. Architecture

```
                     ┌─────────────────────────────┐
                     │   GitHub Repo (this project) │
                     │   Terraform + Python source   │
                     └──────────────┬────────────────┘
                                    │ terraform apply
                                    ▼
        ┌───────────────────────────────────────────────────┐
        │                  AWS (us-east-1)                    │
        │                                                     │
        │   Default VPC                                       │
        │   ┌───────────────────────────────────────────┐     │
        │   │  Security Group (SSH from my IP only)       │     │
        │   │                                               │     │
        │   │   EC2 instance (t3.micro, Ubuntu)            │     │
        │   │   - IAM instance profile (least priv)        │     │
        │   │   - CloudWatch agent (OS metrics)             │     │
        │   │   - drain_app.py (unoptimized workload)       │     │
        │   │   - metrics_reporter.py (CodeCarbon + boto3)  │     │
        │   └──────────────────┬────────────────────────────┘     │
        │                      │ put_metric_data                  │
        │                      ▼                                  │
        │       CloudWatch Custom Namespace: Carbontrace/App       │
        │        - CPUUtilizationCustom                            │
        │        - MemoryUtilization                               │
        │        - EstimatedWatts                                  │
        │        - EstimatedCO2Grams                                │
        │                      │                                  │
        │                      ▼                                  │
        │        CloudWatch Dashboard (Terraform-managed)          │
        │        - CPU widget / Memory widget / CO2-Watts widget   │
        │                                                          │
        │        EventBridge rule (optional) → Lambda → stop EC2   │
        └───────────────────────────────────────────────────┘
```

## 6. Tech Stack

| Layer | Tool |
|---|---|
| IaC | Terraform (AWS provider ~> 5.0) |
| Compute | EC2, Ubuntu 22.04 LTS, t3.micro |
| Networking | Default VPC + custom Security Group |
| IAM | Custom least-privilege role + instance profile |
| Metrics collection | Python (`psutil` for CPU/memory; CodeCarbon as the single source of modeled energy/CO2 estimates) |
| Metrics ingestion | boto3 `put_metric_data` → CloudWatch custom namespace |
| Dashboard | `aws_cloudwatch_dashboard` (Terraform resource, not console-built) |
| Teardown | `terraform destroy` (manual) + EventBridge/Lambda auto-stop |
| CI | GitHub Actions: Terraform formatting/validation, Python tests, and dependency audit |
| State backend | S3 with native lockfile (`use_lockfile = true`) |

## 7. Detailed Requirements by Phase

### Phase 1 — Infrastructure as Code

- [x] Bootstrap and document the Terraform backend separately: the S3 bucket exists before `terraform init`, with `use_lockfile = true`; the project does not depend on another repo's state infrastructure
- [x] Default VPC data lookup (no custom VPC — see PRD decision log)
- [x] Security group: inbound SSH 22 from `var.my_ip` only; outbound all
- [x] IAM role + instance profile, policy scoped to required actions only:
  - `cloudwatch:PutMetricData`
  - `logs:CreateLogStream`
  - `logs:PutLogEvents`
  - `logs:DescribeLogStreams` (required by the CloudWatch agent to write to a stream)
- [x] EC2 instance configuration: Ubuntu 22.04 AMI (via `data "aws_ami"` filter, not hardcoded ID), `t3.micro`, instance profile attached, SG attached
- [x] `user_data` configuration: verifies and installs the CloudWatch agent, Python3, pip, git; installs a pinned application revision and pinned `requirements.txt`. The deployed Git commit SHA is recorded in structured run output.
- [ ] `terraform plan` / `apply` / `destroy` all run clean with no manual intervention
- [x] All variables (IP, project name, instance type, region) externalized to `variables.tf`; the account and cost-sensitive instance type are validated
- [ ] Phase exit evidence: save a redacted `terraform plan`, a successful apply output, and a successful destroy output in the build notes

### Phase 2 — Energy Drain Application

- [x] `drain_app.py`: intentionally unoptimized workload — redundant recomputation and an unnecessary but bounded in-memory allocation are clearly documented as teaching artifacts.
- [x] `metrics_reporter.py`:
  - Uses `psutil` to sample CPU% and memory usage every 1s during the run
  - Uses CodeCarbon (`EmissionsTracker`) as the authoritative estimator for energy (kWh) and CO2 (g) for the run
  - Derives average estimated watts as `estimated_Wh / elapsed_hours`; it does not introduce a second CPU/TDP-based power model
  - Aggregates samples, computes a run summary (avg/peak CPU, avg/peak memory, duration, total estimated Wh, average estimated watts, total estimated CO2g)
- [x] Both scripts run standalone locally before EC2 deployment; a real local CodeCarbon-modeled result was produced
- [ ] Deployed to EC2 via `user_data` or a simple `scp` deploy script — document whichever you pick
- [x] A hardened `systemd` service and timer are configured for scheduled runs
- [x] Each run receives a unique `RunId`, records the exact deployed revision, and writes structured output before metrics are sent

### Phase 3 — Observability Dashboard

- [x] `metrics_reporter.py` pushes to the `Carbontrace/App` CloudWatch custom namespace via boto3:
  - `CPUUtilizationCustom` (%)
  - `MemoryUtilizationPercent` (%)
  - `EstimatedWatts`
  - `EstimatedCO2Grams`
- [x] Every custom metric uses stable dimensions: `Project`, `InstanceType`, and `WorkloadVersion`. `RunId` and revision are recorded in structured logs rather than metric dimensions.
- [x] `aws_cloudwatch_dashboard` Terraform resource defines:
  - Line widget: CPU% over time
  - Line widget: Memory over time
  - Line/number widget: Estimated CO2g / Watts per run
- [ ] Dashboard is provisioned by `terraform apply` — zero console clicks
- [ ] Screenshot of populated dashboard included in README (metrics need at least a few runs of real data first)

### Phase 4 — Teardown & Cost Hygiene

- [x] `terraform destroy` documented as the standard end-of-session step
- [x] EventBridge scheduled rule → Lambda stops only the profiler EC2 instance after a configurable N hours; stopped-instance EBS costs and final destroy are documented
- [x] README includes an explicit teardown section

### Phase 5 — Documentation

- [x] README.md includes:
  - One-paragraph project summary (what it does, why)
  - Architecture diagram (ASCII is fine, or draw.io export)
  - **Methodology section**: CodeCarbon is the single modeling source for energy and emissions; average watts are derived from its estimated energy and the recorded duration. Cite CodeCarbon's methodology and state relevant configuration assumptions.
  - Explicit statement that these are *estimates*, not hardware-measured values, and why (no power telemetry access on EC2)
  - Setup instructions (`terraform init/plan/apply`, deploy steps)
  - Teardown instructions
  - [ ] Dashboard screenshot after real AWS runs
  - **Forward-looking note**: this profiler is architected so the "unoptimized loop" workload can be swapped for a real LLM inference workload later — turning this PoC into a general instrumentation harness for measuring model efficiency. (This is the sentence that connects it to Imran's research — don't bury it.)

## 8. Decision Log

| Decision | Choice | Rationale |
|---|---|---|
| VPC | Default VPC + custom SG | PoC doesn't need network isolation; saves a full debugging day for negligible benefit |
| Observability stack | CloudWatch (not Prometheus/Grafana) | Shows breadth vs. CloudMind project; faster to stand up for a PoC |
| Instance type (dev) | t3.micro | Free tier eligible; t3.medium is NOT free tier |
| CO2 measurement | CodeCarbon (single modeled source) | Avoids mixing incompatible estimators; no hardware power telemetry access on EC2, so values remain estimates |
| Wattage metric | Derived from estimated Wh / run duration | A transparent derived value, not a claimed hardware reading |
| IAM scope | Required actions only, with documented AWS-required resource wildcards | Least privilege and honest operational documentation |
| Reproducibility | Pinned application revision + documented backend bootstrap | A clean apply must not depend on an unstated branch or another project |

## 9. Milestones / Sessions (2 hrs/day)

| Day | Focus |
|---|---|
| 1 | Terraform scaffold, backend, default VPC lookup, security group |
| 2 | IAM role/policy (least priv), EC2 resource, instance profile attach |
| 3 | `terraform apply`, verify instance boots, SSH access works |
| 4 | CloudWatch agent install via user_data, verify OS-level metrics flowing (debug-heavy day) |
| 5 | Teardown automation — `destroy` workflow tested, then EventBridge/Lambda auto-stop |
| 6 | `drain_app.py` written and validated locally with CodeCarbon |
| 7 | Deploy script (scp or user_data-baked) to get code onto EC2, manual run confirmed |
| 8 | `metrics_reporter.py` — boto3 `put_metric_data` wired up, custom namespace populated |
| 9 | Cron/systemd timer for scheduled automated runs (debug-heavy day) |
| 10 | `aws_cloudwatch_dashboard` Terraform resource — CPU + memory widgets |
| 11 | CO2/wattage widget, layout polish, sanity-check numbers across multiple runs |
| 12 | README — architecture diagram, methodology section, setup/teardown docs |
| 13 | Buffer day — fix whatever broke in Day 12, full apply→destroy clean run to confirm reproducibility |

**ETA at 2hrs/day, dedicated focus:** ~3–3.5 weeks.

## 10. Success Criteria

- `terraform apply` from a clean clone reproduces the entire stack with zero manual steps.
- Dashboard shows real CPU/memory/CO2 data from at least 3 separate runs.
- `terraform destroy` cleanly removes the main application infrastructure, verified via `aws ec2 describe-instances`; the separately bootstrapped state bucket is intentionally retained and protected by `prevent_destroy`.
- README is readable by someone with zero context and clearly explains the estimation methodology.
- No AWS costs incurred beyond Free Tier during development.
- Sanjay can explain the data flow, IAM permissions, estimation limitations, and one debugging decision without relying on the README.
- The private ignored learning journal shows at least three meaningful observations or fixes made during implementation.

## 11. Remaining execution gates

- Human review and attachment of the permanent backend and main deployment policies.
- Saved main-stack plan review before apply.
- Real EC2 bootstrap, at least three runs, CloudWatch metric/log/dashboard evidence, and auto-stop verification.
- Saved destroy plan/apply and read-only cleanup verification.
- Final dashboard evidence and results section in the README.

---

*Use this document as the project specification and completion checklist.*
