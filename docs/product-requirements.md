# PRD: Carbontrace

**Author:** Sanjay
**Status:** Complete — implementation validated, documentation released, and main AWS stack intentionally destroyed
**Last updated:** 2026-07-18
**Validated revision:** `1cb74aa057ea36b9715f50ada168a9d2e3a91aa9`

---

## 1. Summary

Carbontrace is a small, infrastructure-as-code instrumentation harness that runs a deliberately inefficient workload on one EC2 `t3.micro`, measures process CPU and memory behavior, uses CodeCarbon to model energy and CO2e, and publishes five custom metrics plus structured logs to CloudWatch.

The final implementation was deployed and validated in AWS `us-east-1`. Four workload runs published successfully, the natural EventBridge/Lambda circuit breaker stopped the exact instance, and a reviewed saved destroy plan removed all ten Terraform-managed main-stack resources. The protected backend and administrator-managed prerequisites were retained by design.

The public validation record is the [final validation report](validation-report.md).

## 2. Product narrative

**Primary audience:** Sustainability/Green AI researchers and DevOps, SRE, platform, and cloud engineering reviewers.

**Core claim:** Carbontrace is a reproducible workload instrumentation and operational-safety demonstration. It does not directly measure EC2 power consumption. CPU and memory are process measurements; energy, average watts, and CO2e are modeled estimates suitable for controlled comparisons under the same pinned environment and methodology.

## 3. Goals and outcomes

- [x] Provision the main application stack with Terraform.
- [x] Keep the EC2/Lambda runtime roles and instance profile as administrator-created prerequisites.
- [x] Run an intentionally inefficient, bounded Python workload.
- [x] Measure process CPU and memory at one-second intervals.
- [x] Model energy and CO2e with CodeCarbon and derive average modeled watts.
- [x] Publish five stable-dimension CloudWatch custom metrics.
- [x] Provision a five-widget CloudWatch dashboard.
- [x] Automate recurring runs with a hardened `systemd` timer.
- [x] Protect active work with a confirmed, bounded EC2 tag lease.
- [x] Stop the exact instance naturally through EventBridge/Lambda after the minimum runtime.
- [x] Use reviewed saved plans for deployment and teardown.
- [x] Verify that no main-stack resource remained after destroy.
- [x] Document methodology and limitations honestly.
- [x] Publish a sanitized runtime-evidence visualization derived from the four verified runs and label it clearly as generated evidence rather than an AWS console screenshot.

All core requirements are complete. An actual AWS dashboard screenshot was not retained during the deployment and is therefore documented as an evidence limitation and optional future enhancement—not an incomplete implementation requirement.

## 4. Non-goals

- Kubernetes, autoscaling, multi-region, high availability, or real production traffic
- Prometheus/Grafana; this project intentionally demonstrates native AWS observability
- general-purpose carbon accounting
- scientifically exact facility or hardware power measurement
- claiming AWS-specific live grid intensity or data-center PUE
- automatic removal of protected backend or administrator-managed prerequisites
- a production LLM workload in this phase

## 5. Final architecture

The final architecture is documented as Mermaid in the [main README](../README.md) and the [validation report](validation-report.md). Its core flow is:

```text
Terraform
  -> exact-ID Canonical AMI lookup
  -> EC2 t3.micro
     -> hardened systemd timer/service
     -> carbontrace user
     -> psutil process sampling + bounded workload
     -> CodeCarbon offline model
     -> CloudWatch metrics
     -> structured logs through cwagent
  -> five-widget CloudWatch dashboard
  -> Lambda error alarm
  -> EventBridge hourly rule
     -> Lambda exact-instance state/age/lease checks
     -> StopInstances

Protected S3 backend -> retained after main-stack destroy
```

## 6. Final technical decisions

