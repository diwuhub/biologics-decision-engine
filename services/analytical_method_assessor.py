"""
AnalyticalMethodAssessor — maps AnalyticalMethodEvidence to the
cluster → policy → PackageDecision judgment chain.

Scoring semantics are COMPLIANCE-based: does the validation data
satisfy ICH Q2(R2) requirements for the 9 validation study types?

P2: Analytical method judgment vertical slice.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from schemas.case_context import CaseContext
from schemas.package_decision import PackageDecision, compute_confidence_band
from services.cluster_builder import build_risk_clusters
from services.cluster_matcher import match_for_clusters
from services.judgment_policy import apply_cluster_policy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ICH Q2(R2) validation studies and criticality
# ---------------------------------------------------------------------------
_CRITICAL_STUDIES = {"Specificity", "Accuracy", "Precision (Repeatability)"}
_IMPORTANT_STUDIES = {"Linearity", "Range", "Precision (Intermediate)"}
_IMPORTANT_LOD_LOQ = {"LOD (Limit of Detection)", "LOQ (Limit of Quantitation)"}
_OPTIONAL_STUDIES = {"Robustness"}

_IMPORTANT_STUDIES = _IMPORTANT_STUDIES | _IMPORTANT_LOD_LOQ
_ALL_STUDIES = _CRITICAL_STUDIES | _IMPORTANT_STUDIES | _OPTIONAL_STUDIES

# Map study groups to cluster categories
_STUDY_CATEGORIES = {
    "accuracy_precision": {"Accuracy", "Precision (Repeatability)", "Precision (Intermediate)"},
    "sensitivity": {"LOD (Limit of Detection)", "LOQ (Limit of Quantitation)"},
    "specificity": {"Specificity"},
    "robustness": {"Robustness"},
    "linearity_range": {"Linearity", "Range"},
}


# ---------------------------------------------------------------------------
# Main assessor
# ---------------------------------------------------------------------------

def assess_analytical_method(
    evidence: Dict[str, Any],
    molecule_class: str = "mAb",
) -> Dict[str, Any]:
    """Run analytical method judgment through the cluster → policy chain.

    Returns overview_data dict compatible with UI consumption.
    """
    attribute_results = _map_evidence_to_attribute_results(evidence)
    case_context = _build_case_context(evidence, molecule_class)

    clusters = build_risk_clusters(case_context, attribute_results)

    from schemas.authority_context_pack import AuthorityContextPack
    try:
        from evidence_registry.registry import EvidenceRegistry
        registry = EvidenceRegistry()
        cluster_packs, _ = match_for_clusters(case_context, clusters, registry)
    except Exception:
        cluster_packs = [AuthorityContextPack(cluster_id=c.cluster_id) for c in clusters]

    for i, (cluster, pack) in enumerate(zip(clusters, cluster_packs)):
        clusters[i] = apply_cluster_policy(cluster, pack)

    decision = _build_decision(case_context, clusters, evidence)
    return _build_overview(evidence, decision, clusters, attribute_results)


# ---------------------------------------------------------------------------
# Evidence → attribute_results mapping
# ---------------------------------------------------------------------------

def _map_evidence_to_attribute_results(
    evidence: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Convert AnalyticalMethodEvidence to cluster_builder-compatible dicts."""
    results = []
    found = set(evidence.get("validation_studies_found", []))
    missing = set(evidence.get("validation_studies_missing", []))

    for category, studies in _STUDY_CATEGORIES.items():
        studies_found = studies & found
        studies_missing = studies & missing
        n_total = len(studies)
        n_found = len(studies_found)

        score = n_found / n_total if n_total > 0 else 0.5

        # Criticality-based concern
        has_critical_missing = bool(studies_missing & _CRITICAL_STUDIES)
        has_important_missing = bool(studies_missing & _IMPORTANT_STUDIES)

        if has_critical_missing:
            concern = "major"
        elif has_important_missing:
            # Both LOD and LOQ missing = major gap for impurity testing
            concern = "major" if len(studies_missing & _IMPORTANT_LOD_LOQ) >= 2 else "minor"
        elif studies_missing:
            concern = "none"
        else:
            concern = "none"

        is_cqa = bool(studies & _CRITICAL_STUDIES)
        # Only critical missing studies create gap entries (to avoid assay_gap escalation)
        critical_missing = studies_missing & _CRITICAL_STUDIES
        gaps = [f"missing_{s.lower().replace(' ', '_')}" for s in critical_missing]

        results.append({
            "attribute_id": f"anal_{category}",
            "category": category,
            "score": score,
            "concern_level": concern,
            "is_cqa": is_cqa,
            "gaps": gaps,
        })

    # Quantitative depth attributes
    completeness = evidence.get("completeness_score", 0)
    results.append({
        "attribute_id": "anal_completeness",
        "category": "method_completeness",
        "score": completeness,
        "concern_level": "none" if completeness >= 0.8 else ("minor" if completeness >= 0.5 else "major"),
        "is_cqa": False,
        "gaps": [],
    })

    return results


