"""
PackageAssessor — aggregates per-document judgments into a package-level verdict.

Implements P3 of the iteration plan: multi-document assessment with
cross-document consistency checking.

Workflow:
1. Each document ingested independently via ingest_document()
2. Type-specific assessor runs for each (CharacterizationAssessor, etc.)
3. Cross-document checker finds conflicts
4. Package-level policy produces overall verdict
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DocumentEntry:
    """One document in a package."""
    filename: str
    doc_type: str
    classification_confidence: float
    ingestion_result: Any  # UnifiedIngestionResult
    assessment: Optional[Dict[str, Any]] = None  # assessor output
    error: Optional[str] = None


@dataclass
class PackageCase:
    """A multi-document assessment package."""
    package_id: str = ""
    documents: List[DocumentEntry] = field(default_factory=list)
    cross_document_flags: List[Any] = field(default_factory=list)
    package_verdict: str = ""
    package_confidence: float = 0.0
    package_rationale: str = ""
    document_coverage: Dict[str, bool] = field(default_factory=dict)
    missing_types: List[str] = field(default_factory=list)
    reviewer_questions: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not self.package_id:
            self.package_id = f"PKG-{uuid.uuid4().hex[:8].upper()}"


# ---------------------------------------------------------------------------
# Required document types for a complete CMC package
# ---------------------------------------------------------------------------

_EXPECTED_TYPES = {
    "CHARACTERIZATION": "Physicochemical and biological characterization per ICH Q6B",
    "STABILITY": "Stability data per ICH Q1A/Q5C",
    "ANALYTICAL_METHOD": "Analytical method validation per ICH Q2(R2)",
}

# Per-document verdict severity ordering
_VERDICT_SEVERITY = {
    "proceed": 0,
    "proceed_with_conditions": 1,
    "supplement_required": 2,
    "investigation_required": 3,
    "defer_package": 4,
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def assess_package(
    ingestion_results: List[Any],
    filenames: List[str],
) -> PackageCase:
    """Run full package assessment.

    Args:
        ingestion_results: List of UnifiedIngestionResult from ingest_document().
        filenames: Corresponding filenames.

    Returns:
        PackageCase with per-document assessments, cross-doc flags, and package verdict.
    """
    package = PackageCase()

    # Step 1: Classify and assess each document
    for result, filename in zip(ingestion_results, filenames):
        entry = _assess_single_document(result, filename)
        package.documents.append(entry)

    # Step 2: Check document coverage
    types_present = {d.doc_type for d in package.documents if d.error is None}
    for expected_type, description in _EXPECTED_TYPES.items():
        package.document_coverage[expected_type] = expected_type in types_present
    package.missing_types = [t for t, present in package.document_coverage.items() if not present]

    # Step 3: Cross-document consistency check
    valid_results = [d.ingestion_result for d in package.documents if d.error is None]
    if len(valid_results) >= 2:
        try:
            from services.cross_document_checker import check_cross_document_consistency
            package.cross_document_flags = check_cross_document_consistency(valid_results)
        except Exception as e:
            logger.error("Cross-document check failed: %s", e)

    # Step 4: Package-level policy
    _apply_package_policy(package)

    # Step 5: Aggregate reviewer questions
    _aggregate_reviewer_questions(package)

    return package


# ---------------------------------------------------------------------------
# Per-document assessment
# ---------------------------------------------------------------------------

def _assess_single_document(result: Any, filename: str) -> DocumentEntry:
    """Classify and assess one document."""
    cls = getattr(result, "document_classification", None)
    doc_type = cls.document_type if cls else "UNKNOWN"
    confidence = cls.confidence if cls else 0.0
    evidence = result.extracted_evidence if hasattr(result, "extracted_evidence") else {}

    entry = DocumentEntry(
        filename=filename,
        doc_type=doc_type,
        classification_confidence=confidence,
        ingestion_result=result,
    )

    try:
        if doc_type == "CHARACTERIZATION":
            from services.characterization_assessor import assess_characterization
            entry.assessment = assess_characterization(evidence)
        elif doc_type == "STABILITY":
            from services.stability_assessor import assess_stability
            entry.assessment = assess_stability(evidence)
        elif doc_type == "ANALYTICAL_METHOD":
            from services.analytical_method_assessor import assess_analytical_method
            entry.assessment = assess_analytical_method(evidence)
        elif doc_type == "COMPARABILITY":
            # Comparability uses the existing pipeline, not an assessor
            entry.assessment = {
                "analytical_conclusion": "Requires CSV Data",
                "package_posture": "Review Required",
                "judgment": {"confidence": 0.5, "confidence_band": "moderate"},
                "posture_rationale": "Comparability assessment requires structured pre/post data.",
            }
        else:
            entry.assessment = {
                "analytical_conclusion": "Unclassified",
                "package_posture": "Review Required",
                "judgment": {"confidence": 0.3, "confidence_band": "low"},
                "posture_rationale": "Document type could not be determined.",
            }
    except Exception as e:
        entry.error = str(e)
        logger.error("Assessment failed for %s: %s", filename, e)

    return entry


# ---------------------------------------------------------------------------
# Package-level policy (PKG-001 through PKG-010)
# ---------------------------------------------------------------------------

def _apply_package_policy(package: PackageCase) -> None:
    """Apply package-level policy rules to determine overall verdict."""
    assessed = [d for d in package.documents if d.assessment and d.error is None]
    failed = [d for d in package.documents if d.error is not None]
    rules_applied = []

    if not assessed:
        package.package_verdict = "NO_DOCUMENTS"
        package.package_confidence = 0.0
        package.package_rationale = "No documents were successfully assessed."
        return

    # Collect per-document verdict severities
    verdicts = []
    for d in assessed:
        v = d.assessment.get("judgment", {}).get("package_verdict", "proceed_with_conditions")
        verdicts.append((d.doc_type, v, d.assessment.get("judgment", {}).get("confidence", 0.5)))

    # PKG-001: Missing required document types
    if package.missing_types:
        rules_applied.append("PKG-001")

    # PKG-005: Any individual verdict is "defer" or "investigation_required"
    worst_verdict = max(verdicts, key=lambda x: _VERDICT_SEVERITY.get(x[1], 0))
    worst_severity = _VERDICT_SEVERITY.get(worst_verdict[1], 0)

    if worst_severity >= 4:
        package.package_verdict = "PACKAGE_NOT_READY"
        rules_applied.append("PKG-005")
    elif worst_severity >= 3:
        package.package_verdict = "PACKAGE_NOT_READY"
        rules_applied.append("PKG-005")
    elif worst_severity >= 2:
        package.package_verdict = "PACKAGE_NEEDS_SUPPLEMENT"
        rules_applied.append("PKG-006")
    elif package.missing_types:
        package.package_verdict = "PACKAGE_INCOMPLETE"
        rules_applied.append("PKG-001")
    elif worst_severity >= 1:
        package.package_verdict = "PACKAGE_NEEDS_SUPPLEMENT"
        rules_applied.append("PKG-006")
    else:
        package.package_verdict = "PACKAGE_READY"
        rules_applied.append("PKG-004")

    # PKG-002/003: Cross-document conflicts
    critical_flags = [f for f in package.cross_document_flags if getattr(f, "severity", "") == "critical"]
    if critical_flags:
        if package.package_verdict == "PACKAGE_READY":
            package.package_verdict = "PACKAGE_NEEDS_SUPPLEMENT"
        rules_applied.append("PKG-002")

    # PKG-010: Low classification confidence across all docs
    avg_conf = sum(d.classification_confidence for d in assessed) / len(assessed)
    if avg_conf < 0.6:
        rules_applied.append("PKG-010")

    # Failed documents
    if failed:
        if package.package_verdict == "PACKAGE_READY":
            package.package_verdict = "PACKAGE_NEEDS_SUPPLEMENT"

    # Confidence: weighted average of per-document confidence
    confidences = [d.assessment.get("judgment", {}).get("confidence", 0.5) for d in assessed]
    package.package_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    # Penalize for missing types and cross-doc conflicts
    if package.missing_types:
        package.package_confidence *= 0.8
    if critical_flags:
        package.package_confidence *= 0.7

    # Build rationale
    parts = []
    n_docs = len(assessed)
    types_found = sorted({d.doc_type for d in assessed})
    parts.append(f"{n_docs} document(s) assessed: {', '.join(types_found)}.")

    if package.missing_types:
        parts.append(f"Missing: {', '.join(package.missing_types)}.")

    if critical_flags:
        parts.append(f"{len(critical_flags)} cross-document conflict(s) found.")

    if failed:
        parts.append(f"{len(failed)} document(s) failed to process.")

    verdict_display = package.package_verdict.replace("_", " ").title()
    parts.insert(0, f"Package verdict: {verdict_display}.")

    package.package_rationale = " ".join(parts)


def _aggregate_reviewer_questions(package: PackageCase) -> None:
    """Collect reviewer questions from all documents + package-level."""
    questions = []

    # Per-document questions
    for d in package.documents:
        if d.assessment:
            doc_qs = d.assessment.get("reviewer_risk", {}).get("predicted_questions", [])
            for q in doc_qs:
                q_copy = dict(q)
                q_copy["source_document"] = d.filename
                q_copy["source_doc_type"] = d.doc_type
                questions.append(q_copy)

    # Package-level questions
    if package.missing_types:
        for mt in package.missing_types:
            questions.append({
                "question": f"No {mt.replace('_', ' ').lower()} document provided. "
                            f"Reviewer will ask why this data is absent from the submission.",
                "confidence": "high",
                "source": "package_policy",
                "source_document": "Package",
                "source_doc_type": "PKG",
                "affected_attributes": [],
                "primary": True,
            })

    for flag in package.cross_document_flags:
        desc = getattr(flag, "description", str(flag))
        questions.append({
            "question": f"Cross-document conflict: {desc}",
            "confidence": "high",
            "source": "cross_document_checker",
            "source_document": "Package",
            "source_doc_type": "PKG",
            "affected_attributes": [],
            "primary": False,
        })

    package.reviewer_questions = questions


# ---------------------------------------------------------------------------
# Overview builder (for UI)
# ---------------------------------------------------------------------------

def build_package_overview(package: PackageCase) -> Dict[str, Any]:
    """Build UI-compatible overview for the package assessment."""
    _VERDICT_DISPLAY = {
        "PACKAGE_READY": ("Package Ready", "#10b981"),
        "PACKAGE_NEEDS_SUPPLEMENT": ("Needs Supplement", "#f59e0b"),
        "PACKAGE_INCOMPLETE": ("Incomplete", "#f59e0b"),
        "PACKAGE_NOT_READY": ("Not Ready", "#f43f5e"),
        "NO_DOCUMENTS": ("No Documents", "#64748b"),
    }
    display_label, display_color = _VERDICT_DISPLAY.get(
        package.package_verdict, ("Unknown", "#64748b")
    )

    doc_summaries = []
    for d in package.documents:
        ac = d.assessment.get("analytical_conclusion", "N/A") if d.assessment else "Error"
        pp = d.assessment.get("package_posture", "N/A") if d.assessment else "Error"
        conf = d.assessment.get("judgment", {}).get("confidence", 0) if d.assessment else 0
        doc_summaries.append({
            "filename": d.filename,
            "doc_type": d.doc_type,
            "classification_confidence": d.classification_confidence,
            "analytical_conclusion": ac,
            "package_posture": pp,
            "confidence": conf,
            "error": d.error,
        })

    return {
        "package_id": package.package_id,
        "package_verdict": package.package_verdict,
        "package_verdict_display": display_label,
        "package_verdict_color": display_color,
        "package_confidence": package.package_confidence,
        "package_rationale": package.package_rationale,
        "document_summaries": doc_summaries,
        "document_coverage": package.document_coverage,
        "missing_types": package.missing_types,
        "cross_document_flags": [
            {
                "flag_id": getattr(f, "flag_id", ""),
                "severity": getattr(f, "severity", "info"),
                "description": getattr(f, "description", str(f)),
                "document_a": getattr(f, "document_a", ""),
                "document_b": getattr(f, "document_b", ""),
                "attribute": getattr(f, "attribute", ""),
            }
            for f in package.cross_document_flags
        ],
        "reviewer_questions": package.reviewer_questions,
        "n_documents": len(package.documents),
        "n_failed": sum(1 for d in package.documents if d.error),
    }
