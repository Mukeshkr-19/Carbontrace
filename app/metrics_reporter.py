"""Measure Carbontrace and optionally publish modeled metrics to CloudWatch."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import version as package_version
from numbers import Real

import boto3
import psutil
from botocore.config import Config
from botocore.exceptions import ClientError
from codecarbon import OfflineEmissionsTracker

from app.drain_app import WorkloadSummary, run_workload

METRIC_NAMESPACE = "Carbontrace/App"
WORKLOAD_VERSION = "v1"
CODECARBON_VERSION = package_version("codecarbon")
TRACKING_MODE = "process"
PUE = 1.0
CARBON_METHODOLOGY = "codecarbon-offline-bundled-us-state-energy-mix"
AWS_REGION_CARBON_LOCATIONS = {
    "us-east-1": {
        "country_iso_code": "USA",
        "region": "virginia",
    },
}
CLOUDWATCH_CLIENT_CONFIG = Config(
    connect_timeout=3,
    read_timeout=5,
    retries={"mode": "standard", "total_max_attempts": 3},
)
ACTIVE_WORKLOAD_TAG_KEY = "CarbontraceActiveUntil"
DEFAULT_ACTIVE_LEASE_SECONDS = 300
LEASE_CONFIRMATION_TIMEOUT_SECONDS = 5.0
LEASE_CONFIRMATION_ATTEMPTS = 3
INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8}(?:[0-9a-f]{9})?$")
EC2_LEASE_CLIENT_CONFIG = Config(
    connect_timeout=3,
    read_timeout=5,
    retries={"mode": "standard", "total_max_attempts": 3},
)


@dataclass(frozen=True)
class ResourceSummary:
    """Aggregate process resource measurements collected during a workload run."""

    average_cpu_percent: float
    peak_cpu_percent: float
    average_memory_percent: float
    peak_memory_percent: float
    samples: int


@dataclass(frozen=True)
class EstimationMethodology:
    """Inputs needed to interpret and reproduce one modeled estimate."""

    estimator: str
    estimator_version: str
    tracking_mode: str
    cloud_provider: str
    cloud_region: str
    carbon_country_iso_code: str
    carbon_region: str
    carbon_intensity_source: str
    carbon_intensity_g_co2e_per_kwh: float
    pue: float


class ActiveWorkLease:
    """Short-lived EC2 tag lease preventing auto-stop during a published run."""

    def __init__(
        self,
        instance_id: str,
        region: str,
        lease_seconds: int = DEFAULT_ACTIVE_LEASE_SECONDS,
    ) -> None:
        if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
            raise ValueError("CARBONTRACE_INSTANCE_ID must be a valid EC2 instance ID.")
        if isinstance(lease_seconds, bool) or lease_seconds <= 0:
            raise ValueError("CARBONTRACE_ACTIVE_LEASE_SECONDS must be positive.")
        self.instance_id = instance_id
        self.lease_seconds = lease_seconds
        self.client = boto3.client(
            "ec2",
            region_name=region,
            config=EC2_LEASE_CLIENT_CONFIG,
        )

    def __enter__(self) -> "ActiveWorkLease":
        active_until = int(time.time()) + self.lease_seconds
        expected_value = str(active_until)
        self.client.create_tags(
            Resources=[self.instance_id],
            Tags=[{"Key": ACTIVE_WORKLOAD_TAG_KEY, "Value": expected_value}],
        )
        try:
            self._confirm_visible(expected_value)
        except Exception:
            try:
                self._remove_tag()
            except Exception:
                pass
            raise
        return self

    def __exit__(self, error_type, error, traceback) -> bool:
        try:
            self._remove_tag()
        except Exception:
            if error_type is None:
                raise
        return False

    def _confirm_visible(self, expected_value: str) -> None:
        deadline = time.monotonic() + LEASE_CONFIRMATION_TIMEOUT_SECONDS
        last_error: Exception | None = None
        for attempt in range(LEASE_CONFIRMATION_ATTEMPTS):
            try:
                response = self.client.describe_instances(
                    InstanceIds=[self.instance_id]
                )
                if self._visible_lease_value(response) == expected_value:
                    return
            except Exception as error:
                last_error = error

            if attempt == LEASE_CONFIRMATION_ATTEMPTS - 1:
                break
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break
            time.sleep(min(1.0 * (2**attempt), remaining_seconds))

        error = RuntimeError(
            "Active workload lease was not visible before the confirmation deadline."
        )
        if last_error is not None:
            raise error from last_error
        raise error

    def _visible_lease_value(self, response: dict) -> str | None:
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                if instance.get("InstanceId") != self.instance_id:
                    continue
                tags = {
                    tag.get("Key"): tag.get("Value")
                    for tag in instance.get("Tags", [])
                }
                return tags.get(ACTIVE_WORKLOAD_TAG_KEY)
        return None

    def _remove_tag(self) -> None:
        self.client.delete_tags(
            Resources=[self.instance_id],
            Tags=[{"Key": ACTIVE_WORKLOAD_TAG_KEY}],
        )


def active_work_lease(region: str) -> ActiveWorkLease:
    instance_id = os.environ.get("CARBONTRACE_INSTANCE_ID", "")
    raw_lease_seconds = os.environ.get(
        "CARBONTRACE_ACTIVE_LEASE_SECONDS", str(DEFAULT_ACTIVE_LEASE_SECONDS)
    )
    if not raw_lease_seconds.isdigit():
        raise ValueError("CARBONTRACE_ACTIVE_LEASE_SECONDS must be positive.")
    return ActiveWorkLease(instance_id, region, int(raw_lease_seconds))


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
    methodology: EstimationMethodology
    estimated_energy_wh: float
    estimated_watts: float
    estimated_co2_grams: float


def _require_finite_non_negative(name: str, value: object) -> float:
    """Return a CloudWatch-safe float or fail closed before publication."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite, non-negative number.")
    numeric_value = float(value)
    if not math.isfinite(numeric_value) or numeric_value < 0:
        raise ValueError(f"{name} must be a finite, non-negative number.")
    return numeric_value


