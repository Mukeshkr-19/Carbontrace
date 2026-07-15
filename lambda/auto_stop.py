"""Safely stop the single Carbontrace profiler instance configured for this function."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8}(?:[0-9a-f]{9})?$")
DEFAULT_ACTIVE_WORKLOAD_TAG_KEY = "CarbontraceActiveUntil"
MAX_MIN_RUNTIME_SECONDS = 86_400
MAX_CONFIGURED_ACTIVE_LEASE_SECONDS = 3_600
EC2_CLIENT_CONFIG = Config(
    connect_timeout=3,
    read_timeout=5,
    retries={"mode": "standard", "total_max_attempts": 3},
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(decision: str, instance_id: str, **details: object) -> dict:
    record = {"decision": decision, "instance_id": instance_id, **details}
    print(json.dumps(record, sort_keys=True), flush=True)
    return record


def _required_bounded_seconds(name: str, maximum: int) -> int:
    raw_value = os.environ.get(name, "")
    if not raw_value.isdigit():
        raise ValueError(f"{name} must be a positive bounded whole number.")
    value = int(raw_value)
    if value <= 0 or value > maximum:
        raise ValueError(f"{name} must be a positive bounded whole number.")
    return value


def _error_details(error: Exception) -> dict:
    details = {"error_type": type(error).__name__}
    if isinstance(error, ClientError):
        details["error_code"] = error.response.get("Error", {}).get("Code", "Unknown")
    return details


def _is_instance_not_found(error: Exception) -> bool:
    return (
        isinstance(error, ClientError)
        and error.response.get("Error", {}).get("Code") == "InvalidInstanceID.NotFound"
    )


def _find_instance(response: dict, instance_id: str) -> dict | None:
    instances = [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
        if instance.get("InstanceId") == instance_id
    ]
    return instances[0] if instances else None


def _lease_status(
    instance: dict,
    now: datetime,
    tag_key: str,
    maximum_active_lease_seconds: int,
) -> tuple[str, str | None]:
    tags = {tag.get("Key"): tag.get("Value") for tag in instance.get("Tags", [])}
    lease_value = tags.get(tag_key)
    if lease_value is None:
        return "absent", None
    try:
        active_until = int(lease_value)
    except (TypeError, ValueError):
        return "invalid", "malformed"

    now_epoch = int(now.timestamp())
    if active_until <= now_epoch:
        return "expired", lease_value
    if active_until > now_epoch + maximum_active_lease_seconds:
        return "invalid", "beyond_maximum_horizon"
    return "active", lease_value


def _evaluate_lease(
    instance: dict,
    now: datetime,
    instance_id: str,
    tag_key: str,
    maximum_active_lease_seconds: int,
    phase: str,
) -> dict | None:
    status, value = _lease_status(
        instance,
        now,
        tag_key,
        maximum_active_lease_seconds,
    )
    if status == "active":
        details = {
            "active_workload_tag_key": tag_key,
            "active_until": value,
        }
        if phase != "initial":
            details["phase"] = phase
        return _emit("skipped_active_workload", instance_id, **details)
    if status == "invalid":
        _emit(
            "ignored_invalid_lease",
            instance_id,
            active_workload_tag_key=tag_key,
            reason=value,
            phase=phase,
        )
    return None


def handler(event, context):
    """Stop the configured instance only after state, age, and workload checks pass."""
    instance_id = os.environ["INSTANCE_ID"]
    if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
        raise ValueError("INSTANCE_ID must be a valid EC2 instance ID.")

    minimum_runtime_seconds = _required_bounded_seconds(
        "MIN_RUNTIME_SECONDS", MAX_MIN_RUNTIME_SECONDS
    )
    maximum_active_lease_seconds = _required_bounded_seconds(
        "MAX_ACTIVE_LEASE_SECONDS", MAX_CONFIGURED_ACTIVE_LEASE_SECONDS
    )
    active_workload_tag_key = os.environ.get(
        "ACTIVE_WORKLOAD_TAG_KEY", DEFAULT_ACTIVE_WORKLOAD_TAG_KEY
    )
    ec2 = boto3.client("ec2", config=EC2_CLIENT_CONFIG)

    try:
        response = ec2.describe_instances(InstanceIds=[instance_id])
    except Exception as error:
        if _is_instance_not_found(error):
            return _emit("skipped_not_found", instance_id)
        _emit("describe_failure", instance_id, **_error_details(error))
        raise

    instance = _find_instance(response, instance_id)
    if instance is None:
        return _emit("skipped_not_found", instance_id)

    state = instance.get("State", {}).get("Name", "unknown")
    if state != "running":
        return _emit("skipped_not_running", instance_id, state=state)

    launch_time = instance.get("LaunchTime")
    if not isinstance(launch_time, datetime):
        error = ValueError("DescribeInstances response did not contain a valid LaunchTime.")
        _emit("describe_failure", instance_id, **_error_details(error))
        raise error
    if launch_time.tzinfo is None:
        launch_time = launch_time.replace(tzinfo=timezone.utc)

    now = _utc_now()
    age_seconds = max(0, int((now - launch_time).total_seconds()))
    if age_seconds < minimum_runtime_seconds:
        return _emit(
            "skipped_within_grace_period",
            instance_id,
            age_seconds=age_seconds,
            minimum_runtime_seconds=minimum_runtime_seconds,
        )

    lease_decision = _evaluate_lease(
        instance,
        now,
        instance_id,
        active_workload_tag_key,
        maximum_active_lease_seconds,
        "initial",
    )
    if lease_decision is not None:
        return lease_decision

    # Re-read immediately before stopping to narrow the race with a reporter
    # acquiring its lease after the first state/age check.
    try:
        pre_stop_response = ec2.describe_instances(InstanceIds=[instance_id])
    except Exception as error:
        if _is_instance_not_found(error):
            return _emit("skipped_not_found", instance_id, phase="pre_stop")
        _emit("describe_failure", instance_id, phase="pre_stop", **_error_details(error))
        raise
    pre_stop_instance = _find_instance(pre_stop_response, instance_id)
    if pre_stop_instance is None:
        return _emit("skipped_not_found", instance_id, phase="pre_stop")
    pre_stop_state = pre_stop_instance.get("State", {}).get("Name", "unknown")
    if pre_stop_state != "running":
        return _emit(
            "skipped_not_running", instance_id, state=pre_stop_state, phase="pre_stop"
        )
    lease_decision = _evaluate_lease(
        pre_stop_instance,
        now,
        instance_id,
        active_workload_tag_key,
        maximum_active_lease_seconds,
        "pre_stop",
    )
    if lease_decision is not None:
        return lease_decision

    try:
        ec2.stop_instances(InstanceIds=[instance_id])
    except Exception as error:
        _emit("stop_failure", instance_id, **_error_details(error))
        raise

    return _emit(
        "stop_requested",
        instance_id,
        age_seconds=age_seconds,
        previous_state=state,
    )
