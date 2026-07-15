# Carbontrace pre-existing runtime identities

These IAM resources are an administrator-created prerequisite. The Terraform deployment user may read and pass them but cannot create, edit, attach, or delete IAM roles, policies, or instance profiles.

Before running the commands below, replace every `<AWS_ACCOUNT_ID>` placeholder in the reviewed policy templates with the target 12-digit account ID, authenticate an administrator session for that account, and verify that the active identity is not the `carbontrace` deployment user. Run the commands from the repository root.

```bash
aws sts get-caller-identity

aws iam create-role \
  --role-name carbontrace-ec2-role \
  --description "Narrow runtime identity for the Carbontrace EC2 profiler" \
  --assume-role-policy-document file://bootstrap/runtime-roles/ec2-trust-policy.json \
  --tags Key=Project,Value=Carbontrace Key=ManagedBy,Value=Administrator

aws iam put-role-policy \
  --role-name carbontrace-ec2-role \
  --policy-name CarbontraceEc2Runtime \
  --policy-document file://bootstrap/runtime-roles/ec2-permissions-policy.json

aws iam create-instance-profile \
  --instance-profile-name carbontrace-ec2-profile \
  --tags Key=Project,Value=Carbontrace Key=ManagedBy,Value=Administrator

aws iam add-role-to-instance-profile \
  --instance-profile-name carbontrace-ec2-profile \
  --role-name carbontrace-ec2-role

aws iam create-role \
  --role-name carbontrace-auto-stop-role \
  --description "Narrow runtime identity for the Carbontrace auto-stop Lambda" \
  --assume-role-policy-document file://bootstrap/runtime-roles/lambda-trust-policy.json \
  --tags Key=Project,Value=Carbontrace Key=ManagedBy,Value=Administrator

aws iam put-role-policy \
  --role-name carbontrace-auto-stop-role \
  --policy-name CarbontraceAutoStopRuntime \
  --policy-document file://bootstrap/runtime-roles/lambda-permissions-policy.json
```

The EC2 role can publish only the two Carbontrace metric namespaces, write only to the exact application log group, and create or delete only the `CarbontraceActiveUntil` tag on an instance already tagged with both `Project=Carbontrace` and `Name=carbontrace-profiler`. It has Region-limited, read-only `DescribeInstances` access so the reporter can confirm the exact lease value is visible before beginning work. That expiring tag is the reporter's active-work lease.

The Lambda role has read-only `DescribeInstances` access restricted to `us-east-1`, can stop only an instance tagged with both `Project=Carbontrace` and `Name=carbontrace-profiler`, and writes only to the exact auto-stop log group. `DescribeInstances` requires `Resource: "*"`; it is a read-only EC2 API, Region-conditioned, and the function still requests and accepts only its exact configured instance ID. Neither runtime policy grants `TerminateInstances`.

Do not grant the `carbontrace` deployment user permission to alter these runtime identities.
