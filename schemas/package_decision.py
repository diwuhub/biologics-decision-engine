"""
PackageDecision — Terminal judgment object.

Must answer: what was decided, why, how confident, and what would change it.

Step 0A: Judgment Core Refactor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AnalyticalConclusion(Enum):
    """Analytical axis of the two-axis verdict structure (P5-A-1).

    Captures the purely analytical comparability finding, independent of
    regulatory-package readiness.
    """
    COMPARABLE = "comparable"
    COMPARABLE_WITH_CAVEATS = "comparable_with_caveats"
    NOT_COMPARABLE = "not_comparable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class PackagePosture(Enum):
    """Package-readiness axis of the two-axis verdict structure (P5-A-2).

    Captures the regulatory-package posture recommendation, independent of
    the analytical conclusion.
    """
    PROCEED = "proceed"
    PROCEED_WITH_CONDITIONS = "proceed_with_conditions"
    SUPPLEMENT_REQUIRED = "supplement_required"
    INVESTIGATION_REQUIRED = "investigation_required"
    DEFER = "defer"


# Valid package verdict values.
VALID_PACKAGE_VERDICTS = frozenset({
    "proceed",
    "proceed_with_conditions",
    "supplement_required",
    "investigation_required",
    "defer_package",
})

# Confidence band thresholds.
CONFIDENCE_BAND_HIGH = 0.8
CONFIDENCE_BAND_MODERATE_LOW = 0.5


@dataclass
class PostureRationaleFactors:
    """Structured factors driving the PackagePosture decision (P5-A-3b)."""
    top_blocking_clusters: List[str] = field(default_factory=list)
    elevated_attributes: List[str] = field(default_factory=list)
    evidence_gap_count: int = 0
    contradiction_present: bool = False
    precedent_status: str = ""  # "available", "sparse", "absent"
    n_attributes_assessed: int = 0
    n_attributes_comparable: int = 0


@dataclass
class ConfidenceBreakdown:
    """Decomposed confidence score (P5-B-1).

    Fields:
        analytical_confidence: Mean of attribute scores weighted by CQA designation.
        package_readiness: 1.0 minus blocking cluster penalty.
        evidence_completeness: Evidence strength combined with gap proportion.
        composite: 0.40*analytical + 0.35*readiness + 0.25*completeness.
        derivation_summary: Human-readable derivation narrative.
    """
    analytical_confidence: float = 0.0
    package_readiness: float = 0.0
    evidence_completeness: float = 0.0
    composite: float = 0.0
    derivation_summary: str = ""


def compute_confidence_band(confidence: float) -> str:
    """Derive confidence_band from numeric confidence value.

    high: > 0.8
    moderate: 0.5 - 0.8
    low: < 0.5
    """
    if confidence > CONFIDENCE_BAND_HIGH:
        return "high"
    elif confidence >= CONFIDENCE_BAND_MODERATE_LOW:
        return "moderate"
    else:
        return "low"


@dataclass
class PackageDecision:
    """Terminal judgment for a comparability case.

    Fields:
        case_id: Links to CaseContext.
        package_verdict: One of the five valid verdict values.
        confidence: 0-1.0 after all conservative adjustments.
        confidence_band: Derived from confidence (high/moderate/low).
        blocking_cluster_ids: Clusters that drove verdict to current level.
        supporting_cluster_ids: Clusters with strong authority evidence.
        required_followups: Next actions with type, target_cluster_id, rationale.
        predicted_reviewer_concerns: Concern entries with severity and basis.
        authority_confidence_summary: Human-readable authority posture.
        decision_rule_ids: IDs from Decision Rule Catalog applied.
        provenance_chain_ids: Links to ProvenanceChain records.
        abstain_flag: True only when system cannot make a defensible judgment.
        abstain_reason: Which ABST rule triggered abstain.
        next_best_action: Single highest-impact action.
        what_would_change_verdict: Counterfactual entries (Level 1 MVP).
    """

    # ---- Core Identity ----
    case_id: str
    package_verdict: str
    confidence: float

    # ---- Derived ----
    confidence_band: str = ""

    # ---- Cluster References ----
    blocking_cluster_ids: List[str] = field(default_factory=list)
    supporting_cluster_ids: List[str] = field(default_factory=list)

    # ---- Follow-ups and Concerns ----
    required_followups: List[Dict] = field(default_factory=list)
    predicted_reviewer_concerns: List[Dict] = field(default_factory=list)

    # ---- Authority ----
    authority_confidence_summary: str = ""

    # ---- Rule and Provenance Traceability ----
    decision_rule_ids: List[str] = field(default_factory=list)
    provenance_chain_ids: List[str] = field(default_factory=list)

    # ---- Abstain ----
    abstain_flag: bool = False
    abstain_reason: str = ""

    # ---- Actionable Outputs ----
    next_best_action: str = ""
    what_would_change_verdict: List[Dict] = field(default_factory=list)

    # ---- Evidence Traceability (P1-A) ----
    evidence_trace: List[Any] = field(default_factory=list)  # List[EvidenceTraceEntry]

    def __post_init__(self) -> None:
        if self.package_verdict not in VALID_PACKAGE_VERDICTS:
            raise ValueError(
                f"Invalid package_verdict '{self.package_verdict}'. "
                f"Must be one of {sorted(VALID_PACKAGE_VERDICTS)}"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, got {self.confidence}"
            )
        # Auto-derive confidence_band if not explicitly set.
        if not self.confidence_band:
            self.confidence_band = compute_confidence_band(self.confidence)
        if self.confidence_band not in ("high", "moderate", "low"):
            raise ValueError(
                f"Invalid confidence_band '{self.confidence_band}'. "
                f"Must be 'high', 'moderate', or 'low'."
            )