def _build_case_context(evidence: Dict[str, Any], molecule_class: str) -> CaseContext:
    return CaseContext(
        molecule_class=molecule_class,
        change_type="analytical_method_assessment",
        change_description="ICH Q2(R2) analytical method validation assessment",
        lifecycle_stage="CMC",
        flagged_attribute_ids=[],
        flagged_categories=[],
        identified_gaps=evidence.get("critical_gaps", []),
    )


def _build_decision(
    case_context: CaseContext,
    clusters: list,
    evidence: Dict[str, Any],
) -> PackageDecision:
    def _concern(c):
        return getattr(c, "concern_level", None) or getattr(c, "base_concern_level", "none") or "none"

    blocking = [c for c in clusters if getattr(c, "package_blocking", False)]
    n_critical = sum(1 for c in clusters if _concern(c) == "critical")
    n_major = sum(1 for c in clusters if _concern(c) == "major")

    if blocking or n_critical > 0:
        verdict = "investigation_required"
    elif n_major >= 2:
        verdict = "supplement_required"
    elif n_major >= 1:
        verdict = "proceed_with_conditions"
    else:
        verdict = "proceed"

    scores = [getattr(c, "base_cluster_score", 0.5) or 0.5 for c in clusters]
    confidence = sum(scores) / len(scores) if scores else 0.5

    return PackageDecision(
        case_id=case_context.case_id,
        package_verdict=verdict,
        confidence=confidence,
        confidence_band=compute_confidence_band(confidence),
        blocking_cluster_ids=[c.cluster_id for c in blocking],
    )


# ---------------------------------------------------------------------------
# Verdict mapping
# ---------------------------------------------------------------------------

_VERDICT_TO_ANAL = {
    "proceed": ("Validated", "Complete"),
    "proceed_with_conditions": ("Partially Validated", "Needs Review"),
    "supplement_required": ("Partial", "Needs Studies"),
    "investigation_required": ("Not Validated", "Incomplete"),
    "defer_package": ("Insufficient", "Incomplete"),
}


def _build_overview(
    evidence: Dict[str, Any],
    decision: PackageDecision,
    clusters: list,
    attribute_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    ac, pp = _VERDICT_TO_ANAL.get(decision.package_verdict, ("Assessed", "Review Required"))

    found = evidence.get("validation_studies_found", [])
    missing = evidence.get("validation_studies_missing", [])
    completeness = evidence.get("completeness_score", 0)

    # Detect if document is a guideline (not a validation report)
    is_guideline = evidence.get("method_name") is None and completeness > 0.5
    guideline_note = " (Note: this appears to be a guideline document, not a validation report.)" if is_guideline else ""

    if ac == "Validated":
        rationale = f"Validation complete: {len(found)}/{len(found)+len(missing)} ICH Q2(R2) studies found.{guideline_note}"
    else:
        rationale = (
            f"Validation gaps: {len(missing)} studies missing ({', '.join(missing[:3])}). "
            f"Completeness: {completeness:.0%}.{guideline_note}"
        )

    blocking_clusters = [
        {
            "cluster_id": c.cluster_id,
            "dominant_category": getattr(c, "dominant_category", ""),
            "concern_level": getattr(c, "concern_level", None) or getattr(c, "base_concern_level", "none"),
            "affected_attribute_ids": getattr(c, "affected_attribute_ids", []),
            "cluster_reason_summary": getattr(c, "cluster_reason_summary", ""),
            "package_blocking": getattr(c, "package_blocking", False),
        }
        for c in clusters
        if getattr(c, "package_blocking", False) or (
            getattr(c, "concern_level", None) or getattr(c, "base_concern_level", "none")
        ) == "critical"
    ]

    reviewer_concerns = evidence.get("reviewer_concerns", [])
    predicted_questions = [
        {"question": q, "confidence": "moderate", "source": "judgment_engine",
         "affected_attributes": [], "primary": i == 0}
        for i, q in enumerate(reviewer_concerns)
    ]

    return {
        "analytical_conclusion": ac,
        "package_posture": pp,
        "posture_rationale": rationale,
        "confidence_breakdown": {
            "analytical_confidence": decision.confidence,
            "package_readiness": completeness,
            "evidence_completeness": completeness,
        },
        "judgment": {
            "package_verdict": decision.package_verdict,
            "confidence": decision.confidence,
            "confidence_band": decision.confidence_band,
            "abstain_flag": False,
            "key_finding": rationale,
            "decision_rule_ids": [],
        },
        "judgment_summary": {
            "analytical_conclusion": ac,
            "package_posture": pp,
            "posture_rationale": rationale,
        },
        "blocking_clusters": blocking_clusters,
        "counterfactuals": [],
        "reviewer_risk": {"predicted_questions": predicted_questions},
        "critical_attributes": [
            {
                "name": ar["attribute_id"].replace("anal_", "").replace("_", " ").title(),
                "score": ar["score"],
                "concern": ar["concern_level"],
                "is_cqa": ar["is_cqa"],
                "category": ar["category"],
                "action": "Review" if ar["concern_level"] in ("major", "critical") else "Accept",
            }
            for ar in attribute_results if ar["concern_level"] != "none"
        ],
        "extracted_evidence": evidence,
        "document_type": "ANALYTICAL_METHOD",
    }