| Area | Final choice | Rationale |
|---|---|---|
| Network | Default VPC plus one custom security group | Keeps the PoC bounded; SSH is restricted to one operator-supplied public IPv4 `/32` whose value remains outside Git |
| Compute | One `t3.micro` | Cost-bounded and enforced by validation and IAM |
| Image | Required exact Ubuntu AMI ID, validated against Canonical owner `099720109477` | Prevents silent `most_recent` drift |
| Root storage | Encrypted 8 GiB `gp3`, delete on termination | Bounded storage with encryption and deterministic cleanup |
| Metadata | IMDSv2 required, hop limit 1 | Reduces metadata credential exposure |
| Runtime users | `carbontrace` for application; `cwagent` for agent | Separates application and telemetry privileges |
| Estimator | CodeCarbon 3.2.8 offline, process mode | One explicit modeled source aligned with process sampling |
| Carbon model | Virginia, USA bundled state mix | Reviewed mapping for `us-east-1`; unsupported Regions fail closed |
| PUE | 1.0 | Excludes unverified facility overhead rather than inventing it |
| Metrics | Five custom metrics with stable dimensions | Supports comparison without high-cardinality run dimensions |
| Auto-stop | Hourly EventBridge to exact-instance Lambda | Cost circuit breaker with state, age, and lease checks |
| Activity lease | `CarbontraceActiveUntil`, confirmed before work | Prevents auto-stop from racing an active workload |
| Teardown | Reviewed saved destroy plan plus read-only verifier | Ensures reproducible removal and independent orphan checks |
| IAM | Administrator-managed runtime identities | Prevents deployment-role privilege-escalation chains |

## 7. Requirements by phase

### Phase 1 — Infrastructure as code

- [x] Separate protected S3 backend with native lockfile support
- [x] Required account-specific variables kept in ignored local configuration
- [x] Default VPC and deterministic subnet lookup
- [x] SSH security group restricted to one globally routable `/32`
- [x] Administrator-created EC2 role, Lambda role, and instance profile
- [x] Terraform reads and passes exact runtime identities without mutating them
- [x] Exact-ID AMI lookup restricted to Canonical
- [x] Encrypted 8 GiB `gp3` root volume
- [x] IMDSv2 required with hop limit 1
- [x] Immutable application revision verified after checkout
- [x] Reviewed saved plan applied exactly
- [x] Reviewed saved destroy plan applied exactly
- [x] Sanitized final validation report produced; raw plans/state/evidence remain private

### Phase 2 — Workload and reporter

- [x] Deliberately inefficient but bounded workload
- [x] One-second process CPU and memory sampling with psutil
- [x] CodeCarbon offline process-scoped estimation
- [x] Derived average modeled watts from modeled Wh and elapsed hours
- [x] Structured `measurement_complete`, `publish_success`, and `publish_failure` events
- [x] Nonzero exit on publication failure
- [x] Metric values validated before AWS client creation
- [x] Deployed through immutable `user_data` bootstrap
- [x] Hardened `systemd` service and recurring timer
- [x] Reporter ran as the unprivileged `carbontrace` user
- [x] Four real measurement and publication runs validated

### Phase 3 — CloudWatch observability

- [x] `CPUUtilizationCustom` with unit `Percent`
- [x] `MemoryUtilizationPercent` with unit `Percent`
- [x] `EstimatedWatts` with unit `None`
- [x] `EstimatedEnergyWh` with unit `None`
- [x] `EstimatedCO2Grams` with unit `None`
- [x] Stable `Project`, `InstanceType`, and `WorkloadVersion` dimensions
- [x] At least three one-minute datapoints verified for every metric
- [x] Exactly five dashboard widgets
- [x] Every widget configured with `Average` and a 300-second period
- [x] Four publish successes and zero publication failures in application logs
- [x] Terraform-provisioned dashboard and log groups
- [x] Sanitized runtime-evidence visualization published from the verified run values without claiming it is an AWS console screenshot

### Phase 4 — Auto-stop and cost hygiene

