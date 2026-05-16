"""
Submission Readiness Pipeline (SP v5 Priority P2).

Assesses CMC submission readiness across 8 evidence areas defined by
ICH guidelines. Works entirely with local modules (evidence_registry,
evidence_closure) -- no external dependencies required.

The 8 areas (per ICH CTD Module 3 structure):
  1. Drug Substance Characterization (S.1-S.7)
  2. Drug Product Manufacturing (P.1-P.8)
  3. Analytical Validation
  4. Stability Data
  5. Process Validation
  6. Specification Justification
  7. Comparability Evidence
  8. Regulatory Precedent Coverage

Usage:
    from pipelines.submission_readiness import assess_readiness
    report = assess_readiness(package_sections)
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

# Ensure project root is importable
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Re-insert at front if it was already present but not first (needed when
# reg-intel-biopharma path was inserted earlier by the service layer).
elif sys.path[0] != _PROJECT_ROOT:
    sys.path.remove(_PROJECT_ROOT)
    sys.path.insert(0, _PROJECT_ROOT)

from services.schemas import AreaScore, ReadinessReport  # noqa: E402
from evidence_registry import EvidenceRegistry  # noqa: E402
from modules.evidence_closure.analyzer import analyze as closure_analyze  # noqa: E402
from modules.evidence_closure.schemas import FindingRecord  # noqa: E402


# =========================================================================
# Evidence Area Definitions
# =========================================================================

EVIDENCE_AREAS: Dict[str, Dict[str, Any]] = {
    "ds_characterization": {
        "name": "Drug Substance Characterization (S.1-S.7)",
        "weight": 0.15,
        "required_categories": ["identity", "purity", "physicochemical"],
        "ich_sections": ["S.1", "S.2", "S.3", "S.4", "S.5", "S.6", "S.7"],
        "registry_tags": ["characterization", "structure", "physicochemical"],
        "minimum_evidence_types": ["guideline_clause"],
        "critical_gaps": [
            "Primary structure confirmation missing",
            "Higher-order structure data absent",
            "Post-translational modification analysis incomplete",
        ],
    },
    "dp_manufacturing": {
        "name": "Drug Product Manufacturing (P.1-P.8)",
        "weight": 0.15,
        "required_categories": ["purity", "potency"],
        "ich_sections": ["P.1", "P.2", "P.3", "P.4", "P.5", "P.6", "P.7", "P.8"],
        "registry_tags": ["process_change", "supplement", "mAb"],
        "minimum_evidence_types": ["guideline_clause"],
        "critical_gaps": [
            "Manufacturing process description incomplete",
            "Control strategy not documented",
            "Container closure system suitability not demonstrated",
        ],
    },
    "analytical_validation": {
        "name": "Analytical Validation",
        "weight": 0.12,
        "required_categories": ["identity", "purity", "potency", "safety"],
        "ich_sections": ["S.4.3", "P.5.3"],
        "registry_tags": ["bioassay", "analytical"],
        "minimum_evidence_types": ["guideline_clause"],
        "critical_gaps": [
            "Method validation reports missing or incomplete",
            "System suitability criteria not defined",
            "Reference standard qualification absent",
        ],
    },
    "stability_data": {
        "name": "Stability Data",
        "weight": 0.15,
        "required_categories": ["stability"],
        "ich_sections": ["S.7", "P.8"],
        "registry_tags": ["stability", "accelerated", "shelf_life"],
        "minimum_evidence_types": ["guideline_clause"],
        "critical_gaps": [
            "Insufficient stability timepoints (<6 months real-time)",
            "No accelerated stability data",
            "Photostability data missing",
            "Stability-indicating methods not validated",
        ],
    },
    "process_validation": {
        "name": "Process Validation",
        "weight": 0.12,
        "required_categories": ["purity", "potency"],
        "ich_sections": ["S.2.5", "P.3.5"],
        "registry_tags": ["process_change", "lot_count", "site_transfer"],
        "minimum_evidence_types": ["guideline_clause", "precedent"],
        "critical_gaps": [
            "Fewer than 3 consecutive validation lots",
            "Process parameter ranges not justified",
            "Hold time studies not completed",
        ],
    },
    "spec_justification": {
        "name": "Specification Justification",
        "weight": 0.10,
        "required_categories": ["identity", "purity", "potency", "safety"],
        "ich_sections": ["S.4", "P.5"],
        "registry_tags": ["reference_standard", "consistency", "comparability"],
        "minimum_evidence_types": ["guideline_clause"],
        "critical_gaps": [
            "Specification limits not justified with clinical data",
            "Acceptance criteria not aligned with manufacturing capability",
            "No statistical basis for specification ranges",
        ],
    },
    "comparability": {
        "name": "Comparability Evidence",
        "weight": 0.12,
        "required_categories": ["identity", "purity", "potency", "physicochemical"],
        "ich_sections": ["S.2.6", "P.2.6"],
        "registry_tags": ["comparability", "functional_bridging"],
        "minimum_evidence_types": ["guideline_clause", "precedent"],
        "critical_gaps": [
            "Side-by-side analytical comparison incomplete",
            "Functional comparability not demonstrated",
            "Statistical comparability analysis missing",
        ],
    },
    "regulatory_precedent": {
        "name": "Regulatory Precedent Coverage",
        "weight": 0.09,
        "required_categories": ["purity", "potency", "stability"],
        "ich_sections": [],
        "registry_tags": ["biosimilar", "process_change", "warning_letter"],
        "minimum_evidence_types": ["precedent"],
        "critical_gaps": [
            "No relevant regulatory precedent identified",
            "Warning letter risks not addressed",
            "Agency-specific requirements not reviewed",
        ],
    },
}


# =========================================================================
# Scoring Logic
# =========================================================================

def _score_area(
    area_id: str,
    area_def: Dict[str, Any],
    section_data: Optional[Dict[str, Any]],
    registry: EvidenceRegistry,
) -> AreaScore:
    """Score a single evidence area.

    Scoring is based on three factors:
      1. Completeness from user-provided section data (0-1)
      2. Registry coverage: how many required categories have entries
      3. Gap penalty: deductions for known critical gaps
    """
    user_completeness = 0.0
    user_gaps: List[str] = []

    if section_data is not None:
        user_completeness = float(section_data.get("completeness", 0.0))
        user_gaps = list(section_data.get("gaps", []))

    # Registry coverage: check how many required categories have entries
    required_cats = area_def["required_categories"]
    registry_tags = area_def["registry_tags"]

    covered_cats = 0
    for cat in required_cats:
        entries = registry.query(category=cat, tags=registry_tags)
        if entries:
            covered_cats += 1
    registry_coverage = covered_cats / max(len(required_cats), 1)

    # Combine: 60% user completeness, 40% registry coverage
    if section_data is not None:
        raw_score = 0.60 * user_completeness + 0.40 * registry_coverage
    else:
        # No user data provided -- rely entirely on registry
        raw_score = 0.30 * registry_coverage  # penalize missing user data heavily

    # Gap penalty
    critical_gap_count = len(user_gaps)
    gap_penalty = min(critical_gap_count * 0.08, 0.40)
    score = max(raw_score - gap_penalty, 0.0)
    score = round(min(score, 1.0), 3)

    # Status classification
    if score >= 0.80:
        status = "Ready"
    elif score >= 0.60:
        status = "Near-ready"
    elif score >= 0.30:
        status = "In progress"
    else:
        status = "Not ready"

    # Combine user gaps with auto-detected gaps
    all_gaps = list(user_gaps)
    if registry_coverage < 1.0:
        missing_cats = [c for c in required_cats if not registry.query(category=c, tags=registry_tags)]
        if missing_cats:
            all_gaps.append(f"Registry coverage gaps: {', '.join(missing_cats)}")

    return AreaScore(
        area_id=area_id,
        name=area_def["name"],
        score=score,
        weight=area_def["weight"],
        status=status,
        gaps=all_gaps,
    )


def _identify_gaps_via_closure(
    area_scores: List[AreaScore],
) -> List[str]:
    """Use evidence_closure to identify cross-area gaps and blockers."""
    findings = []
    for area in area_scores:
        if area.status in ("Not ready", "In progress"):
            for gap in area.gaps:
                findings.append(FindingRecord(
                    text=f"[{area.area_id}] {gap}",
                    category="gap",
                    severity="major" if area.status == "Not ready" else "warning",
                    source="submission_readiness",
                ))
        elif area.gaps:
            for gap in area.gaps:
                findings.append(FindingRecord(
                    text=f"[{area.area_id}] {gap}",
                    category="gap",
                    severity="info",
                    source="submission_readiness",
                ))

    if not findings:
        return []

    closure_report = closure_analyze(findings)
    return closure_report.priority_actions


# =========================================================================
# Public API
# =========================================================================

def assess_readiness(
    package_sections: Optional[List[Dict[str, Any]]] = None,
    registry: Optional[EvidenceRegistry] = None,
) -> ReadinessReport:
    """Assess CMC submission readiness across 8 evidence areas.

    Parameters
    ----------
    package_sections : list[dict] or None
        List of dicts with keys: ``area_id``, ``completeness`` (0-1),
        ``gaps`` (list of strings). If None or empty, assessment is
        based solely on evidence registry coverage.
    registry : EvidenceRegistry or None
        Custom registry instance. Defaults to the standard registry.

    Returns
    -------
    ReadinessReport
        Aggregate readiness assessment with per-area scores.
    """
    if registry is None:
        registry = EvidenceRegistry()

    # Build lookup from user-provided sections
    section_lookup: Dict[str, Dict[str, Any]] = {}
    if package_sections:
        for sec in package_sections:
            aid = sec.get("area_id", "")
            if aid:
                section_lookup[aid] = sec

    # Score each area
    area_scores: List[AreaScore] = []
    for area_id, area_def in EVIDENCE_AREAS.items():
        section_data = section_lookup.get(area_id)
        score = _score_area(area_id, area_def, section_data, registry)
        area_scores.append(score)

    # Compute weighted composite
    total_weight = sum(a.weight for a in area_scores)
    if total_weight > 0:
        composite = sum(a.score * a.weight for a in area_scores) / total_weight
    else:
        composite = 0.0
    composite = round(composite, 3)

    # Count statuses
    n_ready = sum(1 for a in area_scores if a.status == "Ready")
    n_blockers = sum(1 for a in area_scores if a.status == "Not ready")

    # Identify blockers
    blockers = []
    for a in area_scores:
        if a.status == "Not ready":
            blockers.append(f"{a.name}: {'; '.join(a.gaps[:2]) if a.gaps else 'insufficient evidence'}")

    # Use closure analysis to find cross-area gaps
    _identify_gaps_via_closure(area_scores)

    # Verdict
    if n_blockers > 0:
        verdict = "Not Ready -- address blockers before submission"
    elif composite >= 0.80:
        verdict = "Ready for submission"
    elif composite >= 0.60:
        verdict = "Near-ready -- minor gaps remain"
    else:
        verdict = "Significant gaps -- substantial work needed"

    return ReadinessReport(
        composite=composite,
        verdict=verdict,
        n_areas=len(area_scores),
        n_ready=n_ready,
        n_blockers=n_blockers,
        blockers=blockers,
        area_scores=area_scores,
    )
