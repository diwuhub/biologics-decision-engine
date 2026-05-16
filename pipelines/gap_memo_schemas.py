"""Dataclasses for the gap memo pipeline.

These classes keep the gap-memo product path independent from FastAPI/Pydantic
while still exposing a clean ``to_dict`` contract for API responses and
benchmark serialization.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class GapFinding:
    section: str
    checklist_item: str
    severity: str
    description: str
    remediation: str
    estimated_effort: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConsistencyFlag:
    finding_id: str
    category: str
    severity: str
    description: str
    section_a: str
    section_b: str
    value_a: str
    value_b: str
    suggested_resolution: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PredictedQuestion:
    question: str
    section: str
    probability: float
    suggested_response_approach: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GapMemo:
    product_name: str
    submission_type: str
    n_sections_reviewed: int
    n_gaps_found: int
    n_critical: int
    n_major: int
    n_minor: int
    gaps: List[GapFinding] = field(default_factory=list)
    consistency_flags: List[ConsistencyFlag] = field(default_factory=list)
    predicted_questions: List[PredictedQuestion] = field(default_factory=list)
    overall_readiness: str = "Not Ready"
    recommended_actions: List[str] = field(default_factory=list)
    compliance_score: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_name": self.product_name,
            "submission_type": self.submission_type,
            "n_sections_reviewed": self.n_sections_reviewed,
            "n_gaps_found": self.n_gaps_found,
            "n_critical": self.n_critical,
            "n_major": self.n_major,
            "n_minor": self.n_minor,
            "gaps": [gap.to_dict() for gap in self.gaps],
            "consistency_flags": [flag.to_dict() for flag in self.consistency_flags],
            "predicted_questions": [question.to_dict() for question in self.predicted_questions],
            "overall_readiness": self.overall_readiness,
            "recommended_actions": list(self.recommended_actions),
            "compliance_score": self.compliance_score,
            "timestamp": self.timestamp,
        }
