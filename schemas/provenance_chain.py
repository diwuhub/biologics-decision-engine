"""
ProvenanceChain -- Structured Decision Traceability (P0-3).

A ProvenanceChain captures the complete evidence lineage for one attribute decision:
  - Normative references (ROL Type A: binding guidelines)
  - Precedent references (ROL Type B: prior approvals, warnings)
  - User evidence matched
  - Inference explanation
  - Confidence and alternatives

This is the bridge between evidence services (Layer 2) and the decision workflow (Layer 1).
Every attribute action must carry a ProvenanceChain.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


@dataclass
class ProvenanceChain:
    """Structured decision traceability for one attribute.

    Fields:
        attribute_name: Name of the attribute being judged.
        normative_refs: List of ROL Normative reference IDs (binding, e.g., "ICH_Q5E_2.2").
        precedent_refs: List of ROL Precedent reference IDs (prior approvals, e.g., "FDA_BLA_761024").
        method_refs: List of ROL Method reference IDs (analytical standards).
        concern_refs: List of ROL Concern Pattern reference IDs (issue taxonomies).
        user_evidence: Dict mapping attribute -> {value, unit, lots, cv, method}.
        inference_summary: 1-3 sentence natural language explanation of the judgment.
        confidence: Overall confidence in this decision (0-1.0).
        alternative_conclusion: What would change if key evidence differed.
        human_override: Optional dict {override_by, reason, timestamp, references_added, references_disputed}.
        decision_rule_ids: [PATCH 7] List of aggregation rule IDs that fired.
        confidence_factors: [PATCH 7] Dict explaining why confidence is high/low.
        created_at: Timestamp when this chain was created.
        source_module: Which engine module created this.
    """
    attribute_name: str
    normative_refs: List[str] = field(default_factory=list)
    precedent_refs: List[str] = field(default_factory=list)
    method_refs: List[str] = field(default_factory=list)
    concern_refs: List[str] = field(default_factory=list)
    user_evidence: Dict[str, Any] = field(default_factory=dict)
    inference_summary: str = ""
    confidence: float = 0.5
    alternative_conclusion: str = ""
    human_override: Optional[Dict[str, Any]] = None
    # [PATCH 7] Audit trail: which rules fired and why confidence is what it is
    decision_rule_ids: List[str] = field(default_factory=list)
    confidence_factors: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    source_module: str = "unknown"

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to serializable dict (excludes None values)."""
        result = asdict(self)
        # Remove None values for cleaner JSON
        return {k: v for k, v in result.items() if v is not None}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ProvenanceChain:
        """Reconstruct from dict."""
        return cls(
            attribute_name=d.get('attribute_name', ''),
            normative_refs=d.get('normative_refs', []),
            precedent_refs=d.get('precedent_refs', []),
            method_refs=d.get('method_refs', []),
            concern_refs=d.get('concern_refs', []),
            user_evidence=d.get('user_evidence', {}),
            inference_summary=d.get('inference_summary', ''),
            confidence=d.get('confidence', 0.5),
            alternative_conclusion=d.get('alternative_conclusion', ''),
            human_override=d.get('human_override'),
            decision_rule_ids=d.get('decision_rule_ids', []),
            confidence_factors=d.get('confidence_factors', {}),
            created_at=d.get('created_at', ''),
            source_module=d.get('source_module', 'unknown'),
        )

    def to_export_row(self) -> Dict[str, str]:
        """Convert to a single row for CSV/Excel export.

        Flattens nested structures for tabular representation.
        """
        evidence_str = "; ".join([
            f"{k}={v.get('value', 'N/A')}{v.get('unit', '')}"
            for k, v in self.user_evidence.items()
        ]) if self.user_evidence else "None"

        return {
            'attribute': self.attribute_name,
            'normative_refs': ", ".join(self.normative_refs) or "None",
            'precedent_refs': ", ".join(self.precedent_refs) or "None",
            'user_evidence': evidence_str,
            'inference': self.inference_summary,
            'confidence': f"{self.confidence:.2f}",
            'alternatives': self.alternative_conclusion or "None",
            'override': self.human_override.get('reason', '') if self.human_override else "None",
        }

    @property
    def n_normative(self) -> int:
        """Count of normative references."""
        return len(self.normative_refs)

    @property
    def n_precedent(self) -> int:
        """Count of precedent references."""
        return len(self.precedent_refs)

    @property
    def has_override(self) -> bool:
        """Whether this decision was overridden by human judgment."""
        return self.human_override is not None
