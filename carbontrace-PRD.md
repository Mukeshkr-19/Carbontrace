# PRD: Carbontrace

**Author:** Sanjay
**Status:** Active build
**Last updated:** 2026-07-09

---

## 1. Summary

A small, fully IaC-provisioned pipeline that runs a deliberately unoptimized workload on EC2, measures its real-time CPU/memory footprint, estimates its energy and CO2 impact, and visualizes all of it on a CloudWatch dashboard. The end deliverable is a clean, well-documented GitHub repo intended as a research-adjacent artifact — evidence that Sanjay can learn unfamiliar cloud systems, make defensible engineering decisions, and connect DevOps practice to a Green AI / Sustainability AI research question.

**Primary audience for the finished repo:** Professor Asif Imran (Sustainability AI / Green AI research). Secondary audience: DevOps/SRE/Platform hiring managers.

**Core narrative:** This is not a claim to directly measure EC2 power consumption. It is a reproducible instrumentation harness that records workload behavior and produces explicitly modeled energy/emissions estimates, ready to compare future workload versions such as LLM inference.

---

## 2. Goals

- Provision all infrastructure via Terraform — zero manual console clicks.
- Run a Python workload that intentionally wastes CPU/memory ("code smell" / unoptimized loop simulating an inefficient LLM script).
- Capture CPU%, memory, estimated wattage, and estimated CO2 emissions per run.
- Push those metrics to CloudWatch as custom metrics.
- Visualize them on a Terraform-provisioned CloudWatch dashboard.
- Document methodology honestly, especially around CO2 estimation (it's modeled, not measured — say so).
- Ship with automatic teardown so nothing runs (or costs money) unattended.
- Keep an engineering journal in the repo (`docs/build-notes.md`) recording decisions, test evidence, failures, and fixes. This demonstrates learning and ownership, not just a finished result.

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
        │        CloudWatch Custom Namespace: GreenOps/App         │
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
| Teardown | `terraform destroy` (manual) + optional EventBridge/Lambda auto-stop |
| CI (optional/stretch) | GitHub Actions: `terraform fmt -check`, `terraform validate`, `terraform plan` on PR |
| State backend | S3 + DynamoDB (reuse existing backend infra from ATLAS if available) |

## 7. Detailed Requirements by Phase

### Phase 1 — Infrastructure as Code

- [ ] Bootstrap or document the Terraform backend separately: S3 bucket and DynamoDB lock table must exist before `terraform init`; the project must not silently depend on another repo's state infrastructure
- [ ] Default VPC data lookup (no custom VPC — see PRD decision log)
- [ ] Security group: inbound SSH 22 from `var.my_ip` only; outbound all
- [ ] IAM role + instance profile, policy scoped to required actions only:
  - `cloudwatch:PutMetricData`
  - `logs:CreateLogStream`
  - `logs:PutLogEvents`
  - `logs:DescribeLogStreams` (required by the CloudWatch agent to write to a stream)
- [ ] EC2 instance: Ubuntu 22.04 AMI (via `data "aws_ami"` filter, not hardcoded ID), `t3.micro`, instance profile attached, SG attached
- [ ] `user_data` script: installs CloudWatch agent, Python3, pip, git; installs a pinned application revision and `requirements.txt`. The deployed Git commit SHA is recorded as a metric dimension or in run output.
- [ ] `terraform plan` / `apply` / `destroy` all run clean with no manual intervention
- [ ] All variables (IP, project name, instance type, region) externalized to `variables.tf` — nothing hardcoded in `main.tf`
- [ ] Phase exit evidence: save a redacted `terraform plan`, a successful apply output, and a successful destroy output in the build notes

### Phase 2 — Energy Drain Application

- [ ] `drain_app.py`: intentionally unoptimized workload — e.g., nested loops doing redundant recomputation, unnecessary large in-memory data structures, no caching, single-threaded where parallelism would be trivial. Comment clearly *why* each inefficiency is there (this is a teaching artifact, not sloppy code).
- [ ] `metrics_reporter.py`:
  - Uses `psutil` to sample CPU% and memory usage every 1s during the run
  - Uses CodeCarbon (`EmissionsTracker`) as the authoritative estimator for energy (kWh) and CO2 (g) for the run
  - Derives average estimated watts as `estimated_Wh / elapsed_hours`; it does not introduce a second CPU/TDP-based power model
  - Aggregates samples, computes a run summary (avg/peak CPU, avg/peak memory, duration, total estimated Wh, average estimated watts, total estimated CO2g)
- [ ] Both scripts run standalone locally first (validate output makes sense) before deploying to EC2
- [ ] Deployed to EC2 via `user_data` or a simple `scp` deploy script — document whichever you pick
- [ ] Runs on a schedule via `cron` or `systemd` timer (not just manual invocation) — this is the "automated pipeline" part of the pitch
- [ ] Each run receives a unique `RunId`; output is written locally before metrics are sent, so failed metric publishing can be diagnosed

### Phase 3 — Observability Dashboard

- [ ] `metrics_reporter.py` pushes to CloudWatch custom namespace (e.g., `GreenOps/App`) via boto3:
  - `CPUUtilizationCustom` (%)
  - `MemoryUtilizationPercent` (%)
  - `EstimatedWatts`
  - `EstimatedCO2Grams`
- [ ] Every custom metric uses stable dimensions: `Project`, `InstanceType`, and `WorkloadVersion`. `RunId` is recorded in structured logs rather than as a metric dimension, avoiding unnecessary metric-cardinality cost.
- [ ] `aws_cloudwatch_dashboard` Terraform resource defining:
  - Line widget: CPU% over time
  - Line widget: Memory over time
  - Line/number widget: Estimated CO2g / Watts per run
- [ ] Dashboard is provisioned by `terraform apply` — zero console clicks
- [ ] Screenshot of populated dashboard included in README (metrics need at least a few runs of real data first)

### Phase 4 — Teardown & Cost Hygiene

- [ ] `terraform destroy` documented as the standard end-of-session step
- [ ] EventBridge scheduled rule → Lambda that stops (not terminates) the EC2 instance after a configurable N hours, so a forgotten session doesn't run indefinitely. README explains that stopped instances can still incur EBS charges and that `terraform destroy` remains the final cleanup step.
- [ ] README includes an explicit "How to tear this down" section

### Phase 5 — Documentation

- [ ] README.md includes:
  - One-paragraph project summary (what it does, why)
  - Architecture diagram (ASCII is fine, or draw.io export)
  - **Methodology section**: CodeCarbon is the single modeling source for energy and emissions; average watts are derived from its estimated energy and the recorded duration. Cite CodeCarbon's methodology and state relevant configuration assumptions.
  - Explicit statement that these are *estimates*, not hardware-measured values, and why (no power telemetry access on EC2)
  - Setup instructions (`terraform init/plan/apply`, deploy steps)
  - Teardown instructions
  - Dashboard screenshot
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
- `terraform destroy` cleanly removes everything, verified via `aws ec2 describe-instances`.
- README is readable by someone with zero context and clearly explains the estimation methodology.
- No AWS costs incurred beyond Free Tier during development.
- Sanjay can explain the data flow, IAM permissions, estimation limitations, and one debugging decision without relying on the README.
- `docs/build-notes.md` shows at least three meaningful observations or fixes made during implementation.

## 11. Open Questions

- Final call on EventBridge/Lambda auto-stop: build it after the core pipeline works. It is part of the final project, but must not delay evidence of the core workload-to-dashboard flow.
- Should the "swap in a real LLM workload" extension actually be built, or just described as future work in the README? (Recommendation: describe only, for now — don't scope-creep before Phase 1–5 ship.)

---

*Use this doc as the spec when prompting Codex/Cursor — reference section numbers (e.g. "implement Phase 1, section 7") rather than re-explaining context each time.*