- [x] EventBridge enabled at `rate(1 hour)`
- [x] Exactly one Lambda target
- [x] Exact-instance `DescribeInstances` checks
- [x] 900-second minimum runtime
- [x] 600-second maximum accepted active lease horizon
- [x] Lease checked before work and twice by Lambda
- [x] Malformed and excessively future-dated leases cannot suppress stop indefinitely
- [x] Lambda uses `StopInstances`, never `TerminateInstances`
- [x] Natural EventBridge invocation requested stop at age 1270 seconds
- [x] Instance subsequently verified stopped
- [x] Lambda error alarm provisioned
- [x] Saved destroy removed the full main stack
- [x] Read-only verifier found no main-stack orphans

### Phase 5 — Documentation and release

- [x] README updated to final validated/destroyed status
- [x] Mermaid architecture documented
- [x] Sanitized runtime-evidence visualization added to the README
- [x] Measurements and modeled estimates distinguished explicitly
- [x] Final validation and teardown report added
- [x] Raw evidence, plans, state, private variables, keys, and audit output ignored
- [x] Public templates use `<AWS_ACCOUNT_ID>` rather than an account-specific identifier
- [x] MIT license and project presentation assets published
- [x] Human review, commit, push, and remote CI completed for the documentation release

## 8. Validation outcome

The final runtime evidence establishes:

- cloud-init success and exact deployed revision
- active timer and correct runtime-user separation
- four `measurement_complete` events
- four `publish_success` events and zero `publish_failure`
- five metric series with at least three datapoints each
- five correctly configured dashboard widgets
- an enabled hourly EventBridge rule with one target
- a natural `stop_requested` decision for the exact instance
- a subsequently stopped instance
- no credentials or private-key patterns detected in reviewed logs

CPU averages slightly above 100% were preserved because process accounting can represent logical CPU execution time. They were not silently clamped.

## 9. Teardown outcome

The saved destroy plan completed with:

```text
Apply complete! Resources: 0 added, 0 changed, 10 destroyed.
```

Removed resources:

1. CloudWatch dashboard
2. EventBridge rule
3. EventBridge target
4. application log group
5. Lambda log group
6. Lambda error alarm
7. EC2 instance
8. Lambda function
9. Lambda invocation permission
10. security group

Terraform state contained zero managed resources. The read-only verifier reported all EC2, EBS, network-interface, security-group, log, Lambda, EventBridge, permission, alarm, and dashboard checks clear.

The protected backend bucket and versions, runtime roles, instance profile, deployment identity and policies, EC2 key pair, and operator-local PEM remain intentionally outside the main-stack lifecycle.

## 10. Evidence policy and known gaps

Raw evidence is local-only and ignored. Public documentation uses sanitized placeholders and aggregated or selected fields.

Verified archive SHA-256:

```text
4375ea6599a60338d50aa68a25418cc36fae6c549fd73ae3519c51cced75619a
```

The completed post-destroy transcript independently hashes to `3a34622d3ef4f8053ed5ed9c23b9adf8b9fd31df96ba173db88242059a5f465f` and is recorded with the archive digest in the verified ignored `evidence/SHA256SUMS` manifest. Its displayed `88afcb3b…` value was calculated before checksum lines were appended back into that same file with `tee -a`; it is an intermediate pre-append digest, not authentication of the completed transcript.

No dashboard screenshot or standalone alarm-state snapshot exists in the archive. The Terraform configuration and post-destroy verifier cover the alarm lifecycle, but the report does not claim unavailable runtime screenshots. The published runtime graphic is generated only from sanitized verified values and is labeled accordingly.

## 11. Future work

- replace the synthetic workload with controlled machine-learning training and LLM inference workloads
- add per-epoch and per-request attribution under the same pinned estimator methodology
- compare workload revisions under the same metric and estimator contract
- evaluate additional reviewed Region-to-carbon-model mappings
- remove SSH in favor of Session Manager if the required security architecture is added
- retain a safely sanitized dashboard screenshot in a future evidence run
- preserve the final evidence manifest and raw transcript without normalization or regeneration
- consider external experimental calibration if more rigorous energy claims are needed

These optional extensions do not change the completed status of the validated implementation, documentation release, or teardown.
