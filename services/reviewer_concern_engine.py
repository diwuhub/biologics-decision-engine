"""
Reviewer Concern & Response Pressure Engine — Step 4.

Generates case-specific reviewer concerns from RiskClusters and
AuthorityContextPacks. Replaces three existing systems:
  - ui/config.py _build_predicted_questions()
  - services/reviewer_templates.py (template matching)
  - services/regulatory_evidence.py predict_reviewer_risks()

Key design principles:
  - Concerns are BIDIRECTIONAL: feed back into cluster priority
  - Different cluster profiles produce DIFFERENT concern text (not template)
  - May NOT invent new verdict categories (GUARD-004)
  - May modify confidence within bounds of SHIFT-002
  - All concerns with affects_verdict_confidence=True cite applied_rule_id

Step 4: Judgment Core Refactor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from schemas.authority_context_pack import AuthorityContextPack
from schemas.package_decision import PackageDecision, compute_confidence_band
from schemas.risk_cluster import RiskCluster


# ---------------------------------------------------------------------------
# Concern severity ordering
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

CONCERN_ORDER = {"none": 0, "minor": 1, "major": 2, "critical": 3}

# Maximum confidence reduction per SHIFT-002
MAX_PER_CONCERN_REDUCTION = 0.15
MAX_TOTAL_REDUCTION = 0.30


# ---------------------------------------------------------------------------
# ReviewerConcern dataclass
# ---------------------------------------------------------------------------

@dataclass
class ReviewerConcern:
    """A single reviewer concern generated from cluster analysis.

    Each concern is case-specific (not template-matched), traces to a
    source cluster, and carries response pressure scoring.
    """
    concern_id: str
    source_cluster_id: str
    concern_text: str  # Case-specific, NOT template-matched
    severity: str  # critical / high / medium / low
    authority_basis: str  # What authority evidence supports this concern
    gap_mapping: str  # What evidence gap this concern maps to
    followup_mapping: str  # Recommended followup type
    response_pressure_score: float  # 0-1: how urgently a response is needed
    affects_verdict_confidence: bool  # If True, must cite applied_rule_id
    neutralizing_evidence: str  # What evidence would neutralize this concern
    applied_rule_id: str  # Rule from catalog (required if affects confidence)

    # ---- P1-D: Per-question evidence linking ----
    source_type: str = "rule_inference"  # 'precedent_pattern' | 'rule_inference' | 'warning_letter_pattern'
    source_ref_ids: List[str] = field(default_factory=list)  # Reference IDs supporting this concern
    source_evidence: str = ""  # Actual text justifying the concern
    confidence_level: str = "medium"  # 'high' | 'medium' | 'low' (replaces arbitrary probability)


@dataclass
class ReviewerConcernResult:
    """Complete result from the reviewer concern engine."""
    concerns: List[ReviewerConcern] = field(default_factory=list)
    total_confidence_impact: float = 0.0
    applied_rule_ids: List[str] = field(default_factory=list)


# =========================================================================
# Main entry point
# =========================================================================

def generate_reviewer_concerns(
    clusters: List[RiskCluster],
    cluster_packs: List[AuthorityContextPack],
    case_pack: AuthorityContextPack,
    preliminary_decision: PackageDecision,
) -> ReviewerConcernResult:
    """Generate reviewer concerns from cluster analysis.

    Returns ReviewerConcernResult containing case-specific concerns,
    total confidence impact, and applied rule IDs.

    The engine follows GUARD-004: may modify confidence and follow-up
    priority but may NOT invent new verdict categories.
    """
    concerns: List[ReviewerConcern] = []
    concern_counter = 0

    # Build cluster->pack mapping
    cluster_pack_map: Dict[str, AuthorityContextPack] = {}
    for i, cluster in enumerate(clusters):
        if i < len(cluster_packs):
            cluster_pack_map[cluster.cluster_id] = cluster_packs[i]

    for cluster in clusters:
        pack = cluster_pack_map.get(cluster.cluster_id)
        if pack is None:
            continue

        # Generate concerns based on cluster profile
        cluster_concerns = _generate_cluster_concerns(
            cluster, pack, case_pack, concern_counter
        )
        concerns.extend(cluster_concerns)
        concern_counter += len(cluster_concerns)

    # Add case-level cross-cluster concerns
    case_level_concerns = _generate_case_level_concerns(
        clusters, cluster_packs, case_pack, concern_counter
    )
    concerns.extend(case_level_concerns)

    # Sort by severity then response pressure
    concerns.sort(key=lambda c: (
        SEVERITY_ORDER.get(c.severity, 99),
        -c.response_pressure_score,
    ))

    # Calculate total confidence impact (SHIFT-002)
    total_impact = _calculate_confidence_impact(concerns)

    # Collect applied rules
    applied_rules = list(set(c.applied_rule_id for c in concerns if c.applied_rule_id))

    result = ReviewerConcernResult(
        concerns=concerns,
        total_confidence_impact=total_impact,
        applied_rule_ids=applied_rules,
    )

    # Update cluster priority scores (bidirectional feedback)
    _update_cluster_priorities(clusters, concerns)

    return result


def apply_concerns_to_decision(
    decision: PackageDecision,
    concern_result: ReviewerConcernResult,
) -> PackageDecision:
    """Apply reviewer concern results to a PackageDecision.

    Modifies confidence within SHIFT-002 bounds. Does NOT invent
    new verdict categories (GUARD-004).
    """
    # Apply confidence reduction (SHIFT-002)
    confidence_affecting = [
        c for c in concern_result.concerns if c.affects_verdict_confidence
    ]

    total_reduction = 0.0
    for concern in confidence_affecting:
        reduction = min(
            concern.response_pressure_score * 0.15,
            MAX_PER_CONCERN_REDUCTION,
        )
        if total_reduction + reduction > MAX_TOTAL_REDUCTION:
            reduction = MAX_TOTAL_REDUCTION - total_reduction
        if reduction <= 0:
            break
        total_reduction += reduction

    if total_reduction > 0:
        decision.confidence = max(0.0, decision.confidence - total_reduction)
        decision.confidence_band = compute_confidence_band(decision.confidence)
        if "SHIFT-002" not in decision.decision_rule_ids:
            decision.decision_rule_ids.append("SHIFT-002")

    # Populate predicted_reviewer_concerns on the decision
    decision.predicted_reviewer_concerns = [
        {
            "concern_text": c.concern_text,
            "source_cluster_id": c.source_cluster_id,
            "authority_basis": c.authority_basis,
            "severity": c.severity,
            "response_pressure_score": c.response_pressure_score,
        }
        for c in concern_result.concerns
    ]

    # Add concern-engine applied rules
    for rule_id in concern_result.applied_rule_ids:
        if rule_id not in decision.decision_rule_ids:
            decision.decision_rule_ids.append(rule_id)

    # GUARD-004: do NOT modify verdict or invent new categories
    # (only confidence and follow-up priority)

    return decision


# =========================================================================
# Cluster-level concern generation
# =========================================================================

def _generate_cluster_concerns(
    cluster: RiskCluster,
    cluster_pack: AuthorityContextPack,
    case_pack: AuthorityContextPack,
    start_id: int,
) -> List[ReviewerConcern]:
    """Generate concerns for a specific cluster.

    Different cluster profiles produce DIFFERENT concern text.
    """
    concerns = []

    concern_level = CONCERN_ORDER.get(cluster.concern_level or "none", 0)
    semantics = cluster.risk_semantics
    category = cluster.dominant_category

    # Extract ref IDs and evidence from pack for evidence linking (P1-D)
    _norm_ref_ids = [r.entry_id for r in cluster_pack.normative_refs]
    _prec_ref_ids = [r.entry_id for r in cluster_pack.precedent_refs]
    _concern_ref_ids = [r.entry_id for r in cluster_pack.concern_pattern_refs]
    _all_ref_ids = _norm_ref_ids + _prec_ref_ids
    _norm_evidence = "; ".join(f"{r.title} ({r.source})" for r in cluster_pack.normative_refs[:2])
    _prec_evidence = "; ".join(f"{r.title} ({r.source})" for r in cluster_pack.precedent_refs[:2])

    # ---- Contradiction concerns ----
    if semantics == "contradiction" or cluster.contradiction_present:
        concerns.append(_make_concern(
            start_id + len(concerns),
            cluster.cluster_id,
            concern_text=(
                f"Methods within {category} produce conflicting comparability "
                f"conclusions. {_describe_cluster_attributes(cluster)} "
                f"This inter-method contradiction requires resolution before "
                f"any comparability claim can be substantiated."
            ),
            severity="critical",
            authority_basis=_summarize_authority(cluster_pack, "normative"),
            gap_mapping="inter_method_contradiction",
            followup_mapping="bridging_study",
            pressure=0.95,
            affects_confidence=True,
            neutralizing=(
                f"Provide bridging data or statistical analysis demonstrating "
                f"which {category} method is rate-limiting, and show the "
                f"discrepancy is within known assay variability."
            ),
            rule_id="CLUST-004",
            source_type="rule_inference",
            source_ref_ids=_all_ref_ids[:5],
            source_evidence=_norm_evidence or "Contradiction detected from inter-method comparison",
            confidence_level="high",
        ))

    # ---- Orthogonal gap concerns ----
    elif semantics == "orthogonal_gap":
        is_cqa = "CQA" if cluster.contains_cqa else "non-CQA"
        concerns.append(_make_concern(
            start_id + len(concerns),
            cluster.cluster_id,
            concern_text=(
                f"{category.capitalize()} {is_cqa} lacks orthogonal method support "
                f"per ICH Q5E. {_describe_cluster_attributes(cluster)} "
                f"A single assay type cannot substantiate comparability for "
                f"{'a Critical Quality Attribute' if cluster.contains_cqa else 'this attribute category'}."
            ),
            severity="high" if cluster.contains_cqa else "medium",
            authority_basis=_summarize_authority(cluster_pack, "normative"),
            gap_mapping="orthogonal_method_gap",
            followup_mapping="additional_testing",
            pressure=0.85 if cluster.contains_cqa else 0.55,
            affects_confidence=cluster.contains_cqa,
            neutralizing=(
                f"Provide orthogonal analytical method data (e.g., {_suggest_orthogonal(category)}) "
                f"confirming the primary assay conclusion."
            ),
            rule_id="AGGR-002" if cluster.contains_cqa else "GUARD-001",
            source_type="rule_inference",
            source_ref_ids=_norm_ref_ids[:3],
            source_evidence=_norm_evidence or f"Orthogonal gap inferred from {category} method coverage",
            confidence_level="high" if cluster.contains_cqa else "medium",
        ))

    # ---- Assay gap / hidden insufficiency concerns ----
    elif semantics == "assay_gap":
        # Differentiate between true method gaps and non-blocking gaps
        # (no_precedent, temporal_sparsity, weak_evidence, geography_divergence)
        reason_lower = cluster.cluster_reason_summary.lower()
        _NON_METHOD_GAP_KEYWORDS = (
            "no_precedent", "no precedent", "temporal_sparsity",
            "temporal sparsity", "weak_evidence", "weak evidence",
            "geography_divergence", "geography divergence",
            "pattern_only", "pattern only",
        )
        is_non_method_gap = any(kw in reason_lower for kw in _NON_METHOD_GAP_KEYWORDS)

        if is_non_method_gap:
            # Lower severity for non-method gaps
            concerns.append(_make_concern(
                start_id + len(concerns),
                cluster.cluster_id,
                concern_text=(
                    f"Evidence gap noted in {category}: "
                    f"{_extract_gap_type(reason_lower)}. "
                    f"{_describe_cluster_attributes(cluster)} "
                    f"This does not constitute a blocking gap but reduces "
                    f"confidence in the comparability assessment."
                ),
                severity="medium",
                authority_basis=_summarize_authority(cluster_pack, "normative"),
                gap_mapping="evidence_gap",
                followup_mapping="enhanced_monitoring",
                pressure=0.35,
                affects_confidence=False,
                neutralizing=(
                    f"Provide additional evidence or references supporting "
                    f"the comparability approach for {category}."
                ),
                rule_id="FALL-003",
                source_type="rule_inference",
                source_ref_ids=_norm_ref_ids[:2],
                source_evidence=_norm_evidence or f"Evidence gap in {category}",
                confidence_level="low",
            ))
        else:
            concerns.append(_make_concern(
                start_id + len(concerns),
                cluster.cluster_id,
                concern_text=(
                    f"Package-level gap detected: {category} cluster is missing a "
                    f"required method type per ICH Q5E despite individual attribute "
                    f"comparability. {_describe_cluster_attributes(cluster)} "
                    f"This constitutes a package-level insufficiency."
                ),
                severity="high",
                authority_basis=_summarize_authority(cluster_pack, "normative"),
                gap_mapping="missing_method_type",
                followup_mapping="additional_testing",
                pressure=0.80,
                affects_confidence=True,
                neutralizing=(
                    f"Add the missing method type for {category} assessment "
                    f"and demonstrate comparability across all required assays."
                ),
                rule_id="AGGR-003",
                source_type="rule_inference",
                source_ref_ids=_norm_ref_ids[:3],
                source_evidence=_norm_evidence or f"Missing method type for {category} per ICH Q5E",
                confidence_level="high",
            ))

    # ---- Favorable shift concerns ----
    elif semantics == "favorable_shift_requires_rationale":
        concerns.append(_make_concern(
            start_id + len(concerns),
            cluster.cluster_id,
            concern_text=(
                f"Favorable shift detected in {category}: post-change values "
                f"exceed pre-change. While seemingly positive, this requires "
                f"immunogenicity impact rationale per ICH Q5E/Q6B. "
                f"{_describe_cluster_attributes(cluster)}"
            ),
            severity="medium",
            authority_basis=_summarize_authority(cluster_pack, "normative"),
            gap_mapping="favorable_shift_rationale",
            followup_mapping="enhanced_monitoring",
            pressure=0.45,
            affects_confidence=False,
            neutralizing=(
                f"Provide immunogenicity risk assessment for the favorable "
                f"shift in {category}, including PK/PD correlation data."
            ),
            rule_id="SHIFT-001",
            source_type="rule_inference",
            source_ref_ids=_norm_ref_ids[:2],
            source_evidence=_norm_evidence or f"Favorable shift in {category} requires immunogenicity rationale",
            confidence_level="medium",
        ))

    # ---- Trend monitoring concerns ----
    elif semantics == "trend_requires_monitoring":
        concerns.append(_make_concern(
            start_id + len(concerns),
            cluster.cluster_id,
            concern_text=(
                f"Downward trend detected in {category} at accelerated stability. "
                f"{_describe_cluster_attributes(cluster)} "
                f"While currently within specification, extended monitoring is "
                f"required to confirm long-term comparability."
            ),
            severity="medium",
            authority_basis=_summarize_authority(cluster_pack, "normative"),
            gap_mapping="stability_trending",
            followup_mapping="enhanced_monitoring",
            pressure=0.50,
            affects_confidence=False,
            neutralizing=(
                f"Provide 12-month real-time stability data confirming "
                f"the trend does not project outside specification limits."
            ),
            rule_id="SHIFT-001",
            source_type="rule_inference",
            source_ref_ids=_norm_ref_ids[:2],
            source_evidence=_norm_evidence or f"Trend detected in {category} stability data",
            confidence_level="medium",
        ))

    # ---- No precedent concerns ----
    elif semantics == "no_precedent_low_confidence":
        concerns.append(_make_concern(
            start_id + len(concerns),
            cluster.cluster_id,
            concern_text=(
                f"No direct regulatory precedent found for {category} assessment "
                f"in this molecule/change combination. "
                f"{_describe_cluster_attributes(cluster)} "
                f"Judgment relies on normative basis only (ICH Q5E)."
            ),
            severity="medium",
            authority_basis=_summarize_authority(cluster_pack, "normative"),
            gap_mapping="no_precedent",
            followup_mapping="human_review",
            pressure=0.50,
            affects_confidence=True,
            neutralizing=(
                f"Identify analogous precedent from related molecule class "
                f"or change type, or provide additional normative justification."
            ),
            rule_id="FALL-001",
            source_type="rule_inference",
            source_ref_ids=_norm_ref_ids[:2],
            source_evidence=_norm_evidence or f"No precedent for {category}; normative basis only",
            confidence_level="low",
        ))

    # ---- Pattern concern only ----
    elif semantics == "pattern_concern_only":
        concerns.append(_make_concern(
            start_id + len(concerns),
            cluster.cluster_id,
            concern_text=(
                f"Only concern pattern references available for {category}; "
                f"no normative or precedent support for comparability conclusion. "
                f"{_describe_cluster_attributes(cluster)} "
                f"This limits confidence but does not constitute a blocking finding."
            ),
            severity="low",
            authority_basis="Concern pattern references only (GUARD-001 applies)",
            gap_mapping="pattern_only",
            followup_mapping="none",
            pressure=0.30,
            affects_confidence=False,
            neutralizing=(
                f"Provide normative or precedent references supporting "
                f"the comparability approach for {category}."
            ),
            rule_id="GUARD-001",
            source_type="warning_letter_pattern" if _concern_ref_ids else "rule_inference",
            source_ref_ids=_concern_ref_ids[:2] if _concern_ref_ids else [],
            source_evidence="Concern pattern references only (GUARD-001 applies)",
            confidence_level="low",
        ))

    # ---- Geography divergence concerns ----
    elif semantics == "cross_geography_divergence":
        concerns.append(_make_concern(
            start_id + len(concerns),
            cluster.cluster_id,
            concern_text=(
                f"Divergent FDA/EMA acceptance criteria detected for {category}. "
                f"{_describe_cluster_attributes(cluster)} "
                f"Geography-specific filing strategy is required."
            ),
            severity="medium",
            authority_basis=_summarize_authority(cluster_pack, "normative"),
            gap_mapping="geography_divergence",
            followup_mapping="geography_strategy",
            pressure=0.55,
            affects_confidence=True,
            neutralizing=(
                f"Provide parallel analyses demonstrating compliance with "
                f"both FDA and EMA acceptance criteria for {category}."
            ),
            rule_id="GEOG-001",
            source_type="precedent_pattern" if _prec_ref_ids else "rule_inference",
            source_ref_ids=_all_ref_ids[:3],
            source_evidence=_prec_evidence or _norm_evidence or f"Geography divergence in {category}",
            confidence_level="medium",
        ))

    # ---- Sufficient evidence but with minor concerns ----
    elif semantics == "sufficient_evidence" and concern_level >= 1:
        concerns.append(_make_concern(
            start_id + len(concerns),
            cluster.cluster_id,
            concern_text=(
                f"Minor concern noted in {category} assessment. "
                f"{_describe_cluster_attributes(cluster)} "
                f"Sufficient authority support exists but routine follow-up "
                f"may be advisable."
            ),
            severity="low",
            authority_basis=_summarize_authority(cluster_pack, "normative"),
            gap_mapping="minor_concern",
            followup_mapping="none",
            pressure=0.15,
            affects_confidence=False,
            neutralizing="No additional evidence required; routine monitoring sufficient.",
            rule_id="GUARD-003",
            source_type="rule_inference",
            source_ref_ids=_norm_ref_ids[:1],
            source_evidence=_norm_evidence or "Sufficient evidence with minor concern noted",
            confidence_level="high",
        ))

    return concerns


# =========================================================================
# Case-level concern generation
# =========================================================================

def _generate_case_level_concerns(
    clusters: List[RiskCluster],
    cluster_packs: List[AuthorityContextPack],
    case_pack: AuthorityContextPack,
    start_id: int,
) -> List[ReviewerConcern]:
    """Generate case-level cross-cluster concerns."""
    concerns = []

    # Cross-cluster authority sparsity
    sparse_packs = [p for p in cluster_packs if p.authority_sparsity_flag]
    if len(sparse_packs) > len(cluster_packs) / 2 and len(cluster_packs) > 1:
        concerns.append(_make_concern(
            start_id + len(concerns),
            "case_level",
            concern_text=(
                f"Authority sparsity detected across {len(sparse_packs)} of "
                f"{len(cluster_packs)} clusters. Package-wide normative coverage "
                f"is limited, reducing overall judgment confidence."
            ),
            severity="high",
            authority_basis="Package-level authority assessment",
            gap_mapping="authority_sparsity",
            followup_mapping="human_review",
            pressure=0.70,
            affects_confidence=True,
            neutralizing=(
                "Provide additional normative or precedent references "
                "covering the sparse clusters."
            ),
            rule_id="FALL-003",
            source_type="rule_inference",
            source_ref_ids=[],
            source_evidence="Package-level authority sparsity across multiple clusters",
            confidence_level="medium",
        ))

    # Multiple blocking clusters warning
    blocking = [c for c in clusters if c.package_blocking]
    if len(blocking) >= 2:
        categories = ", ".join(set(c.dominant_category for c in blocking))
        concerns.append(_make_concern(
            start_id + len(concerns),
            "case_level",
            concern_text=(
                f"Multiple blocking clusters ({len(blocking)}) detected across "
                f"{categories}. This level of concurrent concern strongly suggests "
                f"the package requires significant additional data or expert review."
            ),
            severity="critical",
            authority_basis="AGGR-002: multiple blocking clusters",
            gap_mapping="multiple_blocking",
            followup_mapping="human_review",
            pressure=0.90,
            affects_confidence=True,
            neutralizing=(
                "Resolve blocking concerns in each cluster independently "
                "before resubmitting the package."
            ),
            rule_id="AGGR-002",
            source_type="rule_inference",
            source_ref_ids=[c.cluster_id for c in blocking],
            source_evidence=f"Multiple blocking clusters ({len(blocking)}) across {categories}",
            confidence_level="high",
        ))

    return concerns


# =========================================================================
# Confidence impact calculation
# =========================================================================

def _calculate_confidence_impact(
    concerns: List[ReviewerConcern],
) -> float:
    """Calculate total confidence impact from concerns (SHIFT-002).

    Max 0.15 per concern, max 0.30 total.
    """
    total = 0.0
    for c in concerns:
        if not c.affects_verdict_confidence:
            continue
        reduction = min(c.response_pressure_score * 0.15, MAX_PER_CONCERN_REDUCTION)
        if total + reduction > MAX_TOTAL_REDUCTION:
            reduction = MAX_TOTAL_REDUCTION - total
        if reduction <= 0:
            break
        total += reduction
    return round(total, 4)


# =========================================================================
# Bidirectional feedback: update cluster priorities
# =========================================================================

def _update_cluster_priorities(
    clusters: List[RiskCluster],
    concerns: List[ReviewerConcern],
) -> None:
    """Update cluster priority_score based on generated concerns.

    Concerns feed BACK into cluster priority (bidirectional).
    """
    cluster_pressure: Dict[str, float] = {}
    for concern in concerns:
        cid = concern.source_cluster_id
        if cid == "case_level":
            continue
        current = cluster_pressure.get(cid, 0.0)
        cluster_pressure[cid] = max(current, concern.response_pressure_score)

    for cluster in clusters:
        if cluster.cluster_id in cluster_pressure:
            cluster.priority_score = cluster_pressure[cluster.cluster_id]
        elif cluster.priority_score is None:
            cluster.priority_score = 0.1


# =========================================================================
# Helper functions
# =========================================================================

def _make_concern(
    idx: int,
    source_cluster_id: str,
    concern_text: str,
    severity: str,
    authority_basis: str,
    gap_mapping: str,
    followup_mapping: str,
    pressure: float,
    affects_confidence: bool,
    neutralizing: str,
    rule_id: str,
    source_type: str = "rule_inference",
    source_ref_ids: Optional[List[str]] = None,
    source_evidence: str = "",
    confidence_level: str = "medium",
) -> ReviewerConcern:
    """Factory for creating ReviewerConcern instances."""
    return ReviewerConcern(
        concern_id=f"RC-{idx:03d}",
        source_cluster_id=source_cluster_id,
        concern_text=concern_text,
        severity=severity,
        authority_basis=authority_basis,
        gap_mapping=gap_mapping,
        followup_mapping=followup_mapping,
        response_pressure_score=pressure,
        affects_verdict_confidence=affects_confidence,
        neutralizing_evidence=neutralizing,
        applied_rule_id=rule_id,
        source_type=source_type,
        source_ref_ids=source_ref_ids or [],
        source_evidence=source_evidence,
        confidence_level=confidence_level,
    )


def _describe_cluster_attributes(cluster: RiskCluster) -> str:
    """Build a case-specific description of cluster attributes."""
    n = len(cluster.affected_attribute_ids)
    cqa_note = " (CQA)" if cluster.contains_cqa else ""
    attr_list = ", ".join(cluster.affected_attribute_ids[:3])
    if n > 3:
        attr_list += f" and {n - 3} more"
    return f"Affected attributes{cqa_note}: {attr_list}."


def _summarize_authority(
    pack: AuthorityContextPack,
    primary_type: str = "normative",
) -> str:
    """Summarize authority basis from a pack."""
    parts = []

    if pack.normative_refs:
        top = pack.normative_refs[0]
        parts.append(f"Normative: {top.title} ({top.source})")

    if pack.precedent_refs:
        top = pack.precedent_refs[0]
        parts.append(f"Precedent: {top.title} ({top.source})")

    if not parts:
        if pack.concern_pattern_refs:
            parts.append("Concern pattern references only")
        else:
            parts.append("No direct authority references available")

    return "; ".join(parts)


def _extract_gap_type(reason_lower: str) -> str:
    """Extract a human-readable gap description from cluster reason."""
    if "temporal_sparsity" in reason_lower or "temporal sparsity" in reason_lower:
        return "supporting evidence is dated (pre-QbD era)"
    elif "no_precedent" in reason_lower or "no precedent" in reason_lower:
        return "no direct regulatory precedent available"
    elif "weak_evidence" in reason_lower or "weak evidence" in reason_lower:
        return "mixed evidence strength with limited precedent"
    elif "geography_divergence" in reason_lower or "geography divergence" in reason_lower:
        return "divergent acceptance criteria across jurisdictions"
    elif "pattern_only" in reason_lower or "pattern only" in reason_lower:
        return "only concern pattern references available"
    return "evidence gap identified"


def _suggest_orthogonal(category: str) -> str:
    """Suggest orthogonal methods for a given category."""
    suggestions = {
        "potency": "cell-based potency assay, receptor binding assay",
        "purity": "orthogonal chromatography (e.g., AEX if primary is SEC), CE-SDS",
        "identity": "peptide mapping, intact mass spectrometry",
        "stability": "forced degradation comparison, alternate stability endpoint",
        "safety": "orthogonal HCP method (LC-MS/MS), residual DNA qPCR",
        "physicochemical": "DSC, CD spectroscopy, HDX-MS",
    }
    return suggestions.get(category, "alternative analytical method")
