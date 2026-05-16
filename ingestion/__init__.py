"""
P8-E: Ingestion Pipeline.

Complete document ingestion: parse -> classify -> dispatch -> extract -> context -> signals.
Produces an IngestionResult (or UnifiedIngestionResult) for the comparability pipeline.

Supports DOCX and PDF. Keyword heuristics only -- no LLM.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, IO, List, Optional, Union

from ingestion.docx_parser import DOCXDocumentParser
from ingestion.docx_extractor import DOCXAttributeExtractor, ExtractionIssue
from ingestion.context_extractor import CaseContextExtractor, ExtractedCaseContext
from ingestion.signal_detector import NarrativeSignalDetector, NarrativeSignal
from specs.cross_document_bridge import ExtractedAttribute

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    """Complete result of DOCX ingestion pipeline."""
    # Core data for pipeline consumption
    attributes: List[ExtractedAttribute]
    case_context: ExtractedCaseContext
    signals: List[NarrativeSignal]

    # Pipeline-ready dict (matches run_comparability_assessment input)
    pipeline_input: Dict[str, Any] = field(default_factory=dict)

    # Diagnostics
    parsed_doc: Optional[Dict[str, Any]] = None
    issues: List[str] = field(default_factory=list)
    n_tables_found: int = 0
    n_attributes_extracted: int = 0
    n_signals_detected: int = 0


def ingest_document(file_path_or_buffer: Union[str, IO]) -> "UnifiedIngestionResult":
    """Canonical entry point: auto-detect format -> parse -> classify -> dispatch -> extract -> return.

    Supports .docx and .pdf files (and file-like buffers).
    Never raises unhandled exceptions -- returns a result with issues on error.

    Parameters
    ----------
    file_path_or_buffer : str or file-like
        Path to a .docx or .pdf file, or a file-like object.

    Returns
    -------
    UnifiedIngestionResult
        Contains extracted attributes, case context, narrative signals,
        document classification, extracted evidence, and a pipeline_input dict.
    """
    from ingestion.document_classifier import DocumentClassifier
    from ingestion.dispatcher import IngestionDispatcher
    from ingestion.pdf_parser import PDFDocumentParser
    from ingestion.unified_result import UnifiedIngestionResult

    issues: List[str] = []
    temp_path = None

    try:
        # Determine file format
        if hasattr(file_path_or_buffer, "read"):
            import tempfile
            # Try to determine extension from name attribute
            name = getattr(file_path_or_buffer, "name", "")
            if name.lower().endswith(".pdf"):
                suffix = ".pdf"
            else:
                suffix = ".docx"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(file_path_or_buffer.read())
                temp_path = tmp.name
            doc_path = temp_path
        else:
            doc_path = str(file_path_or_buffer)

        ext = os.path.splitext(doc_path)[1].lower()

        # Step 1: Parse
        if ext == ".pdf":
            parser = PDFDocumentParser()
            source_format = "pdf"
        elif ext in (".docx", ".doc"):
            parser = DOCXDocumentParser()
            source_format = "docx"
        else:
            issues.append(f"Unsupported file format: {ext}. Attempting DOCX parse.")
            parser = DOCXDocumentParser()
            source_format = "unknown"

        try:
            parsed_doc = parser.parse(doc_path)
        except Exception as e:
            issues.append(f"Parse error: {e}")
            # Return empty result -- INV-001: no upload raises unhandled exception
            empty_ctx = ExtractedCaseContext()
            from ingestion.document_classifier import DocTypeSpec
            return UnifiedIngestionResult(
                attributes=[],
                case_context=empty_ctx,
                signals=[],
                pipeline_input={"attributes": [], "molecule_class": "mAb", "modality": "IV"},
                document_classification=DocTypeSpec("UNKNOWN", 0.0, [f"Parse failed: {e}"]),
                extracted_evidence={},
                parsed_doc=None,
                issues=issues,
                source_format=source_format,
            )

        n_tables = sum(
            len(page.get("tables", []))
            for page in parsed_doc.get("pages", [])
        )

        # Step 2: Classify
        classifier = DocumentClassifier()
        doc_type = classifier.classify(parsed_doc)

        # Step 3: Dispatch to extractor
        dispatcher = IngestionDispatcher()
        extractor = dispatcher.dispatch(parsed_doc, doc_type)

        # Step 4: Extract attributes (never raises)
        attributes = extractor.extract_attributes(parsed_doc)

        # Step 5: Extract evidence (never raises)
        evidence = extractor.extract_evidence(parsed_doc)

        # Step 6: Extract case context
        ctx_extractor = CaseContextExtractor()
        case_context = ctx_extractor.extract_context(parsed_doc)

        # Step 7: Detect narrative signals
        signal_detector = NarrativeSignalDetector()
        signals = signal_detector.detect_signals(parsed_doc)

        # Step 8: Build pipeline-ready input (same logic as ingest_docx)
        pipeline_attrs = _build_pipeline_attrs(attributes, issues)

        pipeline_input = {
            "attributes": pipeline_attrs,
            "molecule_class": case_context.molecule_class if case_context.molecule_class != "unknown" else "mAb",
            "modality": "IV",
            "product_name": case_context.product_name,
            "change_description": case_context.change_description,
        }

        return UnifiedIngestionResult(
            attributes=attributes,
            case_context=case_context,
            signals=signals,
            pipeline_input=pipeline_input,
            document_classification=doc_type,
            extracted_evidence=evidence,
            parsed_doc=parsed_doc,
            issues=issues,
            n_tables_found=n_tables,
            n_attributes_extracted=len(attributes),
            n_signals_detected=len(signals),
            source_format=source_format,
        )

    except Exception as e:
        # INV-001: No document upload raises unhandled exception
        logger.error("ingest_document failed: %s", e)
        issues.append(f"Unexpected error: {e}")
        empty_ctx = ExtractedCaseContext()
        from ingestion.document_classifier import DocTypeSpec
        return UnifiedIngestionResult(
            attributes=[],
            case_context=empty_ctx,
            signals=[],
            pipeline_input={"attributes": [], "molecule_class": "mAb", "modality": "IV"},
            document_classification=DocTypeSpec("UNKNOWN", 0.0, [f"Error: {e}"]),
            extracted_evidence={},
            parsed_doc=None,
            issues=issues,
            source_format="unknown",
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _build_pipeline_attrs(
    attributes: List[ExtractedAttribute], issues: List[str]
) -> List[Dict[str, Any]]:
    """Convert ExtractedAttributes to pipeline-ready dicts."""
    pipeline_attrs = []
    for attr in attributes:
        pipeline_attr: Dict[str, Any] = {
            "name": attr.name,
            "category": attr.category or "physicochemical",
            "unit": attr.unit,
        }
        if attr.pre_value is not None:
            pipeline_attr["pre_value"] = attr.pre_value
        if attr.post_value is not None:
            pipeline_attr["post_value"] = attr.post_value
        # If only value is set (no pre/post), skip
        if "pre_value" not in pipeline_attr or "post_value" not in pipeline_attr:
            if attr.value is not None and attr.value != 0.0:
                issues.append(
                    f"Attribute '{attr.name}' has value but missing pre/post split"
                )
            continue

        if attr.n_lots is not None:
            pipeline_attr["n_lots"] = attr.n_lots
        if attr.cv_pct is not None:
            pipeline_attr["cv_pct"] = attr.cv_pct

        # Pass spec limits from extraction metadata
        if hasattr(attr, 'metadata') and attr.metadata:
            if attr.metadata.get('spec_value'):
                if attr.metadata.get('spec_lower') is not None:
                    pipeline_attr['spec_lower'] = attr.metadata['spec_lower']
                if attr.metadata.get('spec_upper') is not None:
                    pipeline_attr['spec_upper'] = attr.metadata['spec_upper']
                pipeline_attr['spec_source'] = 'product_spec'

        pipeline_attrs.append(pipeline_attr)
    return pipeline_attrs


def ingest_docx(file_path_or_buffer: Union[str, IO]) -> IngestionResult:
    """Complete DOCX ingestion pipeline.

    Parameters
    ----------
    file_path_or_buffer : str or file-like
        Path to a .docx file, or a file-like object (e.g., from Streamlit upload).

    Returns
    -------
    IngestionResult
        Contains extracted attributes, case context, narrative signals,
        and a pipeline_input dict ready for run_comparability_assessment.
    """
    # Handle file-like objects by writing to temp file
    temp_path = None
    if hasattr(file_path_or_buffer, "read"):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(file_path_or_buffer.read())
            temp_path = tmp.name
        doc_path = temp_path
    else:
        doc_path = str(file_path_or_buffer)

    issues: List[str] = []

    try:
        # Step 1: Parse DOCX
        parser = DOCXDocumentParser()
        parsed_doc = parser.parse(doc_path)

        n_tables = sum(
            len(page.get("tables", []))
            for page in parsed_doc.get("pages", [])
        )

        # Step 2: Extract attributes
        extractor = DOCXAttributeExtractor()
        attributes: List[ExtractedAttribute] = []
        try:
            attributes = extractor.extract_attributes(parsed_doc)
        except ExtractionIssue as e:
            issues.append(f"Extraction issue ({e.severity}): {str(e)}")

        # Step 3: Extract case context
        ctx_extractor = CaseContextExtractor()
        case_context = ctx_extractor.extract_context(parsed_doc)

        # Step 4: Detect narrative signals
        signal_detector = NarrativeSignalDetector()
        signals = signal_detector.detect_signals(parsed_doc)

        # Step 5: Build pipeline-ready input
        pipeline_attrs = []
        for attr in attributes:
            pipeline_attr: Dict[str, Any] = {
                "name": attr.name,
                "category": attr.category or "physicochemical",
                "unit": attr.unit,
            }
            if attr.pre_value is not None:
                pipeline_attr["pre_value"] = attr.pre_value
            if attr.post_value is not None:
                pipeline_attr["post_value"] = attr.post_value
            # If only value is set (no pre/post), skip
            if "pre_value" not in pipeline_attr or "post_value" not in pipeline_attr:
                if attr.value is not None and attr.value != 0.0:
                    issues.append(
                        f"Attribute '{attr.name}' has value but missing pre/post split"
                    )
                continue

            if attr.n_lots is not None:
                pipeline_attr["n_lots"] = attr.n_lots
            if attr.cv_pct is not None:
                pipeline_attr["cv_pct"] = attr.cv_pct

            # W1-2: Pass spec limits from DOCX extraction metadata
            if hasattr(attr, 'metadata') and attr.metadata:
                if attr.metadata.get('spec_value'):
                    if attr.metadata.get('spec_lower') is not None:
                        pipeline_attr['spec_lower'] = attr.metadata['spec_lower']
                    if attr.metadata.get('spec_upper') is not None:
                        pipeline_attr['spec_upper'] = attr.metadata['spec_upper']
                    pipeline_attr['spec_source'] = 'product_spec'

            pipeline_attrs.append(pipeline_attr)

        pipeline_input = {
            "attributes": pipeline_attrs,
            "molecule_class": case_context.molecule_class if case_context.molecule_class != "unknown" else "mAb",
            "modality": "IV",
            "product_name": case_context.product_name,
            "change_description": case_context.change_description,
        }

        return IngestionResult(
            attributes=attributes,
            case_context=case_context,
            signals=signals,
            pipeline_input=pipeline_input,
            parsed_doc=parsed_doc,
            issues=issues,
            n_tables_found=n_tables,
            n_attributes_extracted=len(attributes),
            n_signals_detected=len(signals),
        )

    finally:
        # Clean up temp file if created
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
