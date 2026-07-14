"""Measure Carbontrace and optionally publish modeled metrics to CloudWatch."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import boto3
import psutil
from codecarbon import EmissionsTracker

from app.drain_app import WorkloadSummary, run_workload

METRIC_NAMESPACE = "Carbontrace/App"
WORKLOAD_VERSION = "v1"


@dataclass(frozen=True)
class ResourceSummary:
    """Aggregate process resource measurements collected during a workload run."""

    average_cpu_percent: float
    peak_cpu_percent: float
    average_memory_percent: float
    peak_memory_percent: float
    samples: int


class ProcessSampler:
    """Sample CPU and memory for the current process at a fixed interval."""

    def __init__(self, interval_seconds: float = 1.0) -> None:
        self._interval_seconds = interval_seconds
        self._process = psutil.Process()
        self._samples: list[tuple[float, float]] = []
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def start(self) -> None:
        self._process.cpu_percent(interval=None)
        self._thread.start()

    def stop(self) -> ResourceSummary:
        self._stop_event.set()
        self._thread.join(timeout=self._interval_seconds + 1)
        if not self._samples:
            return ResourceSummary(0.0, 0.0, 0.0, 0.0, 0)

        cpu_samples, memory_samples = zip(*self._samples)
        return ResourceSummary(
            average_cpu_percent=sum(cpu_samples) / len(cpu_samples),
            peak_cpu_percent=max(cpu_samples),
            average_memory_percent=sum(memory_samples) / len(memory_samples),
            peak_memory_percent=max(memory_samples),
            samples=len(self._samples),
        )

    def _sample(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            self._samples.append(
                (
                    self._process.cpu_percent(interval=None),
                    self._process.memory_percent(),
                )
            )


@dataclass(frozen=True)
class RunSummary:
    """One locally measured and CodeCarbon-modeled workload run."""

    run_id: str
    started_at: str
    revision: str
    workload: WorkloadSummary
    resources: ResourceSummary
    estimated_energy_wh: float
    estimated_watts: float
    estimated_co2_grams: float


def measure_run(duration_seconds: int, work_size: int) -> RunSummary:
    """Measure one workload run; energy and emissions are explicitly modeled estimates."""
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    tracker = EmissionsTracker(
        project_name="carbontrace",
        measure_power_secs=1,
        save_to_file=False,
        log_level="error",
    )
    sampler = ProcessSampler()
    sampler.start()
    try:
        tracker.start()
        try:
            workload = run_workload(duration_seconds, work_size)
        finally:
            emissions_kg = tracker.stop() or 0.0
    finally:
        resources = sampler.stop()

    energy_kwh = float(getattr(tracker.final_emissions_data, "energy_consumed", 0.0) or 0.0)
    energy_wh = energy_kwh * 1_000
    elapsed_hours = workload.duration_seconds / 3_600
    estimated_watts = energy_wh / elapsed_hours if elapsed_hours else 0.0

    return RunSummary(
        run_id=run_id,
        started_at=started_at,
        revision=os.environ.get("CARBONTRACE_REVISION", "local"),
        workload=workload,
        resources=resources,
        estimated_energy_wh=energy_wh,
        estimated_watts=estimated_watts,
        estimated_co2_grams=float(emissions_kg) * 1_000,
    )


def publish_metrics(summary: RunSummary, project_name: str, instance_type: str, region: str) -> None:
    """Publish aggregate metrics without a unique run dimension to control metric cost."""
    dimensions = [
        {"Name": "Project", "Value": project_name},
        {"Name": "InstanceType", "Value": instance_type},
        {"Name": "WorkloadVersion", "Value": WORKLOAD_VERSION},
    ]
    timestamp = datetime.fromisoformat(summary.started_at)
    metric_data = [
        {
            "MetricName": "CPUUtilizationCustom",
            "Dimensions": dimensions,
            "Timestamp": timestamp,
            "Value": summary.resources.average_cpu_percent,
            "Unit": "Percent",
        },
        {
            "MetricName": "MemoryUtilizationPercent",
            "Dimensions": dimensions,
            "Timestamp": timestamp,
            "Value": summary.resources.average_memory_percent,
            "Unit": "Percent",
        },
        {
            "MetricName": "EstimatedWatts",
            "Dimensions": dimensions,
            "Timestamp": timestamp,
            "Value": summary.estimated_watts,
            "Unit": "Watts",
        },
        {
            "MetricName": "EstimatedCO2Grams",
            "Dimensions": dimensions,
            "Timestamp": timestamp,
            "Value": summary.estimated_co2_grams,
            "Unit": "None",
        },
        {
            "MetricName": "EstimatedEnergyWh",
            "Dimensions": dimensions,
            "Timestamp": timestamp,
            "Value": summary.estimated_energy_wh,
            "Unit": "None",
        },
    ]
    boto3.client("cloudwatch", region_name=region).put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=metric_data,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure and report a Carbontrace workload run.")
    parser.add_argument("--duration-seconds", type=int, default=30)
    parser.add_argument("--work-size", type=int, default=2_000)
    parser.add_argument("--project-name", default=os.environ.get("CARBONTRACE_PROJECT", "Carbontrace"))
    parser.add_argument("--instance-type", default=os.environ.get("CARBONTRACE_INSTANCE_TYPE", "local"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--publish", action="store_true", help="Send aggregates to CloudWatch.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = measure_run(args.duration_seconds, args.work_size)
    print(json.dumps(asdict(summary), default=str))
    if args.publish:
        publish_metrics(summary, args.project_name, args.instance_type, args.region)


if __name__ == "__main__":
    main()
