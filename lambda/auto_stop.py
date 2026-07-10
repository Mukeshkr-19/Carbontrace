"""Stop the single GreenOps profiler instance configured for this function."""

import os

import boto3


def handler(event, context):
    """Stop the configured instance; stopping an already stopped instance is harmless."""
    instance_id = os.environ["INSTANCE_ID"]
    response = boto3.client("ec2").stop_instances(InstanceIds=[instance_id])
    return {"instance_id": instance_id, "stopping_instances": response["StoppingInstances"]}
