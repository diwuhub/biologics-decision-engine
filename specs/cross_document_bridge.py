"""
Cross-Document Intelligence Bridge -- Interface Specification (SP v5 P4).

This file defines INTERFACES ONLY. No implementation.
Phase 1 (current) uses structured CSV input.
Phase 2 (future) will implement these interfaces to support raw document input.

The bridge converts unstructured documents into the structured format that
Layer 1 (Decision Workflow) already consumes. Specifically, it produces the
``attributes`` list expected by ``pipelines.comparability.run_comparability_assessment``.

Architecture context:
    Layer 1  Decision Workflow   -- pipelines/comparability.py (exists)
    Layer 2  Evidence Services   -- services/regulatory_evidence.py (exists)
    Layer 3  Evidence Registry   -- evidence_registry/registry.py (exists)
    Layer 4  Connectors          -- THIS FILE defines the interface

Per SP v5 Section 7.1 and Guardrail #2:
    "Phase 1 = CSV.  Phase 2 (raw documents) = separate product generation."

Reference tables follow CTD Module 3.2.S / 3.2.P numbering (ICH M4Q).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

class DocumentType(Enum):
    """Recognized regulatory document types."""
    CTD_MODULE_3 = "CTD_MODULE_3"          # Quality (3.2.S / 3.2.P)
    COMPARABILITY_PROTOCOL = "COMP_PROTO"  # ICH Q5E comparability protocol
    ANALYTICAL_REPORT = "ANALYTICAL"       # Analytical method report
    STABILITY_REPORT = "STABILITY"         # ICH Q5C stability data
    BATCH_RECORD = "BATCH_RECORD"          # Batch manufacturing record
    CERTIFICATE_OF_ANALYSIS = "COA"        # CoA
    OTHER = "OTHER"


@dataclass
class ExtractedAttribute:
    """A quality attribute extracted from a document.

    This is the bridge type: document parsers produce these, and the
    pipeline adapter converts them into the ``attributes`` dicts that
    ``run_comparability_assessment`` consumes.
    """
    name: str                   # e.g. "SEC Purity (Main Peak)"
    value: float
    unit: str                   # e.g. "%", "mg/mL"
    source_document: str        # file path or identifier
    source_page: int            # 1-based page number
    source_table: str           # e.g. "Table 3.2.S.4.1-1"
    confidence: float           # extraction confidence 0.0-1.0
    context: str                # surrounding text for human review
    category: str = ""          # "physicochemical", "biological_activity", etc.
    lot_id: str = ""            # batch/lot identifier if available
    timepoint: str = ""         # stability timepoint if applicable
    metadata: Dict[str, Any] = field(default_factory=dict)
    # P7-B extensions (v4.3.1 Ingestion Contract)
    pre_value: Optional[float] = None
    post_value: Optional[float] = None
    anchor_ids: List[str] = field(default_factory=list)  # links to EvidenceAnchors
    extraction_confidence: float = 1.0  # 0-1, parsing quality (NOT decision confidence)
    n_lots: Optional[int] = None
    cv_pct: Optional[float] = None


@dataclass
class ReconciliationConflict:
    """A contradiction detected across documents for the same attribute."""
    attribute_name: str
    values: List[Dict[str, Any]]   # [{source, value, confidence}, ...]
    severity: str                  # "critical", "major", "minor"
    resolution: Optional[str] = None
    resolved_value: Optional[float] = None


@dataclass
class ReconciliationResult:
    """Output of cross-document reconciliation."""
    harmonized_attributes: List[ExtractedAttribute]
    conflicts: List[ReconciliationConflict]
    source_documents: List[str]
    n_total_extracted: int
    n_conflicts: int
    n_resolved: int


# ---------------------------------------------------------------------------
# Abstract interfaces
# ---------------------------------------------------------------------------

class DocumentParser(ABC):
    """Interface for parsing regulatory documents into structured data.

    Implementations will handle specific formats (PDF, DOCX, scanned images).
    The returned dict follows a common parsed-document schema so that
    downstream extractors are format-agnostic.

    Expected return schema::

        {
            "document_path": str,
            "document_type": DocumentType,
            "pages": [
                {
                    "page_number": int,
                    "text": str,
                    "tables": [
                        {"id": str, "headers": [...], "rows": [[...], ...]}
                    ],
                }
            ],
            "metadata": {...}
        }
    """

    @abstractmethod
    def parse(self, document_path: str) -> Dict[str, Any]:
        """Parse a document and return structured data."""

    @abstractmethod
    def supported_formats(self) -> List[str]:
        """Return list of supported file extensions, e.g. ['.pdf', '.docx']."""


class TableExtractor(ABC):
    """Interface for extracting tables from documents.

    Tables in CTD Module 3 carry the majority of comparability data
    (e.g., Table 3.2.S.4.1-1 for drug substance specifications).
    """

    @abstractmethod
    def extract_tables(self, document_path: str) -> List[Dict[str, Any]]:
        """Extract all tables from a document.

        Returns list of dicts, each with keys:
            id, page, headers, rows, caption (optional).
        """

    @abstractmethod
    def extract_tables_from_parsed(
        self, parsed_content: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Extract tables from already-parsed document content."""