def measure_run(
    duration_seconds: int,
    work_size: int,
    cloud_region: str = "us-east-1",
) -> RunSummary:
    """Measure one workload run; energy and emissions are explicitly modeled estimates."""
    try:
        carbon_location = AWS_REGION_CARBON_LOCATIONS[cloud_region]
    except KeyError as error:
        raise ValueError(
            f"No reviewed carbon methodology is configured for AWS Region {cloud_region!r}."
        ) from error

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    # CodeCarbon 3.2.8 has no AWS rows in its cloud-intensity dataset. Use the
    # reviewed offline state model and record the AWS deployment region separately.
    tracker = OfflineEmissionsTracker(
        project_name="carbontrace",
        measure_power_secs=1,
        save_to_file=False,
        log_level="error",
        tracking_mode=TRACKING_MODE,
        pue=PUE,
        country_iso_code=carbon_location["country_iso_code"],
        region=carbon_location["region"],
    )
    sampler = ProcessSampler()
    sampler.start()
    try:
        tracker.start()
        try:
            workload = run_workload(duration_seconds, work_size)
        finally:
            tracker.stop()
    finally:
        resources = sampler.stop()

    final_data = tracker.final_emissions_data
    if final_data is None:
        raise RuntimeError("CodeCarbon completed without final emissions data.")

    energy_kwh = _require_finite_non_negative(
        "estimated_energy_kwh", getattr(final_data, "energy_consumed", None)
    )
    if energy_kwh == 0:
        raise ValueError("estimated_energy_kwh must be greater than zero.")
    emissions_kg = _require_finite_non_negative(
        "estimated_emissions_kg", getattr(final_data, "emissions", None)
    )
    energy_wh = energy_kwh * 1_000
    elapsed_hours = workload.duration_seconds / 3_600
    estimated_watts = energy_wh / elapsed_hours if elapsed_hours else 0.0
    carbon_intensity = emissions_kg * 1_000 / energy_kwh

    return RunSummary(
        run_id=run_id,
        started_at=started_at,
        revision=os.environ.get("CARBONTRACE_REVISION", "local"),
        workload=workload,
        resources=resources,
        methodology=EstimationMethodology(
            estimator="CodeCarbon",
            estimator_version=CODECARBON_VERSION,
            tracking_mode=TRACKING_MODE,
            cloud_provider="aws",
            cloud_region=cloud_region,
            carbon_country_iso_code=carbon_location["country_iso_code"],
            carbon_region=carbon_location["region"],
            carbon_intensity_source=CARBON_METHODOLOGY,
            carbon_intensity_g_co2e_per_kwh=carbon_intensity,
            pue=PUE,
        ),
        estimated_energy_wh=energy_wh,
        estimated_watts=estimated_watts,
        estimated_co2_grams=emissions_kg * 1_000,
    )


