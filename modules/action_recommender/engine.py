"""
Action Recommender Engine (S-4 Action Layer).

Given per-attribute comparability scores, uncertainty, and concern levels,
produces structured action recommendations using a 5-level taxonomy.

This is what separates a decision engine from an analysis tool: without
action recommendations, the system only shows gaps but never tells
practitioners what to do.

Decision rules (applied per-attribute):
  PROCEED:     score >= 0.85, uncertainty < 0.3, concern == "none"
  SUPPLEMENT:  score >= 0.70, uncertainty 0.3-0.5, concern in (none, minor)
  MONITOR:     score >= 0.70 but uncertainty > 0.4 (trending concern)
  INVESTIGATE: score < 0.70 OR concern == "major" OR uncertainty > 0.5
  DEFER:       score < 0.50 OR concern == "critical" OR uncertainty > 0.7

Usage:
    from modules.action_recommender.engine import recommend_attribute_action
    action = recommend_attribute_action(score=0.92, uncertainty=0.15, concern="none",
                                        attribute_name="SEC Monomer %", category="purity")
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from evidence_registry import EvidenceRegistry
from schemas.provenance import ProvenanceRecord


# =========================================================================
# Action Taxonomy
# =========================================================================

ACTION_LEVELS = {
    "PROCEED": "Evidence sufficient. Manufacturing change acceptable for this attribute.",
    "SUPPLEMENT": "Minor gap. Collect 1-2 additional data points to strengthen the case.",
    "INVESTIGATE": "Meaningful difference detected. Root-cause investigation required before proceeding.",
    "MONITOR": "Attribute trending. Implement enhanced monitoring in post-change batches.",
    "DEFER": "Insufficient evidence OR critical difference. Do not proceed until resolved.",
}

# Ordered by severity for overall summary roll-up (most severe first)
_SEVERITY_ORDER = ["DEFER", "INVESTIGATE", "MONITOR", "SUPPLEMENT", "PROCEED"]


# =========================================================================
# Regulatory reference lookup
# =========================================================================

_REGULATORY_REFERENCES = {
    "identity": "ICH Q5E Section 2.1; FDA Guidance on Demonstrating Comparability (2003)",
    "purity": "ICH Q5E Section 2.2; ICH Q6B Specifications for Biotechnological Products",
    "potency": "ICH Q5E Section 2.3; FDA Potency Assurance Guidance (2011)",
    "safety": "ICH Q5E Section 2.4; ICH Q5A Viral Safety; ICH Q6B",
    "stability": "ICH Q5E Section 3; ICH Q1A/Q5C Stability Testing",
    "physicochemical": "ICH Q5E Section 2.2; ICH Q6B; FDA Biosimilarity Guidance (2015)",
}

_DEFAULT_REGULATORY_REF = "ICH Q5E Comparability of Biotechnological/Biological Products"


# =========================================================================
# Effort estimates
# =========================================================================

_EFFORT_BY_ACTION = {
    "PROCEED": "No additional work required. Document in comparability protocol.",
    "SUPPLEMENT": "1-2 additional lots or 1 orthogonal method (~1-2 weeks).",
    "INVESTIGATE": "Root-cause investigation including process parameter review (~2-4 weeks).",
    "MONITOR": "Enhanced sampling plan for next 3-5 post-change batches (~1-3 months).",
    "DEFER": "Full resolution required: additional studies, process adjustment, or regulatory consult (~1-3 months).",
}


# =========================================================================
# Next-best-evidence lookup
# =========================================================================

_NEXT_EVIDENCE = {
    ("SUPPLEMENT", "identity"): "Run confirmatory peptide mapping on 1-2 additional lots.",
    ("SUPPLEMENT", "purity"): "Add 1-2 post-change lots to SE-HPLC and CE-SDS datasets.",
    ("SUPPLEMENT", "potency"): "Repeat bioassay with additional replicates or add orthogonal binding assay.",
    ("SUPPLEMENT", "safety"): "Confirm impurity levels with additional lot testing.",
    ("SUPPLEMENT", "stability"): "Extend accelerated stability comparison to 3-month time point.",
    ("SUPPLEMENT", "physicochemical"): "Add orthogonal characterization (e.g., LC-MS for glycans).",
    ("INVESTIGATE", "identity"): "Perform full LC-MS/MS sequence coverage and disulfide mapping.",
    ("INVESTIGATE", "purity"): "Characterize HMW species by SEC-MALS; assess aggregate mechanism.",
    ("INVESTIGATE", "potency"): "Conduct head-to-head dose-response bioassay with expanded lot panel.",
    ("INVESTIGATE", "safety"): "Perform HCP identification by LC-MS/MS; assess process-related impurity clearance.",
    ("INVESTIGATE", "stability"): "Initiate forced degradation study comparing pre- and post-change material.",
    ("INVESTIGATE", "physicochemical"): "Full glycan profiling with orthogonal methods (HILIC + CE-LIF).",
    ("MONITOR", "identity"): "Include identity confirmation in routine batch release for next 5 lots.",
    ("MONITOR", "purity"): "Track purity trending in next 3-5 post-change lots with control charts.",
    ("MONITOR", "potency"): "Include potency trending in enhanced batch analysis for next 5 lots.",
    ("MONITOR", "safety"): "Implement tighter impurity monitoring on next 3-5 production batches.",
    ("MONITOR", "stability"): "Compare real-time stability data at 6- and 12-month points.",
    ("MONITOR", "physicochemical"): "Track attribute in enhanced monitoring protocol for post-change lots.",
    ("DEFER", "identity"): "Resolve sequence discrepancy before proceeding. Consider cell bank re-qualification.",
    ("DEFER", "purity"): "Investigate root cause of purity shift. May require process optimization.",
    ("DEFER", "potency"): "Potency gap must be closed. Consider process or formulation adjustment.",
    ("DEFER", "safety"): "Safety attribute out of range. Resolve before any further manufacturing.",
    ("DEFER", "stability"): "Stability concern must be resolved. Initiate bridging stability study.",
    ("DEFER", "physicochemical"): "Critical physicochemical shift. Characterize mechanism and assess clinical impact.",
}

_DEFAULT_NEXT_EVIDENCE = {
    "PROCEED": "No additional evidence needed. Maintain routine batch monitoring.",
    "SUPPLEMENT": "Collect 1-2 additional data points from post-change lots.",
    "INVESTIGATE": "Conduct root-cause analysis and expanded characterization.",
    "MONITOR": "Implement enhanced trending for this attribute in post-change production.",
    "DEFER": "Full investigation and resolution required before proceeding.",
}


def _query_registry_for_category(
    registry: EvidenceRegistry,
    category: str,
    action_level: str,
) -> tuple:
    """Query the evidence registry for guidelines relevant to this category + action.

    Returns:
        (regulatory_reference: str, provenance_records: list[ProvenanceRecord])
    """
    # Query for guideline clauses applicable to this category
    guidelines = registry.query(category=category, entry_type="guideline_clause")

    # For INVESTIGATE/DEFER, also pull precedents (they show what happened before)
    precedents = []
    if action_level in ("INVESTIGATE", "DEFER"):
        precedents = registry.query(category=category, entry_type="precedent")

    # Build reference string and provenance records
    provenance = []
    ref_parts = []

    for entry in guidelines:
        ref_parts.append(f"{entry.source} {entry.id}: {entry.title}")
        provenance.append(entry.to_provenance(
            module="action_recommender",
            context=f"Guideline for {action_level} on {category}",
        ))

    for entry in precedents[:2]:  # Limit to 2 most relevant precedents
        ref_parts.append(f"[Precedent] {entry.source}: {entry.title}")
        provenance.append(entry.to_provenance(
            module="action_recommender",
            context=f"Precedent for {action_level} on {category}",
        ))

    if ref_parts:
        regulatory_ref = "; ".join(ref_parts)
    else:
        # Fall back to hardcoded
        regulatory_ref = _REGULATORY_REFERENCES.get(category, _DEFAULT_REGULATORY_REF)

    return regulatory_ref, provenance


# =========================================================================
# Data Classes
# =========================================================================

@dataclass
class ActionRecommendation:
    """Structured action recommendation for a single attribute."""
    attribute_name: str
    category: str
    action_level: str              # one of ACTION_LEVELS keys
    rationale: str                 # why this action
    next_best_evidence: str        # what specific data would change the recommendation
    estimated_effort: str          # time/resource estimate
    regulatory_reference: str      # ICH Q5E, FDA guidance, etc.
    score: float = 0.0
    uncertainty: float = 0.0
    concern: str = "none"
    provenance: List[Dict] = field(default_factory=list)
    provenance_chain: Optional[Dict] = None  # P0-3: Structured traceability

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OverallActionSummary:
    """Aggregated action summary for the entire comparability package."""
    overall_action: str            # most severe action across all attributes
    n_proceed: int = 0
    n_supplement: int = 0
    n_investigate: int = 0
    n_monitor: int = 0
    n_defer: int = 0
    critical_attributes: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    estimated_timeline: str = ""
    regulatory_risk: str = ""      # low, medium, high

    def to_dict(self) -> dict:
        return asdict(self)


# =========================================================================
# Core Decision Logic
# =========================================================================

def _classify_action(
    score: float,
    uncertainty: float,
    concern: str,
) -> str:
    """Apply decision rules to determine action level.

    Rules are evaluated in priority order (most severe first):
      1. DEFER:       score < 0.50 OR concern == "critical" OR uncertainty > 0.7
      2. INVESTIGATE: score < 0.70 OR concern == "major" OR uncertainty > 0.5
      3. MONITOR:     score >= 0.70 but uncertainty > 0.4
      4. SUPPLEMENT:  score >= 0.70, uncertainty 0.3-0.5, concern in (none, minor)
      5. PROCEED:     score >= 0.85, uncertainty < 0.3, concern == "none"
    """
    # DEFER: most severe
    if score < 0.50 or concern == "critical" or uncertainty > 0.7:
        return "DEFER"

    # INVESTIGATE
    if score < 0.70 or concern == "major" or uncertainty > 0.5:
        return "INVESTIGATE"

    # MONITOR: score is acceptable but uncertainty is elevated
    if uncertainty > 0.4:
        return "MONITOR"

    # SUPPLEMENT: minor gap
    if score < 0.85 or uncertainty >= 0.3 or concern == "minor":
        return "SUPPLEMENT"

    # PROCEED: everything looks good
    return "PROCEED"


def _build_rationale(
    action_level: str,
    attribute_name: str,
    score: float,
    uncertainty: float,
    concern: str,
) -> str:
    """Build human-readable rationale for the action recommendation."""
    parts = []

    if action_level == "PROCEED":
        parts.append(
            f"{attribute_name} shows strong comparability (score={score:.3f}, "
            f"uncertainty={uncertainty:.2f}) with no concerns."
        )
    elif action_level == "SUPPLEMENT":
        reasons = []
        if score < 0.85:
            reasons.append(f"score={score:.3f} is below 0.85 threshold")
        if uncertainty >= 0.3:
            reasons.append(f"uncertainty={uncertainty:.2f} is moderately elevated")
        if concern == "minor":
            reasons.append("minor concern flagged")
        parts.append(
            f"{attribute_name} is largely comparable but has minor gaps: "
            + "; ".join(reasons) + "."
        )
    elif action_level == "INVESTIGATE":
        reasons = []
        if score < 0.70:
            reasons.append(f"score={score:.3f} is below 0.70 threshold")
        if concern == "major":
            reasons.append("major concern identified in comparability assessment")
        if uncertainty > 0.5:
            reasons.append(f"uncertainty={uncertainty:.2f} exceeds 0.50")
        parts.append(
            f"{attribute_name} requires investigation: "
            + "; ".join(reasons) + "."
        )
    elif action_level == "MONITOR":
        parts.append(
            f"{attribute_name} is comparable (score={score:.3f}) but shows trending "
            f"uncertainty ({uncertainty:.2f} > 0.40). Enhanced post-change monitoring recommended."
        )
    elif action_level == "DEFER":
        reasons = []
        if score < 0.50:
            reasons.append(f"score={score:.3f} is critically low")
        if concern == "critical":
            reasons.append("critical concern identified")
        if uncertainty > 0.7:
            reasons.append(f"uncertainty={uncertainty:.2f} is unacceptably high")
        parts.append(
            f"{attribute_name} cannot proceed: "
            + "; ".join(reasons) + ". Resolution required before manufacturing change."
        )

    return " ".join(parts)


def _build_alternative(action_level: str, category: str) -> str:
    """Build a brief alternative conclusion statement for provenance chain."""
    alternatives = {
        "PROCEED": f"If additional {category} data showed divergence, action could escalate to SUPPLEMENT.",
        "SUPPLEMENT": f"With 1-2 additional {category} data points, action could resolve to PROCEED.",
        "MONITOR": f"If trending stabilizes in post-change lots, action could resolve to PROCEED.",
        "INVESTIGATE": f"If root-cause analysis for {category} is favorable, action could downgrade to SUPPLEMENT.",
        "DEFER": f"Resolution of {category} concerns could downgrade action to INVESTIGATE or SUPPLEMENT.",
    }
    return alternatives.get(action_level, f"Additional {category} evidence could change this recommendation.")


def recommend_attribute_action(
    score: float,
    uncertainty: float,
    concern: str,
    attribute_name: str,
    category: str = "physicochemical",
    is_cqa: bool = False,
    registry: Optional[EvidenceRegistry] = None,
) -> ActionRecommendation:
    """Compute a structured action recommendation for a single attribute.

    Parameters
    ----------
    score : float
        Comparability score 0-1 (1 = identical).
    uncertainty : float
        Residual uncertainty 0-1.
    concern : str
        Concern level: "none", "minor", "major", "critical".
    attribute_name : str
        Human-readable attribute name.
    category : str
        Attribute category (identity, purity, potency, safety, stability, physicochemical).
    is_cqa : bool
        Whether the attribute is classified as a Critical Quality Attribute.

    Returns
    -------
    ActionRecommendation
    """
    action_level = _classify_action(score, uncertainty, concern)

    # CQA escalation: if CQA and action is SUPPLEMENT, escalate to INVESTIGATE
    if is_cqa and action_level == "SUPPLEMENT" and concern != "none":
        action_level = "INVESTIGATE"

    rationale = _build_rationale(action_level, attribute_name, score, uncertainty, concern)

    # Look up next-best-evidence
    next_evidence = _NEXT_EVIDENCE.get(
        (action_level, category),
        _DEFAULT_NEXT_EVIDENCE.get(action_level, ""),
    )

    # Regulatory reference
    provenance_records = []
    if registry is not None:
        reg_ref, provenance_records = _query_registry_for_category(
            registry, category, action_level,
        )
    else:
        reg_ref = _REGULATORY_REFERENCES.get(category, _DEFAULT_REGULATORY_REF)

    # Effort estimate
    effort = _EFFORT_BY_ACTION.get(action_level, "")

    # Build provenance chain (P0-3)
    from schemas.provenance_chain import ProvenanceChain
    norm_refs = [p.source_id for p in provenance_records if p.source_type == "guideline"]
    prec_refs = [p.source_id for p in provenance_records if p.source_type == "precedent"]
    prov_chain = ProvenanceChain(
        attribute_name=attribute_name,
        normative_refs=norm_refs[:3],
        precedent_refs=prec_refs[:2],
        user_evidence={},  # Populated by caller if needed
        inference_summary=rationale[:200],
        confidence=score if score >= 0 else 0.5,
        alternative_conclusion=_build_alternative(action_level, category),
        source_module='action_recommender',
    )

    return ActionRecommendation(
        attribute_name=attribute_name,
        category=category,
        action_level=action_level,
        rationale=rationale,
        next_best_evidence=next_evidence,
        estimated_effort=effort,
        regulatory_reference=reg_ref,
        score=score,
        uncertainty=uncertainty,
        concern=concern,
        provenance=[p.to_dict() for p in provenance_records],
        provenance_chain=prov_chain.to_dict(),
    )


def recommend_overall_actions(
    attribute_actions: List[ActionRecommendation],
    molecule_class: str = "mAb",
    modality: str = "IV",
) -> OverallActionSummary:
    """Aggregate per-attribute actions into overall action — SP v5 5-rule version.

    Parameters
    ----------
    attribute_actions : list of ActionRecommendation
    molecule_class : str
        From validated input. Affects Rule 5 context modifiers.
    modality : str
        From validated input. Affects Rule 5 context modifiers.
    """
    if not attribute_actions:
        return OverallActionSummary(
            overall_action="DEFER",
            next_steps=["No attribute data available."],
            estimated_timeline="N/A",
            regulatory_risk="high",
        )

    counts = {level: 0 for level in ACTION_LEVELS}
    for aa in attribute_actions:
        counts[aa.action_level] = counts.get(aa.action_level, 0) + 1

    # Identify CQA-flagged actions (requires attribute to carry is_cqa info)
    # We check the rationale for "CQA" mention as a heuristic since
    # ActionRecommendation doesn't carry is_cqa directly
    cqa_investigate = [a for a in attribute_actions
                       if a.action_level == "INVESTIGATE"
                       and ("CQA" in a.rationale or "cqa" in a.attribute_name.lower())]

    # --- Rule 1: DEFER blocks all ---
    if counts.get("DEFER", 0) > 0:
        overall = "DEFER"

    # --- Rule 2: INVESTIGATE elevates (CQA-aware) ---
    elif counts.get("INVESTIGATE", 0) > 0:
        if cqa_investigate:
            overall = "INVESTIGATE"
        elif counts["INVESTIGATE"] <= 1:
            # Single non-CQA INVESTIGATE → treat as SUPPLEMENT
            overall = "SUPPLEMENT"
        else:
            overall = "INVESTIGATE"

    # --- Rule 3: SUPPLEMENT aggregation by proportion ---
    elif counts.get("SUPPLEMENT", 0) > 0:
        supplement_ratio = counts["SUPPLEMENT"] / len(attribute_actions)
        # Rule 5 context modifier: complex molecules have lower tolerance
        threshold = 0.30
        if molecule_class in ("bispecific", "ADC"):
            threshold = 0.20  # tighter for complex molecules
        if supplement_ratio > threshold:
            overall = "SUPPLEMENT"
        else:
            overall = "PROCEED"  # few supplements, proceed with conditions

    # --- Rule 4: MONITOR doesn't block ---
    elif counts.get("MONITOR", 0) > 0:
        overall = "PROCEED"  # MONITOR alone = PROCEED with monitoring

    else:
        overall = "PROCEED"

    critical_attrs = [
        aa.attribute_name for aa in attribute_actions
        if aa.action_level in ("DEFER", "INVESTIGATE")
    ]

    next_steps = _build_next_steps(attribute_actions, counts, overall)
    timeline = _estimate_timeline(overall, counts)
    reg_risk = _assess_regulatory_risk(overall, counts, len(attribute_actions))

    return OverallActionSummary(
        overall_action=overall,
        n_proceed=counts.get("PROCEED", 0),
        n_supplement=counts.get("SUPPLEMENT", 0),
        n_investigate=counts.get("INVESTIGATE", 0),
        n_monitor=counts.get("MONITOR", 0),
        n_defer=counts.get("DEFER", 0),
        critical_attributes=critical_attrs,
        next_steps=next_steps,
        estimated_timeline=timeline,
        regulatory_risk=reg_risk,
    )


# =========================================================================
# Helpers
# =========================================================================

def _build_next_steps(
    actions: List[ActionRecommendation],
    counts: Dict[str, int],
    overall: str,
) -> List[str]:
    """Build prioritized next-steps list."""
    steps = []

    # DEFER items first
    defer_attrs = [a for a in actions if a.action_level == "DEFER"]
    if defer_attrs:
        names = ", ".join(a.attribute_name for a in defer_attrs)
        steps.append(
            f"HOLD: Resolve {len(defer_attrs)} deferred attribute(s) before proceeding: {names}."
        )

    # INVESTIGATE items
    inv_attrs = [a for a in actions if a.action_level == "INVESTIGATE"]
    if inv_attrs:
        names = ", ".join(a.attribute_name for a in inv_attrs)
        steps.append(
            f"INVESTIGATE: Conduct root-cause analysis for {len(inv_attrs)} attribute(s): {names}."
        )

    # MONITOR items
    mon_attrs = [a for a in actions if a.action_level == "MONITOR"]
    if mon_attrs:
        names = ", ".join(a.attribute_name for a in mon_attrs)
        steps.append(
            f"MONITOR: Implement enhanced trending for {len(mon_attrs)} attribute(s): {names}."
        )

    # SUPPLEMENT items
    sup_attrs = [a for a in actions if a.action_level == "SUPPLEMENT"]
    if sup_attrs:
        names = ", ".join(a.attribute_name for a in sup_attrs)
        steps.append(
            f"SUPPLEMENT: Collect additional data for {len(sup_attrs)} attribute(s): {names}."
        )

    # If everything proceeds
    if overall == "PROCEED":
        steps.append(
            "All attributes pass comparability. Document conclusion and proceed with change implementation."
        )

    # General regulatory step
    if overall in ("PROCEED", "SUPPLEMENT", "MONITOR"):
        steps.append("Prepare comparability report for regulatory submission per ICH Q5E.")
    elif overall in ("INVESTIGATE", "DEFER"):
        steps.append(
            "Do NOT submit comparability package until all DEFER/INVESTIGATE items are resolved."
        )

    return steps


def _estimate_timeline(overall: str, counts: Dict[str, int]) -> str:
    """Estimate timeline based on worst-case action."""
    if overall == "PROCEED":
        return "Ready for submission. No additional studies required."
    elif overall == "SUPPLEMENT":
        return "~2-4 weeks for additional data collection."
    elif overall == "MONITOR":
        return "~1-3 months for enhanced monitoring across post-change batches."
    elif overall == "INVESTIGATE":
        n = counts.get("INVESTIGATE", 0) + counts.get("DEFER", 0)
        return f"~1-2 months for root-cause investigation of {n} attribute(s)."
    else:  # DEFER
        return "Timeline TBD pending resolution of critical findings. Minimum 1-3 months."


def _assess_regulatory_risk(
    overall: str, counts: Dict[str, int], n_total: int
) -> str:
    """Assess regulatory risk level."""
    if overall == "DEFER":
        return "high"
    elif overall == "INVESTIGATE":
        return "high"
    elif overall == "MONITOR":
        # If many attributes need monitoring, risk is medium-high
        n_monitor = counts.get("MONITOR", 0)
        return "medium" if n_monitor <= 2 else "high"
    elif overall == "SUPPLEMENT":
        return "low"
    else:
        return "low"
