"""
RiskCluster — Primary judgment atom.

Attributes are evidence atoms; clusters are judgment atoms. Every verdict,
action recommendation, and reviewer concern traces to a cluster, never
directly to an attribute.

Step 0A: Judgment Core Refactor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# Valid values for cluster_type.
VALID_CLUSTER_TYPES = frozenset({
    "category_risk",
    "cqa_concern",
    "cross_category_gap",
    "single_attribute_critical",
})

# Valid values for risk_semantics.
VALID_RISK_SEMANTICS = frozenset({
    "assay_gap",
    "orthogonal_gap",
    "contradiction",
    "favorable_shift_requires_rationale",
    "trend_requires_monitoring",
    "no_precedent_low_confidence",
    "cross_geography_divergence",
    "pattern_concern_only",
    "sufficient_evidence",
})

# Valid values for base_concern_level (and concern_level).
VALID_CONCERN_LEVELS = frozenset({
    "none",
    "minor",
    "major",
    "critical",
})


@dataclass
class RiskCluster:
    """Primary judgment atom for the biologics decision engine.

    Identity fields are frozen at construction. Progressive fields are
    filled by successive pipeline stages (matcher, conservative policy,
    concern engine).
    """

    # ---- Identity Fields (frozen at construction) ----
    cluster_id: str
    cluster_type: str  # one of VALID_CLUSTER_TYPES
    dominant_category: str
    affected_attribute_ids: List[str]
    contains_cqa: bool
    base_concern_level: str  # one of VALID_CONCERN_LEVELS
    cluster_reason_summary: str
    risk_semantics: str  # one of VALID_RISK_SEMANTICS

    # ---- Progressive Fields (filled by pipeline stages) ----
    # Filled by: matcher (Step 2)
    orthogonal_support_level: Optional[str] = None  # strong/moderate/weak/absent
    functional_support_level: Optional[str] = None  # confirmed/partial/absent
    lot_adequacy: Optional[str] = None  # adequate/limited/insufficient
    contradiction_present: Optional[bool] = None
    matched_reference_ids: List[str] = field(default_factory=list)

    # Filled by: conservative policy (Step 3) / cluster builder
    concern_level: Optional[str] = None  # adjusted; may differ from base_concern_level
    base_cluster_score: Optional[float] = None
    package_blocking: Optional[bool] = None

    # Filled by: concern engine (Step 4)
    priority_score: Optional[float] = None
    likely_reviewer_concerns: List[str] = field(default_factory=list)
    recommended_followup_type: Optional[str] = None  # additional_testing/enhanced_monitoring/bridging_study/none

    def __post_init__(self) -> None:
        if self.cluster_type not in VALID_CLUSTER_TYPES:
            raise ValueError(
                f"Invalid cluster_type '{self.cluster_type}'. "
                f"Must be one of {sorted(VALID_CLUSTER_TYPES)}"
            )
        if self.risk_semantics not in VALID_RISK_SEMANTICS:
            raise ValueError(
                f"Invalid risk_semantics '{self.risk_semantics}'. "
                f"Must be one of {sorted(VALID_RISK_SEMANTICS)}"
            )
        if self.base_concern_level not in VALID_CONCERN_LEVELS:
            raise ValueError(
                f"Invalid base_concern_level '{self.base_concern_level}'. "
                f"Must be one of {sorted(VALID_CONCERN_LEVELS)}"
            )
        if not self.cluster_reason_summary or not self.cluster_reason_summary.strip():
            raise ValueError(
                "RiskCluster.cluster_reason_summary must be non-empty"
            )
        if not self.risk_semantics or not self.risk_semantics.strip():
            raise ValueError(
                "RiskCluster.risk_semantics must be non-empty"
            )
        if not isinstance(self.affected_attribute_ids, list):
            raise TypeError("affected_attribute_ids must be a list")
