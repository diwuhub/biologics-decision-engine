#!/usr/bin/env python3
"""
E4: QA Agent Entry Point.

Runs all capability probes, detects drift, and reports results.

Usage:
    python3 qa/run_qa.py
    python3 qa/run_qa.py --verbose
    python3 qa/run_qa.py --cap CAP-001
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is importable
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml")
    sys.exit(1)

from qa.capability_probe import probe_capability, ProbeResult
from qa.drift_detector import detect_drift, DriftReport

VISION_SPEC_PATH = Path(__file__).parent / "vision_spec.yaml"
TEST_DOCS_DIR = Path(PROJECT_ROOT) / "benchmarks" / "real_documents"


def load_vision_spec() -> dict:
    """Load the vision specification YAML."""
    with open(VISION_SPEC_PATH) as f:
        return yaml.safe_load(f)


def run_all_probes(
    vision_spec: dict,
    target_cap: str | None = None,
) -> list[ProbeResult]:
    """Run probes for all (or one) capabilities."""
    capabilities = vision_spec.get("capabilities", {})
    results = []

    test_docs = str(TEST_DOCS_DIR) if TEST_DOCS_DIR.is_dir() else None

    for cap_id in sorted(capabilities.keys()):
        if target_cap and cap_id != target_cap:
            continue
        result = probe_capability(cap_id, vision_spec, test_docs_dir=test_docs)
        results.append(result)

    return results


def print_report(
    probe_results: list[ProbeResult],
    drift_report: DriftReport,
    verbose: bool = False,
) -> None:
    """Pretty-print the QA report."""
    sep = "=" * 76
    print(sep)
    print("  BIOLOGICS DECISION ENGINE -- QA CAPABILITY REPORT")
    print(sep)
    print(f"\n  {drift_report.summary}")
    print()

    # Per-capability table
    print(f"  {'Cap ID':<12s} {'Name':<35s} {'Status':<8s} {'Assertions':>10s}")
    print(f"  {'-'*12} {'-'*35} {'-'*8} {'-'*10}")

    for r in probe_results:
        assertion_str = (
            f"{r.assertions_passed}/{r.assertions_total}"
            if r.assertions_total > 0
            else "-"
        )
        print(
            f"  {r.capability_id:<12s} {r.capability_name:<35s} "
            f"{r.status.upper():<8s} {assertion_str:>10s}"
        )

    # Drift items
    if drift_report.drift_items:
        print(f"\n  {'='*76}")
        print("  DRIFT ITEMS")
        print(f"  {'='*76}")
        for item in drift_report.drift_items:
            severity_tag = item.severity.upper()
            print(f"\n  [{severity_tag}] {item.capability_id} -- {item.drift_type}")
            print(f"    {item.description}")

    if verbose:
        print(f"\n  {'='*76}")
        print("  DETAILED RESULTS")
        print(f"  {'='*76}")
        for r in probe_results:
            print(f"\n  [{r.capability_id}] {r.capability_name}")
            print(f"    Status: {r.status.upper()}")
            print(f"    Assertions: {r.assertions_passed}/{r.assertions_total}")
            print(f"    Elapsed: {r.elapsed_seconds:.2f}s")
            if r.failure_details:
                for detail in r.failure_details:
                    print(f"    - {detail}")
            if r.error:
                print(f"    ERROR: {r.error}")

    print(f"\n{sep}")


def main():
    parser = argparse.ArgumentParser(
        description="Biologics Decision Engine -- QA Capability Probe"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show detailed per-capability results"
    )
    parser.add_argument(
        "--cap", help="Probe a single capability (e.g., CAP-001)"
    )
    args = parser.parse_args()

    vision_spec = load_vision_spec()
    probe_results = run_all_probes(vision_spec, target_cap=args.cap)
    drift_report = detect_drift(probe_results, vision_spec)

    print_report(probe_results, drift_report, verbose=args.verbose)

    # Exit code: non-zero if critical drift detected
    n_critical = sum(
        1 for d in drift_report.drift_items if d.severity == "critical"
    )
    sys.exit(1 if n_critical > 0 else 0)


if __name__ == "__main__":
    main()
