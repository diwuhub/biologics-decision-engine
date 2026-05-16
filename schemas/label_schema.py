"""
Label Schema — Core dataclasses for the labeling & training readiness layer.

Every scoring output in the platform becomes a LabelRecord — a prediction
paired with an initially-empty ground_truth slot. When an expert, experiment,
or regulatory outcome fills in the ground_truth, the record becomes a
training pair that can retrain narrow models to replace heuristics.

Dataclasses:
  1. LabelRecord — prediction + empty ground_truth (filled later)
  2. FeedbackEvent — expert accept/reject/modify action on a record
  3. EvidenceClaim — structured claim for admissibility engine
  4. NAMReadinessRecord — New Approach Methodology readiness assessment
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# =========================================================================
# 1. LabelRecord
# =========================================================================

@dataclass
class LabelRecord:
    """A prediction paired with an initially-empty ground truth slot.

    Lifecycle:
      1. Module produces a prediction → LabelRecord created (ground_truth=None)
      2. User sees the result
      3. Expert/experiment fills ground_truth later
      4. (prediction, ground_truth) becomes a training pair

    Fields:
        record_id: Unique ID (UUID4). Auto-generated if not provided.
        module: Source module name (e.g., 'comparability_graph', 'fda_warning_letters').
        timestamp: When the prediction was made.
        prediction: The module's output (flexible dict — schema varies per module).
        ground_truth: None until labeled. Filled with experimental/expert/regulatory data.
        annotator: Who provided the ground truth (name or ID).
        annotation_source: How the ground truth was obtained.
        confidence_delta: How much ground truth differed from prediction (0=perfect, 1=opposite).
        metadata: Module-specific context (input parameters, versions, etc.).
    """
    module: str
    prediction: Dict[str, Any]
    record_id: str = ""
    timestamp: str = ""
    ground_truth: Optional[Dict[str, Any]] = None
    annotator: Optional[str] = None
    annotation_source: Optional[str] = None  # 'expert' | 'experiment' | 'regulatory_outcome' | 'literature'
    confidence_delta: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.record_id:
            self.record_id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    @property
    def is_labeled(self) -> bool:
        """True if ground_truth has been filled in."""
        return self.ground_truth is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "module": self.module,
            "timestamp": self.timestamp,
            "prediction": self.prediction,
            "ground_truth": self.ground_truth,
            "annotator": self.annotator,
            "annotation_source": self.annotation_source,
            "confidence_delta": self.confidence_delta,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LabelRecord:
        return cls(
            record_id=data.get("record_id", ""),
            module=data["module"],
            timestamp=data.get("timestamp", ""),
            prediction=data["prediction"],
            ground_truth=data.get("ground_truth"),
            annotator=data.get("annotator"),
            annotation_source=data.get("annotation_source"),
            confidence_delta=data.get("confidence_delta"),
            metadata=data.get("metadata", {}),
        )


# =========================================================================
# 2. FeedbackEvent
# =========================================================================

@dataclass
class FeedbackEvent:
    """Expert feedback on a LabelRecord — accept, reject, or modify.

    Links to a LabelRecord via record_id. Captures the expert's action
    and reasoning, enabling active learning workflows.

    Fields:
        event_id: Unique ID (UUID4).
        record_id: Links to the LabelRecord being reviewed.
        action: What the expert did ('accept', 'reject', 'modify').
        modified_value: If action='modify', the corrected value.
        reason: Why the expert took this action.
        source_type: Type of evidence behind the feedback.
        timestamp: When the feedback was given.
    """
    record_id: str
    action: str  # 'accept' | 'reject' | 'modify'
    event_id: str = ""
    modified_value: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    source_type: str = "expert"  # 'expert' | 'experiment' | 'regulatory' | 'literature'
    timestamp: str = ""

    def __post_init__(self):
        if not self.event_id:
            self.event_id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "record_id": self.record_id,
            "action": self.action,
            "modified_value": self.modified_value,
            "reason": self.reason,
            "source_type": self.source_type,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FeedbackEvent:
        return cls(
            event_id=data.get("event_id", ""),
            record_id=data["record_id"],
            action=data["action"],
            modified_value=data.get("modified_value"),
            reason=data.get("reason"),
            source_type=data.get("source_type", "expert"),
            timestamp=data.get("timestamp", ""),
        )


# =========================================================================
# 3. EvidenceClaim (Science-to-Admissibility Engine)
# =========================================================================

@dataclass
class EvidenceClaim:
    """A structured claim extracted from a scientific source for admissibility assessment.

    Used by the admissibility engine to evaluate whether a scientific claim
    has sufficient evidence for regulatory acceptance.

    Fields:
        claim_id: Unique identifier.
        claim_text: The claim in natural language.
        source_type: Where the claim came from.
        source_url: URL or DOI of the source.
        extracted_entities: Key biological entities (targets, molecules, mechanisms).
        evidence_strength: Assessed strength of supporting evidence.
        regulatory_relevance: How relevant to regulatory decisions.
        admissibility_gap: What's missing for regulatory acceptance.
        six_question_scores: Scores on the 6 strategic questions.
    """
    claim_text: str
    source_type: str  # 'journal_paper' | 'fda_announcement' | 'conference_abstract' | 'company_pr' | 'wechat_article'
    claim_id: str = ""
    source_url: Optional[str] = None
    extracted_entities: List[str] = field(default_factory=list)
    evidence_strength: Optional[str] = None  # 'strong' | 'moderate' | 'weak' | 'anecdotal'
    regulatory_relevance: Optional[str] = None  # 'directly_cited' | 'indirectly_relevant' | 'background'
    admissibility_gap: Optional[List[str]] = None
    six_question_scores: Optional[Dict[str, float]] = None  # biology_credible, signal_measurable, model_translatable, cmc_supportable, regulatory_acceptable, commercial_window

    def __post_init__(self):
        if not self.claim_id:
            self.claim_id = str(uuid.uuid4())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "extracted_entities": self.extracted_entities,
            "evidence_strength": self.evidence_strength,
            "regulatory_relevance": self.regulatory_relevance,
            "admissibility_gap": self.admissibility_gap,
            "six_question_scores": self.six_question_scores,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceClaim:
        return cls(
            claim_id=data.get("claim_id", ""),
            claim_text=data["claim_text"],
            source_type=data["source_type"],
            source_url=data.get("source_url"),
            extracted_entities=data.get("extracted_entities", []),
            evidence_strength=data.get("evidence_strength"),
            regulatory_relevance=data.get("regulatory_relevance"),
            admissibility_gap=data.get("admissibility_gap"),
            six_question_scores=data.get("six_question_scores"),
        )


# =========================================================================
# 4. NAMReadinessRecord (New Approach Methodology)
# =========================================================================

@dataclass
class NAMReadinessRecord:
    """Assessment of a New Approach Methodology's readiness for regulatory use.

    NAMs (organoids, organ-on-chip, in silico models) are increasingly
    accepted by FDA/EMA as alternatives to animal testing. This record
    tracks the evidence supporting a NAM's qualification.

    Fields:
        record_id: Unique identifier.
        nam_type: Type of NAM being assessed.
        context_of_use: Specific regulatory application (e.g., 'hepatotoxicity screening').
        species_replaced: Which animal species this NAM could replace.
        validation_evidence: List of validation studies with concordance data.
        regulatory_precedent: FDA/EMA cases where similar NAMs were accepted.
        readiness_score: Overall readiness (0-1, computed from evidence).
        readiness_gaps: What's still needed for qualification.
        qualification_pathway: Regulatory pathway for formal qualification.
    """
    nam_type: str  # 'organoid' | 'organ_on_chip' | 'in_silico' | 'bioprinted' | 'iPSC_derived'
    context_of_use: str
    record_id: str = ""
    species_replaced: Optional[str] = None
    validation_evidence: List[Dict[str, Any]] = field(default_factory=list)
    regulatory_precedent: List[str] = field(default_factory=list)
    readiness_score: Optional[float] = None
    readiness_gaps: List[str] = field(default_factory=list)
    qualification_pathway: Optional[str] = None  # 'DDT' | 'ISTAND' | 'voluntary_submission' | 'none'

    def __post_init__(self):
        if not self.record_id:
            self.record_id = str(uuid.uuid4())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "nam_type": self.nam_type,
            "context_of_use": self.context_of_use,
            "species_replaced": self.species_replaced,
            "validation_evidence": self.validation_evidence,
            "regulatory_precedent": self.regulatory_precedent,
            "readiness_score": self.readiness_score,
            "readiness_gaps": self.readiness_gaps,
            "qualification_pathway": self.qualification_pathway,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NAMReadinessRecord:
        return cls(
            record_id=data.get("record_id", ""),
            nam_type=data["nam_type"],
            context_of_use=data["context_of_use"],
            species_replaced=data.get("species_replaced"),
            validation_evidence=data.get("validation_evidence", []),
            regulatory_precedent=data.get("regulatory_precedent", []),
            readiness_score=data.get("readiness_score"),
            readiness_gaps=data.get("readiness_gaps", []),
            qualification_pathway=data.get("qualification_pathway"),
        )
