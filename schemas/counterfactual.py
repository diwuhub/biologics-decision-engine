"""
CounterfactualEntry — Typed what_would_change_verdict schema.

Replaces untyped List[Dict] in PackageDecision.what_would_change_verdict
with structured entries describing what evidence gaps, if resolved, would
change the verdict.

Phase P1-C: Backend Logic Completion.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CounterfactualEntry:
    """A single counterfactual entry describing what would change the verdict.

    Fields:
        gap_id: Identifier for the gap (cluster_id or gap reference).
        current_state: Description of the current state (e.g., risk semantics).
        required_evidence: What evidence would resolve the gap.
        current_verdict: The current package verdict.
        verdict_if_resolved: What verdict would become if gap is resolved.
        confidence_delta: Expected confidence increase if resolved.
        priority: Priority for resolution (critical/high/medium/low).
    """
    gap_id: str
    current_state: str
    required_evidence: str
    current_verdict: str
    verdict_if_resolved: str
    confidence_delta: float = 0.0
    priority: str = "medium"  # critical / high / medium / low
    # P5-E: Two-axis counterfactuals
    analytical_if_resolved: str = ""   # AnalyticalConclusion value if gap resolved
    posture_if_resolved: str = ""      # PackagePosture value if gap resolved

    def to_dict(self) -> dict:
        """Convert to dictionary for backward compatibility."""
        return {
            "gap_id": self.gap_id,
            "current_state": self.current_state,
            "required_evidence": self.required_evidence,
            "current_verdict": self.current_verdict,
            "verdict_if_resolved": self.verdict_if_resolved,
            "confidence_delta": self.confidence_delta,
            "priority": self.priority,
            "analytical_if_resolved": self.analytical_if_resolved,
            "posture_if_resolved": self.posture_if_resolved,
        }
