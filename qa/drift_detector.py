"""
E3: Drift Detector.

Compares current probe results against the vision spec to detect
capability drift -- capabilities that were passing but now fail,
or new capabilities that should be tested.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from qa.capability_probe import ProbeResult

logger = logging.getLogger(__name__)


@dataclass
class DriftItem:
    """A single drift observation."""
    capability_id: str
    drift_type: str  # "regression", "new_failure", "coverage_gap"
    description: str
    severity: str    # "critical", "warning", "info"


@dataclass
class DriftReport:
    """Aggregate drift detection results."""
    n_capabilities: int
    n_probed: int
    n_passed: int
    n_failed: int
    n_skipped: int
    n_errors: int
    drift_items: List[DriftItem] = field(default_factory=list)
    summary: str = ""

    @property
    def has_drift(self) -> bool:
        return len(self.drift_items) > 0


def detect_drift(
    probe_results: List[ProbeResult],
    vision_spec: Dict[str, Any],
) -> DriftReport:
    """Detect capability drift by comparing probe results to the vision spec.

    Parameters
    ----------
    probe_results : list of ProbeResult
        Results from probing each capability.
    vision_spec : dict
        Loaded vision_spec.yaml contents.

    Returns
    -------
    DriftReport
        Summary of drift items and overall health.
    """
    capabilities = vision_spec.get("capabilities", {})
    n_capabilities = len(capabilities)

    n_probed = 0
    n_passed = 0
    n_failed = 0
    n_skipped = 0
    n_errors = 0
    drift_items: List[DriftItem] = []

    # Index probe results by cap_id
    results_by_id = {r.capability_id: r for r in probe_results}

    for cap_id, cap_spec in capabilities.items():
        cap_status = cap_spec.get("status", "planned")
        cap_name = cap_spec.get("name", cap_id)

        result = results_by_id.get(cap_id)

        if result is None:
            # Capability in spec but not probed
            if cap_status == "implemented":
                drift_items.append(DriftItem(
                    capability_id=cap_id,
                    drift_type="coverage_gap",
                    description=f"Implemented capability '{cap_name}' was not probed",
                    severity="warning",
                ))
            continue

        n_probed += 1

        if result.status == "pass":
            n_passed += 1
        elif result.status == "fail":
            n_failed += 1
            if cap_status == "implemented":
                drift_items.append(DriftItem(
                    capability_id=cap_id,
                    drift_type="regression",
                    description=(
                        f"Implemented capability '{cap_name}' FAILED: "
                        f"{result.assertions_failed}/{result.assertions_total} assertions failed. "
                        + "; ".join(result.failure_details[:3])
                    ),
                    severity="critical",
                ))
            else:
                drift_items.append(DriftItem(
                    capability_id=cap_id,
                    drift_type="new_failure",
                    description=(
                        f"Planned capability '{cap_name}' failed probe "
                        f"({result.assertions_failed} failures)"
                    ),
                    severity="info",
                ))
        elif result.status == "skip":
            n_skipped += 1
        elif result.status == "error":
            n_errors += 1
            drift_items.append(DriftItem(
                capability_id=cap_id,
                drift_type="regression",
                description=f"Capability '{cap_name}' errored: {result.error}",
                severity="critical" if cap_status == "implemented" else "warning",
            ))

    # Build summary
    parts = [
        f"Probed {n_probed}/{n_capabilities} capabilities: "
        f"{n_passed} pass, {n_failed} fail, {n_skipped} skip, {n_errors} error."
    ]
    if drift_items:
        n_critical = sum(1 for d in drift_items if d.severity == "critical")
        n_warning = sum(1 for d in drift_items if d.severity == "warning")
        parts.append(f"Drift detected: {n_critical} critical, {n_warning} warning.")
    else:
        parts.append("No drift detected.")

    return DriftReport(
        n_capabilities=n_capabilities,
        n_probed=n_probed,
        n_passed=n_passed,
        n_failed=n_failed,
        n_skipped=n_skipped,
        n_errors=n_errors,
        drift_items=drift_items,
        summary=" ".join(parts),
    )
