"""
Comparability Assessment -- Output Schemas.

Defines the structured report returned by the comparability pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from schemas.package_decision import (
    AnalyticalConclusion,
    PackagePosture,
    PostureRationaleFactors,
    ConfidenceBreakdown,
)


@dataclass
class AttributeResult:
    """Per-attribute comparability result."""
    name: str
    category: str
    pre_value: float
    post_value: float
    unit: str
    delta_pct: float
    score: float           # 0-1 (1 = identical)
    comparable: bool
    concern: str           # "none", "minor", "major", "critical"
    is_cqa: bool
    cqa_designation: str   # "CQA", "KQA", "QA", "Monitor"
    uncertainty: float     # residual uncertainty 0-1
    detail: str
    action: Optional[Dict[str, Any]] = None  # S-4 action recommendation
    score_breakdown: Optional[Dict[str, Any]] = None  # P1-B: Score transparency
    attribute_provenance: Optional[Dict[str, Any]] = None  # P2: Per-attribute evidence trace


@dataclass
class ComparabilityReport:
    """Full comparability assessment output.

    Note on evidence_strength_index: Not a calibrated probability. Composite
    index (0-1) reflecting data completeness, score quality, and uncertainty
    level.
    """
    product_name: str
    change_description: str
    overall_verdict: str        # DEPRECATED: use analytical_conclusion. Legacy 4-level: "Comparable", "Comparable With Caveats", "Not Comparable", "Insufficient Evidence"
    evidence_strength_index: float  # 0-1, composite index (see docstring)
    n_attributes: int
    n_cqa: int
    n_comparable: int           # attributes passing
    n_flagged: int              # attributes with concerns
    attribute_results: List[AttributeResult]
    cqa_summary: List[Dict[str, Any]]
    uncertainty_summary: Dict[str, Any]
    evidence_gaps: List[str]
    recommended_actions: List[str]
    action_summary: Optional[Dict[str, Any]] = None  # S-4 overall action summary
    package_verdict: Optional[Dict[str, Any]] = None  # P0-2: Package-level aggregation verdict
    provenance_chain: List[Dict] = field(default_factory=list)
    timestamp: str = ""
    # Phase 3 Judgment Core fields (None-guarded for legacy reports)
    judgment_core_verdict: Optional[str] = None       # DEPRECATED: use package_posture. JC internal verdict (5-level)
    judgment_confidence: Optional[float] = None       # JC confidence 0-1
    judgment_confidence_band: Optional[str] = None    # high/moderate/low
    blocking_clusters: Optional[List[Dict[str, Any]]] = None  # Blocking cluster summaries
    abstain_flag: Optional[bool] = None               # True if system abstained
    decision_rule_ids: Optional[List[str]] = None     # Applied decision rule catalog IDs
    what_would_change_verdict: Optional[List[Dict[str, Any]]] = None  # Counterfactuals
    evidence_trace: Optional[List[Any]] = None  # P1-A: List[EvidenceTraceEntry]

    # ---- P5-A: Two-Axis Verdict Structure ----
    analytical_conclusion: AnalyticalConclusion = AnalyticalConclusion.INSUFFICIENT_EVIDENCE
    package_posture: PackagePosture = PackagePosture.DEFER
    posture_rationale: str = ""
    posture_rationale_factors: PostureRationaleFactors = field(default_factory=PostureRationaleFactors)

    # ---- P5-B: Confidence Decomposition ----
    confidence_breakdown: Optional[ConfidenceBreakdown] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Convert any Enum values to their .value for JSON serialization
        _convert_enums(d)
        return d


def _convert_enums(obj):
    """Recursively convert Enum values to their .value in a dict/list tree."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if hasattr(v, 'value') and isinstance(v, type) is False:
                try:
                    obj[k] = v.value
                except Exception:
                    obj[k] = str(v)
            elif isinstance(v, (dict, list)):
                _convert_enums(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if hasattr(v, 'value') and isinstance(v, type) is False:
                try:
                    obj[i] = v.value
                except Exception:
                    obj[i] = str(v)
            elif isinstance(v, (dict, list)):
                _convert_enums(v)