def build_metric_data(summary: RunSummary, project_name: str, instance_type: str) -> list[dict]:
    """Build and validate the complete CloudWatch metric contract."""
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
            "Value": _require_finite_non_negative(
                "CPUUtilizationCustom", summary.resources.average_cpu_percent
            ),
            "Unit": "Percent",
        },
        {
            "MetricName": "MemoryUtilizationPercent",
            "Dimensions": dimensions,
            "Timestamp": timestamp,
            "Value": _require_finite_non_negative(
                "MemoryUtilizationPercent", summary.resources.average_memory_percent
            ),
            "Unit": "Percent",
        },
        {
            "MetricName": "EstimatedWatts",
            "Dimensions": dimensions,
            "Timestamp": timestamp,
            "Value": _require_finite_non_negative("EstimatedWatts", summary.estimated_watts),
            "Unit": "None",
        },
        {
            "MetricName": "EstimatedCO2Grams",
            "Dimensions": dimensions,
            "Timestamp": timestamp,
            "Value": _require_finite_non_negative(
                "EstimatedCO2Grams", summary.estimated_co2_grams
            ),
            "Unit": "None",
        },
        {
            "MetricName": "EstimatedEnergyWh",
            "Dimensions": dimensions,
            "Timestamp": timestamp,
            "Value": _require_finite_non_negative(
                "EstimatedEnergyWh", summary.estimated_energy_wh
            ),
            "Unit": "None",
        },
    ]
    return metric_data


def publish_metrics(
    summary: RunSummary,
    project_name: str,
    instance_type: str,
    region: str,
) -> None:
    """Publish aggregate metrics without a unique run dimension to control metric cost."""
    metric_data = build_metric_data(summary, project_name, instance_type)
    boto3.client(
        "cloudwatch",
        region_name=region,
        config=CLOUDWATCH_CLIENT_CONFIG,
    ).put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=metric_data,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure and report a Carbontrace workload run.")
    parser.add_argument("--duration-seconds", type=int, default=30)
    parser.add_argument("--work-size", type=int, default=2_000)
    parser.add_argument(
        "--project-name",
        default=os.environ.get("CARBONTRACE_PROJECT", "Carbontrace"),
    )
    parser.add_argument(
        "--instance-type",
        default=os.environ.get("CARBONTRACE_INSTANCE_TYPE", "local"),
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--publish", action="store_true", help="Send aggregates to CloudWatch.")
    return parser.parse_args()


def _measure_and_report(args: argparse.Namespace) -> None:
    summary = measure_run(args.duration_seconds, args.work_size, args.region)
    print(
        json.dumps(
            {"event": "measurement_complete", "summary": asdict(summary)},
            default=str,
        )
    )
    if args.publish:
        try:
            publish_metrics(summary, args.project_name, args.instance_type, args.region)
        except Exception as error:
            failure_record = {
                "event": "publish_failure",
                "run_id": summary.run_id,
                "namespace": METRIC_NAMESPACE,
                "metric_count": 5,
                "error_type": type(error).__name__,
            }
            if isinstance(error, ClientError):
                failure_record["error_code"] = error.response.get("Error", {}).get(
                    "Code", "Unknown"
                )
            print(json.dumps(failure_record), flush=True)
            raise
        print(
            json.dumps(
                {
                    "event": "publish_success",
                    "run_id": summary.run_id,
                    "namespace": METRIC_NAMESPACE,
                    "metric_count": 5,
                }
            ),
            flush=True,
        )


def main() -> None:
    args = parse_args()
    if args.publish:
        with active_work_lease(args.region):
            _measure_and_report(args)
    else:
        _measure_and_report(args)


if __name__ == "__main__":
    main()
