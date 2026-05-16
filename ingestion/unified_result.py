"""
A5: Unified Ingestion Result.

Extends the existing IngestionResult with document classification
and extracted evidence fields so that all document types can flow
through a single result type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ingestion.context_extractor import ExtractedCaseContext
from ingestion.document_classifier import DocTypeSpec
from ingestion.signal_detector import NarrativeSignal
from specs.cross_document_bridge import ExtractedAttribute


@dataclass
class UnifiedIngestionResult:
    """Complete result of the unified ingestion pipeline.

    Extends the original IngestionResult with:
    - document_classification: DocTypeSpec from the classifier
    - extracted_evidence: Dict with type-specific payload
    """
    # Core data for pipeline consumption
    attributes: List[ExtractedAttribute]
    case_context: ExtractedCaseContext
    signals: List[NarrativeSignal]

    # Pipeline-ready dict (matches run_comparability_assessment input)
    pipeline_input: Dict[str, Any] = field(default_factory=dict)

    # Phase 1 extensions
    document_classification: Optional[DocTypeSpec] = None
    extracted_evidence: Dict[str, Any] = field(default_factory=dict)

    # Diagnostics
    parsed_doc: Optional[Dict[str, Any]] = None
    issues: List[str] = field(default_factory=list)
    n_tables_found: int = 0
    n_attributes_extracted: int = 0
    n_signals_detected: int = 0
    source_format: str = "unknown"  # "docx", "pdf", etc.