class AttributeExtractor(ABC):
    """Interface for extracting quality attributes from parsed content.

    Maps raw table cells and text passages to typed ``ExtractedAttribute``
    objects with confidence scores.
    """

    @abstractmethod
    def extract_attributes(
        self, parsed_content: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Extract structured attributes from parsed document content."""

    @abstractmethod
    def supported_categories(self) -> List[str]:
        """Return attribute categories this extractor handles.

        e.g. ["physicochemical", "purity", "biological_activity", "potency"]
        """


class CrossDocumentReconciler(ABC):
    """Interface for reconciling data across multiple documents.

    When the same attribute appears in several documents (e.g., CoA vs.
    comparability report vs. stability report), this interface detects
    contradictions and produces a single harmonized attribute set.
    """

    @abstractmethod
    def reconcile(
        self,
        attribute_sets: List[List[ExtractedAttribute]],
    ) -> ReconciliationResult:
        """Reconcile attributes across multiple documents.

        Parameters
        ----------
        attribute_sets : list of list of ExtractedAttribute
            One inner list per source document.

        Returns
        -------
        ReconciliationResult
            Harmonized attributes with conflict metadata.
        """


class BridgeOrchestrator(ABC):
    """Top-level interface that composes parser + extractor + reconciler.

    This is the entry point that Layer 1 calls. Phase 1 uses CSVAdapter
    (a concrete implementation). Phase 2 implementations will wire up
    document parsers, table extractors, attribute extractors, and
    reconcilers behind this single interface.
    """

    @abstractmethod
    def ingest(
        self,
        sources: List[str],
        product_name: str = "",
        change_description: str = "",
    ) -> Dict[str, Any]:
        """Ingest one or more sources and return pipeline-ready input.

        The returned dict matches the schema expected by
        ``pipelines.comparability.run_comparability_assessment``::

            {
                "attributes": [
                    {
                        "name": str,
                        "category": str,
                        "pre_value": float,
                        "post_value": float,
                        "unit": str,
                        ...
                    },
                    ...
                ],
                "molecule_class": str,
                "modality": str,
            }

        Parameters
        ----------
        sources : list of str
            File paths (CSV for Phase 1, documents for Phase 2).
        product_name : str
            Product identifier.
        change_description : str
            Description of manufacturing change.
        """


# ---------------------------------------------------------------------------
# P7-A: Ingestion Contract schemas (v4.3.1)
# ---------------------------------------------------------------------------

@dataclass
class EvidenceAnchor:
    """A specific location in a source document that backs an extracted value."""
    anchor_id: str          # UUID-based
    document_id: str
    page: Optional[int]     # 1-based (PDF) or None (DOCX)
    section_title: str
    paragraph_index: Optional[int]
    table_index: Optional[int]
    table_row: Optional[int]
    table_col: Optional[int]
    snippet: str            # 1-3 sentence excerpt
    snippet_context: str    # surrounding text


@dataclass
class ExtractedCaseContext:
    """High-level case metadata extracted from source documents."""
    product_name: str
    molecule_class: str
    molecule_class_confidence: float
    change_type: str
    change_description: str
    source_anchors: List[str]  # anchor_ids
    extraction_notes: List[str]


@dataclass
class ExtractionIssue:
    """A problem detected during document extraction."""
    issue_id: str
    severity: str  # 'critical' | 'warning' | 'info'
    description: str
    affected_attribute: Optional[str]
    source_anchor_id: Optional[str]
    resolution_hint: str
    resolved: bool = False
    resolved_by: Optional[str] = None


@dataclass
class NarrativeSignal:
    """A quality signal detected in document narrative text."""
    signal_type: str  # 'oos' | 'bridging' | 'capa' | 'deviation' | 'trend' | 'specification_change'
    text: str
    anchor_id: str
    confidence: float
    affects_attributes: List[str]


@dataclass
class UserOverride:
    """A manual correction applied by a user to an extracted value (NEW in v4.3.1)."""
    override_id: str
    attribute_name: str
    field_name: str
    original_value: Any
    corrected_value: Any
    corrected_by: str
    reason: str
    source_anchor_ids: List[str]
    resolved_issue_id: Optional[str]
    timestamp: str


@dataclass
class IngestionResult:
    """Composite result of the full document ingestion process."""
    document_id: str
    source_filename: str
    case_context: ExtractedCaseContext
    attributes: List[ExtractedAttribute]
    anchors: List[EvidenceAnchor]
    issues: List[ExtractionIssue]
    narrative_signals: List[NarrativeSignal]
    user_overrides: List[UserOverride]
    extraction_timestamp: str
    parser_version: str
