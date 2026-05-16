"""
StabilityAssessor — maps StabilityEvidence to the
cluster → policy → PackageDecision judgment chain.

Scoring semantics are ADEQUACY-based: does the stability data support
the shelf-life claim with sufficient conditions, timepoints, and no
unresolved OOS events?

P2: Stability judgment vertical slice.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from schemas.case_context import CaseContext
from schemas.package_decision import PackageDecision, compute_confidence_band
from services.cluster_builder import build_risk_clusters
from services.cluster_matcher import match_for_clusters
from services.judgment_policy import apply_cluster_policy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ICH Q1A/Q5C required conditions
# ---------------------------------------------------------------------------
_REQUIRED_CONDITIONS = {
    "long_term": ["5c", "2-8c", "5°c", "25c/60rh", "25°c/60%rh"],
    "accelerated": ["25c/60rh", "30c/65rh", "25°c/60%rh", "30°c/65%rh"],
    "stress": ["40c/75rh", "40°c/75%rh"],
}


def _classify_conditions(conditions: List[str]) -> Dict[str, bool]:
    """Classify tested conditions into ICH categories."""
    conds_lower = [c.lower().replace(" ", "") for c in conditions]
    return {
        "has_long_term": any(
            any(req in cl for req in _REQUIRED_CONDITIONS["long_term"])
            for cl in conds_lower
        ),
        "has_accelerated": any(
            any(req in cl for req in _REQUIRED_CONDITIONS["accelerated"])
            for cl in conds_lower
        ),
        "has_stress": any(
            any(req in cl for req in _REQUIRED_CONDITIONS["stress"])
            for cl in conds_lower
        ),
    }


# ---------------------------------------------------------------------------
# Main assessor
# ---------------------------------------------------------------------------

def assess_stability(
    evidence: Dict[str, Any],
    molecule_class: str = "mAb",
) -> Dict[str, Any]:
    """Run stability judgment through the cluster → policy chain.

    Returns overview_data dict compatible with UI consumption.
    """
    attribute_results = _map_evidence_to_attribute_results(evidence, molecule_class)
    case_context = _build_case_context(evidence, molecule_class)

    clusters = build_risk_clusters(case_context, attribute_results)

    # Apply cluster policy (STAB-* rules already exist)
    from schemas.authority_context_pack import AuthorityContextPack
    try:
        from evidence_registry.registry import EvidenceRegistry
        registry = EvidenceRegistry()
        cluster_packs, case_pack = match_for_clusters(case_context, clusters, registry)
    except Exception:
        cluster_packs = [AuthorityContextPack(cluster_id=c.cluster_id) for c in clusters]
        case_pack = AuthorityContextPack(cluster_id="case_level")

    for i, (cluster, pack) in enumerate(zip(clusters, cluster_packs)):
        clusters[i] = apply_cluster_policy(cluster, pack)

    decision = _build_decision(case_context, clusters, evidence)
    return _build_overview(evidence, decision, clusters, attribute_results)


# ---------------------------------------------------------------------------
# Evidence → attribute_results mapping
# ---------------------------------------------------------------------------

def _map_evidence_to_attribute_results(
    evidence: Dict[str, Any],
    molecule_class: str,
) -> List[Dict[str, Any]]:
    """Convert StabilityEvidence to cluster_builder-compatible dicts."""
    results = []
    conditions = evidence.get("conditions_tested", [])
    cond_class = _classify_conditions(conditions)
    max_tp = evidence.get("max_timepoint_months", 0)
    shelf_life = evidence.get("proposed_shelf_life")
    oos_events = evidence.get("oos_events", [])
    sufficiency = evidence.get("sufficiency_for_claim", "insufficient")

    # 1. Condition coverage
    n_conditions = len(conditions)
    cond_score = min(n_conditions / 3.0, 1.0)  # 3+ conditions = full score
    cond_concern = "none" if n_conditions >= 3 else ("minor" if n_conditions >= 2 else "major")
    cond_gaps = []
    if not cond_class["has_long_term"]:
        cond_gaps.append("missing_long_term_condition")
    if not cond_class["has_accelerated"]:
        cond_gaps.append("missing_accelerated_condition")
    if not cond_class["has_stress"]:
        cond_gaps.append("missing_stress_condition")

    results.append({
        "attribute_id": "stab_condition_coverage",
        "category": "condition_coverage",
        "score": cond_score,
        "concern_level": cond_concern if not cond_gaps else "major",
        "is_cqa": True,
        "gaps": cond_gaps,
    })

    # 2. Timepoint adequacy
    if shelf_life and shelf_life > 0:
        ratio = max_tp / shelf_life
        tp_score = min(ratio / 2.0, 1.0)  # 2x coverage = full score
        tp_concern = "none" if ratio >= 1.5 else ("minor" if ratio >= 1.0 else "major")
        tp_gaps = ["timepoint_insufficient"] if ratio < 1.0 else []
    else:
        tp_score = 0.7 if max_tp > 0 else 0.2
        tp_concern = "minor" if max_tp > 0 else "major"
        tp_gaps = ["no_shelf_life_claim"] if shelf_life is None else []

    results.append({
        "attribute_id": "stab_timepoint_adequacy",
        "category": "timepoint_adequacy",
        "score": tp_score,
        "concern_level": tp_concern,
        "is_cqa": True,
        "gaps": tp_gaps,
    })

    # 3. OOS resolution
    n_oos = len(oos_events)
    oos_score = 1.0 if n_oos == 0 else max(0.2, 1.0 - n_oos * 0.3)
    oos_concern = "none" if n_oos == 0 else ("minor" if n_oos <= 1 else "major")
    results.append({
        "attribute_id": "stab_oos_resolution",
        "category": "oos_resolution",
        "score": oos_score,
        "concern_level": oos_concern,
        "is_cqa": True,
        "gaps": [f"oos_event_{i}" for i in range(n_oos)] if n_oos > 0 else [],
    })

    # 4. Trend risk
    trends = evidence.get("trend_concerns", [])
    trend_score = 1.0 if not trends else max(0.3, 1.0 - len(trends) * 0.2)
    trend_concern = "none" if not trends else "minor"
    results.append({
        "attribute_id": "stab_trend_risk",
        "category": "trend_risk",
        "score": trend_score,
        "concern_level": trend_concern,
        "is_cqa": False,
        "gaps": [],
        "trend_detected": bool(trends),
    })

    # 5. Shelf-life claim support
    suf_score_map = {"sufficient": 0.95, "extrapolated": 0.6, "insufficient": 0.2}
    suf_score = suf_score_map.get(sufficiency, 0.5)
    suf_concern = "none" if sufficiency == "sufficient" else ("minor" if sufficiency == "extrapolated" else "major")
    results.append({
        "attribute_id": "stab_shelf_life_claim",
        "category": "shelf_life",
        "score": suf_score,
        "concern_level": suf_concern,
        "is_cqa": True,
        "gaps": ["shelf_life_insufficient"] if sufficiency == "insufficient" else [],
    })

    return results


def _build_case_context(evidence: Dict[str, Any], molecule_class: str) -> CaseContext:
    """Build CaseContext for stability assessment."""
    gaps = evidence.get("critical_gaps", [])
    return CaseContext(
        molecule_class=molecule_class,
        change_type="stability_assessment",
        change_description="ICH Q1A/Q5C stability assessment",
        lifecycle_stage="CMC",
        flagged_attribute_ids=[],
        flagged_categories=[],
        identified_gaps=gaps,
    )


def _build_decision(
    case_context: CaseContext,
    clusters: list,
    evidence: Dict[str, Any],
) -> PackageDecision:
    """Build PackageDecision from stability cluster results."""
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
# Verdict mapping and overview builder
# ---------------------------------------------------------------------------

_VERDICT_TO_STAB = {
    "proceed": ("Supports Claim", "Sufficient"),
    "proceed_with_conditions": ("Supports Claim", "Needs Monitoring"),
    "supplement_required": ("Extrapolation Needed", "Needs Data"),
    "investigation_required": ("Data Gaps", "Not Sufficient"),
    "defer_package": ("Insufficient", "Not Sufficient"),
}


def _build_overview(
    evidence: Dict[str, Any],
    decision: PackageDecision,
    clusters: list,
    attribute_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build UI-compatible overview dict."""
    ac, pp = _VERDICT_TO_STAB.get(decision.package_verdict, ("Assessed", "Review Required"))

    blocking_clusters = []
    for c in clusters:
        if getattr(c, "package_blocking", False) or (
            getattr(c, "concern_level", None) or getattr(c, "base_concern_level", "none")
        ) == "critical":
            blocking_clusters.append({
                "cluster_id": c.cluster_id,
                "dominant_category": getattr(c, "dominant_category", ""),
                "concern_level": getattr(c, "concern_level", None) or getattr(c, "base_concern_level", "none"),
                "affected_attribute_ids": getattr(c, "affected_attribute_ids", []),
                "cluster_reason_summary": getattr(c, "cluster_reason_summary", ""),
                "package_blocking": getattr(c, "package_blocking", False),
            })

    reviewer_concerns = evidence.get("reviewer_concerns", [])
    predicted_questions = [
        {"question": q, "confidence": "moderate", "source": "judgment_engine",
         "affected_attributes": [], "primary": i == 0}
        for i, q in enumerate(reviewer_concerns)
    ]

    conditions = evidence.get("conditions_tested", [])
    shelf_life = evidence.get("proposed_shelf_life")
    oos = evidence.get("oos_events", [])

    if ac == "Supports Claim":
        rationale = (
            f"Stability data covers {len(conditions)} conditions with "
            f"max timepoint {evidence.get('max_timepoint_months', 0)} months. "
            f"Shelf-life claim of {shelf_life} months is supported."
        )
    else:
        issues = []
        if len(conditions) < 3:
            issues.append(f"only {len(conditions)} conditions tested")
        if oos:
            issues.append(f"{len(oos)} OOS event(s)")
        rationale = f"Stability assessment identified gaps: {', '.join(issues) if issues else 'review extraction details'}."

    return {
        "analytical_conclusion": ac,
        "package_posture": pp,
        "posture_rationale": rationale,
        "confidence_breakdown": {
            "analytical_confidence": decision.confidence,
            "package_readiness": 1.0 - 0.25 * len(blocking_clusters),
            "evidence_completeness": min(len(conditions) / 3.0, 1.0),
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
                "name": ar["attribute_id"].replace("stab_", "").replace("_", " ").title(),
                "score": ar["score"],
                "concern": ar["concern_level"],
                "is_cqa": ar["is_cqa"],
                "category": ar["category"],
                "action": "Review" if ar["concern_level"] in ("major", "critical") else "Accept",
            }
            for ar in attribute_results if ar["concern_level"] != "none"
        ],
        "extracted_evidence": evidence,
        "document_type": "STABILITY",
    }
