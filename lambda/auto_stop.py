"""Stop the single Carbontrace profiler instance configured for this function."""

import os
import re

import boto3

INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8}(?:[0-9a-f]{9})?$")


def handler(event, context):
    """Stop the configured instance; stopping an already stopped instance is harmless."""
    instance_id = os.environ["INSTANCE_ID"]
    if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
        raise ValueError("INSTANCE_ID must be a valid EC2 instance ID.")
    response = boto3.client("ec2").stop_instances(InstanceIds=[instance_id])
    return {"instance_id": instance_id, "stopping_instances": response["StoppingInstances"]}
