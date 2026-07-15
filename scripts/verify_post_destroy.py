#!/usr/bin/env python3
"""Read-only verification that the Terraform-managed Carbontrace main stack is gone."""

from __future__ import annotations

import json
import os
import subprocess
import sys


PROJECT_TAG = "Carbontrace"
APPLICATION_LOG_GROUP = "/aws/carbontrace/carbontrace"
LAMBDA_LOG_GROUP = "/aws/lambda/carbontrace-auto-stop"
LAMBDA_NAME = "carbontrace-auto-stop"
EVENT_RULE_NAME = "carbontrace-auto-stop"
ALARM_NAME = "carbontrace-auto-stop-errors"
DASHBOARD_NAME = "carbontrace-dashboard"

ALLOWED_OPERATIONS = {
    ("ec2", "describe-instances"),
    ("ec2", "describe-volumes"),
    ("ec2", "describe-network-interfaces"),
    ("ec2", "describe-security-groups"),
    ("logs", "describe-log-groups"),
    ("lambda", "get-function"),
    ("lambda", "get-policy"),
    ("events", "describe-rule"),
    ("events", "list-targets-by-rule"),
    ("cloudwatch", "describe-alarms"),
    ("cloudwatch", "get-dashboard"),
}
NOT_FOUND_MARKERS = (
    "ResourceNotFoundException",
    "ResourceNotFound",
    "does not exist",
)


def _aws(
    profile: str,
    region: str,
    service: str,
    operation: str,
    *arguments: str,
    not_found_ok: bool = False,
) -> dict | None:
    if (service, operation) not in ALLOWED_OPERATIONS:
        raise ValueError(f"Disallowed AWS operation: {service} {operation}")
    command = [
        "aws",
        service,
        operation,
        *arguments,
        "--profile",
        profile,
        "--region",
        region,
        "--output",
        "json",
        "--no-cli-pager",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        if not_found_ok and any(marker in result.stderr for marker in NOT_FOUND_MARKERS):
            return None
        raise RuntimeError(
            f"Read-only AWS check failed for {service} {operation} "
            f"with exit code {result.returncode}."
        )
    return json.loads(result.stdout or "{}")


def _record(remains: list[str], resource_type: str, identifiers: list[str]) -> None:
    if identifiers:
        remains.append(f"{resource_type}: {', '.join(sorted(set(identifiers)))}")
        print(f"[REMAINS] {resource_type}: {', '.join(sorted(set(identifiers)))}")
    else:
        print(f"[clear] {resource_type}")


def verify(profile: str, region: str) -> int:
    remains: list[str] = []
    project_filter = f"Name=tag:Project,Values={PROJECT_TAG}"

    instances = _aws(
        profile,
        region,
        "ec2",
        "describe-instances",
        "--filters",
        project_filter,
        "Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped",
    )
    instance_ids = [
        instance["InstanceId"]
        for reservation in instances.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    _record(remains, "EC2 instances", instance_ids)

    volumes = _aws(
        profile,
        region,
        "ec2",
        "describe-volumes",
        "--filters",
        project_filter,
    )
    _record(
        remains,
        "EBS volumes",
        [volume["VolumeId"] for volume in volumes.get("Volumes", [])],
    )

    security_groups = _aws(
        profile,
        region,
        "ec2",
        "describe-security-groups",
        "--filters",
        project_filter,
    )
    security_group_ids = [
        group["GroupId"] for group in security_groups.get("SecurityGroups", [])
    ]
    _record(remains, "security groups", security_group_ids)

    network_interface_ids: set[str] = set()
    tagged_interfaces = _aws(
        profile,
        region,
        "ec2",
        "describe-network-interfaces",
        "--filters",
        project_filter,
    )
    network_interface_ids.update(
        interface["NetworkInterfaceId"]
        for interface in tagged_interfaces.get("NetworkInterfaces", [])
    )
    for group_id in security_group_ids:
        grouped_interfaces = _aws(
            profile,
            region,
            "ec2",
            "describe-network-interfaces",
            "--filters",
            f"Name=group-id,Values={group_id}",
        )
        network_interface_ids.update(
            interface["NetworkInterfaceId"]
            for interface in grouped_interfaces.get("NetworkInterfaces", [])
        )
    _record(remains, "network interfaces", list(network_interface_ids))

    for log_group_name, label in (
        (APPLICATION_LOG_GROUP, "application log group"),
        (LAMBDA_LOG_GROUP, "Lambda log group"),
    ):
        logs = _aws(
            profile,
            region,
            "logs",
            "describe-log-groups",
            "--log-group-name-prefix",
            log_group_name,
        )
        exact_matches = [
            item["logGroupName"]
            for item in logs.get("logGroups", [])
            if item.get("logGroupName") == log_group_name
        ]
        _record(remains, label, exact_matches)

    function = _aws(
        profile,
        region,
        "lambda",
        "get-function",
        "--function-name",
        LAMBDA_NAME,
        not_found_ok=True,
    )
    _record(remains, "Lambda function", [LAMBDA_NAME] if function else [])

    rule = _aws(
        profile,
        region,
        "events",
        "describe-rule",
        "--name",
        EVENT_RULE_NAME,
        not_found_ok=True,
    )
    _record(remains, "EventBridge rule", [EVENT_RULE_NAME] if rule else [])
    targets = None
    if rule:
        targets = _aws(
            profile,
            region,
            "events",
            "list-targets-by-rule",
            "--rule",
            EVENT_RULE_NAME,
        )
    _record(
        remains,
        "EventBridge targets",
        [target.get("Id", "unknown") for target in (targets or {}).get("Targets", [])],
    )

    permission = None
    if function:
        permission = _aws(
            profile,
            region,
            "lambda",
            "get-policy",
            "--function-name",
            LAMBDA_NAME,
            not_found_ok=True,
        )
    _record(
        remains,
        "Lambda invoke permission",
        ["AllowEventBridgeInvoke"] if permission else [],
    )

    alarms = _aws(
        profile,
        region,
        "cloudwatch",
        "describe-alarms",
        "--alarm-names",
        ALARM_NAME,
    )
    _record(
        remains,
        "CloudWatch alarm",
        [alarm["AlarmName"] for alarm in alarms.get("MetricAlarms", [])],
    )

    dashboard = _aws(
        profile,
        region,
        "cloudwatch",
        "get-dashboard",
        "--dashboard-name",
        DASHBOARD_NAME,
        not_found_ok=True,
    )
    _record(remains, "CloudWatch dashboard", [DASHBOARD_NAME] if dashboard else [])

    print("[retained] Terraform backend bucket and object versions")
    print("[retained] IAM runtime roles and carbontrace-ec2-profile instance profile")
    print("[retained] deployment user and deployment/backend policies")
    print("[retained] EC2 key pair and operator-local PEM file")

    if remains:
        print(f"Post-destroy verification failed: {len(remains)} resource categories remain.")
        return 1
    print("Post-destroy verification passed: no main-stack resources remain.")
    return 0


def main() -> int:
    profile = os.environ.get("AWS_PROFILE", "").strip()
    region = os.environ.get("AWS_REGION", "").strip()
    if not profile or not region:
        print("AWS_PROFILE and AWS_REGION must both be set.", file=sys.stderr)
        return 2
    return verify(profile, region)


if __name__ == "__main__":
    raise SystemExit(main())
