"""Bounded, intentionally inefficient workload used for Carbontrace measurements."""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict, dataclass

MIN_DURATION_SECONDS = 1
MAX_DURATION_SECONDS = 600
MIN_WORK_SIZE = 100
MAX_WORK_SIZE = 10_000
BUFFER_MULTIPLIER = 100


@dataclass(frozen=True)
class WorkloadSummary:
    """Summary returned after a bounded workload run."""

    duration_seconds: float
    iterations: int
    work_size: int
    allocation_items: int
    checksum: int


def validate_settings(duration_seconds: int, work_size: int) -> None:
    """Reject settings that could create an unexpectedly expensive local run."""
    if not MIN_DURATION_SECONDS <= duration_seconds <= MAX_DURATION_SECONDS:
        raise ValueError(
            f"duration_seconds must be between {MIN_DURATION_SECONDS} and "
            f"{MAX_DURATION_SECONDS}."
        )
    if not MIN_WORK_SIZE <= work_size <= MAX_WORK_SIZE:
        raise ValueError(
            f"work_size must be between {MIN_WORK_SIZE} and {MAX_WORK_SIZE}."
        )


def redundant_checksum(values: list[int]) -> int:
    """Return a deterministic checksum using deliberately repetitive arithmetic."""
    total = 0
    for value in values:
        total += sum((value * multiplier) % 97 for multiplier in range(1, 8))
    return total


def run_workload(duration_seconds: int, work_size: int) -> WorkloadSummary:
    """Run a deliberately wasteful but bounded workload for measurement exercises.

    The buffer and duplicate checksum are intentional: they model avoidable memory
    allocation and repeated computation in an unoptimized data-processing script.
    They are not recommended production techniques.
    """
    validate_settings(duration_seconds, work_size)
    started_at = time.monotonic()
    deadline = started_at + duration_seconds

    # Intentional teaching artifact: this data is larger than the computation needs.
    unnecessary_buffer = [index * index for index in range(work_size * BUFFER_MULTIPLIER)]
    values = list(range(work_size))
    iterations = 0
    checksum = 0

    while time.monotonic() < deadline:
        # Intentional teaching artifact: the second calculation repeats identical work.
        checksum = redundant_checksum(values)
        checksum ^= redundant_checksum(values)
        checksum ^= unnecessary_buffer[iterations % len(unnecessary_buffer)]
        iterations += 1

    return WorkloadSummary(
        duration_seconds=time.monotonic() - started_at,
        iterations=iterations,
        work_size=work_size,
        allocation_items=len(unnecessary_buffer),
        checksum=checksum,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the bounded Carbontrace teaching workload.")
    parser.add_argument("--duration-seconds", type=int, default=30)
    parser.add_argument("--work-size", type=int, default=2_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_workload(args.duration_seconds, args.work_size)
    print(asdict(summary))


if __name__ == "__main__":
    main()
