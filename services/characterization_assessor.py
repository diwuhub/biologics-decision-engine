"""
CharacterizationAssessor — maps CharacterizationEvidence to the
cluster → policy → PackageDecision judgment chain.

Scoring semantics are COMPLETENESS-based (not comparison-based like
comparability). Each Q6B section and CQA field becomes an attribute_result
that feeds into build_risk_clusters.

P1: Characterization judgment vertical slice.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from schemas.case_context import CaseContext
from schemas.package_decision import PackageDecision, compute_confidence_band
from services.cluster_builder import build_risk_clusters
from services.cluster_matcher import match_for_clusters
from services.judgment_policy import apply_cluster_policy, apply_package_policy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Q6B Section → Analytical Category mapping
# ---------------------------------------------------------------------------
_SECTION_CATEGORY_MAP = {
    "Primary Structure": "identity",
    "Higher-Order Structure": "higher_order_structure",
    "Aggregation / Size Variants": "aggregation",
    "Charge Heterogeneity": "charge_variants",
    "Glycosylation / PTMs": "glycosylation",
    "Biological Activity / Potency": "potency",
    "Immunochemical Properties": "immunochemical",
    "Purity / Impurities": "purity",
}

_CQA_SECTIONS = {"potency", "aggregation", "glycosylation", "charge_variants", "purity"}

# ---------------------------------------------------------------------------
# CQA spec ranges (ICH Q6B-informed defaults for mAb)
# ---------------------------------------------------------------------------
_CQA_SPECS = {
    "hmw_pct": {"acceptable": (0, 5.0), "marginal": (5.0, 10.0)},
    "main_charge_peak_pct": {"acceptable": (50.0, 100.0), "marginal": (40.0, 50.0)},
    "potency_relative_pct": {"acceptable": (80.0, 120.0), "marginal": (70.0, 130.0)},
    "afucosylation_pct": None,  # informational — no hard spec
    "acidic_variants_pct": None,
    "basic_variants_pct": None,
}


def _score_cqa_value(
    field_name: str,
    state: str,
    value: Optional[float],
    is_cqa: bool,
) -> tuple:
    """Score a CQA field based on three-state model + spec compliance.

    Returns (score, concern_level, gaps_list).
    """
    if state == "present" and value is not None:
        spec = _CQA_SPECS.get(field_name)
        if spec is not None:
            lo, hi = spec["acceptable"]
            if lo <= value <= hi:
                return 0.95, "none", []
            lo_m, hi_m = spec["marginal"]
            if lo_m <= value <= hi_m:
                return 0.6, "minor", [f"{field_name}_marginal"]
            return 0.3, "major" if is_cqa else "minor", [f"{field_name}_out_of_spec"]
        # No spec defined — present is good
        return 0.85, "none", []

    if state == "uncertain":
        # UNCERTAIN = data may exist but wasn't extracted — less severe than absent
        # Use empty gaps to avoid triggering assay_gap risk_semantics
        concern = "minor" if is_cqa else "none"
        return 0.5, concern, []

    # confirmed_absent = data genuinely not in document
    concern = "critical" if is_cqa else "major"
    return 0.05, concern, [f"{field_name}_missing_data"]


# ---------------------------------------------------------------------------
# Main assessor
# ---------------------------------------------------------------------------

def assess_characterization(
    evidence: Dict[str, Any],
    molecule_class: str = "mAb",
) -> Dict[str, Any]:
    """Run full characterization judgment through the cluster → policy → decision chain.

    Args:
        evidence: CharacterizationEvidence as dict (from extract_evidence).
        molecule_class: Product type for CQA designation.

    Returns:
        Dict with verdict, confidence, clusters, reviewer concerns, etc.
        Compatible with overview_data format for UI consumption.
    """
    attribute_results = _map_evidence_to_attribute_results(evidence, molecule_class)
    case_context = _build_case_context(evidence, molecule_class)

    # Step 1: Build risk clusters
    clusters = build_risk_clusters(case_context, attribute_results)

    # Step 2: Match clusters to authority evidence
    from schemas.authority_context_pack import AuthorityContextPack
    try:
        from evidence_registry.registry import EvidenceRegistry
        registry = EvidenceRegistry()
        cluster_packs, case_pack = match_for_clusters(
            case_context, clusters, registry,
        )
    except Exception as e:
        logger.debug("Cluster matching failed (authority evidence sparse): %s", e)
        cluster_packs = [AuthorityContextPack(cluster_id=c.cluster_id) for c in clusters]
        case_pack = AuthorityContextPack(cluster_id="case_level")

    # Step 3: Apply cluster-level policy (CHAR-* rules)
    for i, (cluster, pack) in enumerate(zip(clusters, cluster_packs)):
        clusters[i] = apply_cluster_policy(cluster, pack)

    # Step 4: Build characterization-specific decision
    # Note: we intentionally skip apply_package_policy here because its
    # AGGR/GUARD rules are tuned for comparability verdicts and produce
    # over-escalated results for completeness-based characterization.
    decision = _build_preliminary_decision(case_context, clusters)

    # Step 5: Map to characterization-specific display
    return _build_overview(evidence, decision, clusters, attribute_results)


# ---------------------------------------------------------------------------
# Evidence → attribute_results mapping
# ---------------------------------------------------------------------------

def _map_evidence_to_attribute_results(
    evidence: Dict[str, Any],
    molecule_class: str,
) -> List[Dict[str, Any]]:
    """Convert CharacterizationEvidence to cluster_builder-compatible dicts."""
    results = []

    # Identify categories that have CQA value assessments (to avoid double-counting)
    _CQA_CATEGORY_SET = {"aggregation", "charge_variants", "potency", "glycosylation", "purity"}

    # A. Section-level attributes (one per Q6B section)
    # Skip sections whose category already has a CQA value assessment — the CQA
    # three-state model already captures the section coverage for those.
    sections_found = set(evidence.get("sections_found", []))
    for section_name, category in _SECTION_CATEGORY_MAP.items():
        if category in _CQA_CATEGORY_SET:
            continue  # CQA value will represent this category
        found = section_name in sections_found
        is_cqa_section = category in _CQA_SECTIONS
        score = 1.0 if found else 0.0
        concern = "none" if found else ("major" if is_cqa_section else "minor")
        gaps = [] if found else [f"missing_section:{section_name}"]

        results.append({
            "attribute_id": f"char_section_{category}",
            "category": category,
            "score": score,
            "concern_level": concern,
            "is_cqa": is_cqa_section,
            "gaps": gaps,
        })

    # B. CQA value attributes (from three-state model)
    _CQA_FIELDS = {
        "hmw_pct": ("aggregation", True),
        "main_charge_peak_pct": ("charge_variants", True),
        "potency_relative_pct": ("potency", True),
        "afucosylation_pct": ("glycosylation", molecule_class.lower() in ("mab", "bispecific")),
        "acidic_variants_pct": ("charge_variants", False),
        "basic_variants_pct": ("charge_variants", False),
    }

    # Map three-state field names to evidence dict keys
    _THREE_STATE_MAP = {
        "hmw_pct": "hmw",
        "main_charge_peak_pct": "main_charge_peak",
        "potency_relative_pct": "relative_potency",
        "afucosylation_pct": "afucosylation",
    }

    for field_name, (category, is_cqa) in _CQA_FIELDS.items():
        # Get three-state info
        three_state_key = _THREE_STATE_MAP.get(field_name)
        state_data = evidence.get(three_state_key, {}) if three_state_key else {}
        state = state_data.get("state", "") if isinstance(state_data, dict) else ""
        value = evidence.get(field_name)

        if not state and value is not None:
            state = "present"
        elif not state:
            state = "confirmed_absent"

        score, concern, gaps = _score_cqa_value(field_name, state, value, is_cqa)

        results.append({
            "attribute_id": f"char_{field_name}",
            "category": category,
            "score": score,
            "concern_level": concern,
            "is_cqa": is_cqa,
            "gaps": gaps,
        })

    # C. Reference standard attribute
    ref_identified = evidence.get("reference_standard_identified", False)
    results.append({
        "attribute_id": "char_reference_standard",
        "category": "identity",
        "score": 1.0 if ref_identified else 0.0,
        "concern_level": "none" if ref_identified else "major",
        "is_cqa": False,
        "gaps": [] if ref_identified else ["reference_standard_missing"],
    })

    return results


# ---------------------------------------------------------------------------
# Case context builder
# ---------------------------------------------------------------------------

def _build_case_context(
    evidence: Dict[str, Any],
    molecule_class: str,
) -> CaseContext:
    """Build a CaseContext for characterization assessment."""
    # Identify flagged categories (sections with gaps)
    flagged_cats = []
    flagged_ids = []
    for section_name, category in _SECTION_CATEGORY_MAP.items():
        if section_name not in evidence.get("sections_found", []):
            flagged_cats.append(category)
            flagged_ids.append(f"char_section_{category}")

    gaps = evidence.get("critical_gaps", []) + evidence.get("extraction_uncertainties", [])

    return CaseContext(
        molecule_class=molecule_class,
        change_type="characterization_assessment",
        change_description="ICH Q6B characterization assessment",
        lifecycle_stage="CMC",
        flagged_attribute_ids=flagged_ids,
        flagged_categories=flagged_cats,
        identified_gaps=gaps,
    )


# ---------------------------------------------------------------------------
# Preliminary decision builder
# ---------------------------------------------------------------------------

def _build_preliminary_decision(
    case_context: CaseContext,
    clusters: list,
) -> PackageDecision:
    """Build a preliminary PackageDecision from cluster results."""
    blocking = [c for c in clusters if getattr(c, "package_blocking", False)]
    # Use concern_level if set by policy, otherwise fall back to base_concern_level
    def _concern(c):
        return getattr(c, "concern_level", None) or getattr(c, "base_concern_level", "none") or "none"
    n_critical = sum(1 for c in clusters if _concern(c) == "critical")
    n_major = sum(1 for c in clusters if _concern(c) == "major")

    # Determine verdict
    if blocking or n_critical > 0:
        verdict = "investigation_required"
    elif n_major >= 3:
        verdict = "supplement_required"
    elif n_major >= 1:
        verdict = "proceed_with_conditions"
    else:
        verdict = "proceed"

    # Compute confidence from cluster scores
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
# Overview builder (UI-compatible output)
# ---------------------------------------------------------------------------

_VERDICT_TO_CHAR = {
    "proceed": ("Adequate", "Ready"),
    "proceed_with_conditions": ("Adequate", "Needs Data"),
    "supplement_required": ("Gaps Identified", "Needs Data"),
    "investigation_required": ("Gaps Identified", "Not Ready"),
    "defer_package": ("Insufficient", "Not Ready"),
}


def _build_overview(
    evidence: Dict[str, Any],
    decision: PackageDecision,
    clusters: list,
    attribute_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build overview_data dict compatible with UI consumption."""
    ac, pp = _VERDICT_TO_CHAR.get(decision.package_verdict, ("Assessment Complete", "Review Required"))

    # Build cluster summaries for UI — only truly blocking or critical
    blocking_clusters = []
    for c in clusters:
        is_blocking = getattr(c, "package_blocking", False)
        is_critical = getattr(c, "concern_level", "") == "critical"
        if is_blocking or is_critical:
            blocking_clusters.append({
                "cluster_id": c.cluster_id,
                "dominant_category": getattr(c, "dominant_category", ""),
                "concern_level": getattr(c, "concern_level", "none"),
                "affected_attribute_ids": getattr(c, "affected_attribute_ids", []),
                "cluster_reason_summary": getattr(c, "cluster_reason_summary", ""),
                "package_blocking": getattr(c, "package_blocking", False),
                "likely_reviewer_concerns": getattr(c, "likely_reviewer_concerns", []),
            })

    # Build reviewer concerns from evidence + cluster-level concerns
    reviewer_concerns = evidence.get("reviewer_concerns", [])
    predicted_questions = [
        {
            "question": q,
            "confidence": "moderate",
            "source": "judgment_engine",
            "affected_attributes": [],
            "primary": i == 0,
        }
        for i, q in enumerate(reviewer_concerns)
    ]
    # Add cluster-derived concerns
    for c in clusters:
        for concern_tag in getattr(c, "likely_reviewer_concerns", []):
            if concern_tag.startswith("[cluster_policy:"):
                rule = concern_tag.strip("[]").split(":")[1]
                predicted_questions.append({
                    "question": f"Cluster {getattr(c, 'dominant_category', '')}: policy rule {rule} triggered",
                    "confidence": "high",
                    "source": "cluster_policy",
                    "affected_attributes": getattr(c, "affected_attribute_ids", []),
                    "primary": False,
                })

    # Build decision rule trace
    rule_ids = list(getattr(decision, "decision_rule_ids", []) or [])
    for c in clusters:
        for tag in getattr(c, "likely_reviewer_concerns", []):
            if tag.startswith("[cluster_policy:"):
                rule = tag.strip("[]").split(":")[1]
                if rule not in rule_ids:
                    rule_ids.append(rule)

    # Posture rationale
    if blocking_clusters:
        cat_names = [bc["dominant_category"].replace("_", " ") for bc in blocking_clusters[:3]]
        rationale = f"Gaps identified in: {', '.join(cat_names)}. Additional data required before submission."
    elif ac == "Adequate":
        rationale = "Characterization data covers all critical Q6B sections with adequate CQA evidence."
    else:
        rationale = "Assessment completed with identified gaps. Review extraction uncertainties."

    return {
        "analytical_conclusion": ac,
        "package_posture": pp,
        "posture_rationale": rationale,
        "confidence_breakdown": {
            "analytical_confidence": decision.confidence,
            "package_readiness": 1.0 - 0.25 * len(blocking_clusters),
            "evidence_completeness": evidence.get("completeness_score", 0),
        },
        "judgment": {
            "package_verdict": decision.package_verdict,
            "confidence": decision.confidence,
            "confidence_band": decision.confidence_band,
            "abstain_flag": getattr(decision, "abstain_flag", False),
            "key_finding": rationale,
            "decision_rule_ids": rule_ids,
        },
        "judgment_summary": {
            "analytical_conclusion": ac,
            "package_posture": pp,
            "posture_rationale": rationale,
            "decision_rule_ids": rule_ids,
        },
        "blocking_clusters": blocking_clusters,
        "counterfactuals": [],
        "reviewer_risk": {"predicted_questions": predicted_questions},
        "critical_attributes": [
            {
                "name": ar["attribute_id"].replace("char_", "").replace("_", " ").title(),
                "score": ar["score"],
                "concern": ar["concern_level"],
                "is_cqa": ar["is_cqa"],
                "category": ar["category"],
                "action": "Review" if ar["concern_level"] in ("major", "critical") else "Accept",
            }
            for ar in attribute_results
            if ar["concern_level"] != "none"
        ],
        "extracted_evidence": evidence,
        "document_type": "CHARACTERIZATION",
    }
