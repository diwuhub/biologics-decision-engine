"""
Provenance Schemas — SP v5 Section 8.1.1.

Two core schemas:
  1. ProvenanceRecord — traces one piece of evidence to its source
  2. EvidenceResult — wraps an evidence assessment with its provenance chain

These are the formal contracts between Layer 2 (Evidence Services) and
Layer 1 (Decision Workflow). Every evidence-based judgment in the pipeline
must produce an EvidenceResult that carries ProvenanceRecords.

Design principle: ProvenanceRecord is NOT optional metadata. It is a
first-class output that ships with every recommendation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ProvenanceRecord:
    """Traces one piece of evidence to its source.

    Fields:
        record_id: Unique identifier (auto-generated UUID4).
        source_type: Where the evidence came from.
            'guideline' — ICH, FDA guidance, EMA guideline
            'precedent' — prior approval, warning letter, CRL
            'literature' — PubMed, journal article
            'database'  — ChEMBL, DrugBank, OpenFDA, ClinicalTrials.gov
            'internal'  — company data, LIMS, Veeva (future)
            'computed'  — calculated by engine module
        source_id: Specific identifier (e.g., "ICH Q5E Section 2.2",
            "PMID:12345678", "OpenFDA:warning_letter:123").
        source_url: Direct URL if available.
        retrieval_timestamp: When the evidence was accessed.
        confidence: How reliable this source is for this context (0-1).
            1.0 = guideline directly applicable
            0.8 = strong precedent
            0.5 = literature support
            0.3 = computed/inferred
        module: Which engine module produced or used this evidence.
        context: Free-text explaining why this evidence is relevant.
    """
    source_type: str
    source_id: str
    module: str
    record_id: str = ""
    source_url: str = ""
    retrieval_timestamp: str = ""
    confidence: float = 0.5
    context: str = ""

    def __post_init__(self):
        if not self.record_id:
            self.record_id = str(uuid.uuid4())
        if not self.retrieval_timestamp:
            self.retrieval_timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "retrieval_timestamp": self.retrieval_timestamp,
            "confidence": self.confidence,
            "module": self.module,
            "context": self.context,
        }


@dataclass
class EvidenceResult:
    """Wraps an evidence-based assessment with its provenance chain.

    This is the standard output format for Layer 2 Evidence Services.
    Layer 1 consumes EvidenceResults, not raw data.

    Fields:
        result_id: Unique identifier.
        query: What was asked (e.g., "precedent for SEC monomer >2% delta").
        assessment: The evidence-based judgment.
        confidence: Overall confidence in this assessment (0-1).
        provenance: List of ProvenanceRecords supporting this assessment.
        metadata: Additional context (module version, parameters, etc.).
    """
    query: str
    assessment: str
    confidence: float
    provenance: List[ProvenanceRecord] = field(default_factory=list)
    result_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.result_id:
            self.result_id = str(uuid.uuid4())

    @property
    def n_sources(self) -> int:
        return len(self.provenance)

    @property
    def source_types(self) -> List[str]:
        return list(set(p.source_type for p in self.provenance))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result_id": self.result_id,
            "query": self.query,
            "assessment": self.assessment,
            "confidence": self.confidence,
            "n_sources": self.n_sources,
            "source_types": self.source_types,
            "provenance": [p.to_dict() for p in self.provenance],
            "metadata": self.metadata,
        }
