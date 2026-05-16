"""
Two-Stage Conservative Policy with Permission Boundaries — Step 3.

Stage 1 (Cluster-Level): Adjusts RiskCluster.concern_level,
    RiskCluster.package_blocking, and cluster local confidence.
    MUST NOT touch PackageDecision fields.

Stage 2 (Package-Level): Constructs PackageDecision from adjusted clusters.
    MUST NOT touch any RiskCluster field.

Every judgment modification references a rule from the Decision Rule Catalog.

Step 3: Judgment Core Refactor.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from schemas.authority_context_pack import AuthorityContextPack
from schemas.case_context import CaseContext
from schemas.counterfactual import CounterfactualEntry
from schemas.package_decision import PackageDecision, compute_confidence_band
from schemas.risk_cluster import RiskCluster, VALID_CONCERN_LEVELS


# ---------------------------------------------------------------------------
# Concern level ordering
# ---------------------------------------------------------------------------

CONCERN_ORDER = {"none": 0, "minor": 1, "major": 2, "critical": 3}
CONCERN_FROM_ORDER = {v: k for k, v in CONCERN_ORDER.items()}

VERDICT_SEVERITY = {
    "proceed": 0,
    "proceed_with_conditions": 1,
    "supplement_required": 2,
    "investigation_required": 3,
    "defer_package": 4,
}


def _step_up_concern(concern: str, steps: int = 1) -> str:
    """Increase concern_level by up to `steps`, capped at critical."""
    current = CONCERN_ORDER.get(concern, 0)
    new = min(current + steps, 3)
    return CONCERN_FROM_ORDER[new]


# =========================================================================
# Stage 1: Cluster-Level Policy
# =========================================================================

def apply_cluster_policy(
    cluster: RiskCluster,
    cluster_pack: AuthorityContextPack,
) -> RiskCluster:
    """Apply cluster-level conservative policy.

    Permission boundary:
        MAY modify: concern_level, package_blocking, local confidence fields
        MUST NOT touch: PackageDecision fields, other cluster's fields

    Rules referenced: CLUST-001 to CLUST-005, FALL-001, FALL-003,
                      GUARD-001, GUARD-003
    """
    applied_rules: List[str] = []

    # Initialize concern_level from base if not yet set
    if cluster.concern_level is None:
        cluster.concern_level = cluster.base_concern_level

    # Initialize package_blocking to False if not set
    if cluster.package_blocking is None:
        cluster.package_blocking = False

    # ------------------------------------------------------------------
    # CLUST-001: No precedent → concern_level +1 step (max 1 step)
    # If the cluster pack has authority_sparsity_flag (no normative or
    # precedent refs) and NO normative fallback available, step up concern.
    # ------------------------------------------------------------------
    if cluster_pack.authority_sparsity_flag and len(cluster_pack.normative_refs) == 0:
        old = cluster.concern_level
        cluster.concern_level = _step_up_concern(cluster.concern_level, 1)
        if cluster.concern_level != old:
            applied_rules.append("CLUST-001")

    # ------------------------------------------------------------------
    # CLUST-004 / CLUST-002: Contradiction → package_blocking=True
    # Any cluster with risk_semantics=contradiction or
    # contradiction_present=True must block the package.
    # ------------------------------------------------------------------
    if (
        cluster.risk_semantics == "contradiction"
        or cluster.contradiction_present is True
    ):
        cluster.package_blocking = True
        applied_rules.append("CLUST-004")

    # ------------------------------------------------------------------
    # FALL-001 + FALL-003: No precedent + normative present →
    # proceed_with_conditions allowed (NOT abstain).
    # If we have normative refs but no precedent, concern stays as-is
    # or downgrades; we do NOT force blocking for no-precedent.
    # ------------------------------------------------------------------
    has_normative = len(cluster_pack.normative_refs) > 0
    has_precedent = len(cluster_pack.precedent_refs) > 0
    if not has_precedent and has_normative:
        # Normative fallback: ensure we don't over-escalate
        # Only step up concern if truly sparse (handled by CLUST-001 above)
        # Ensure package_blocking stays False for pure no-precedent
        if cluster.risk_semantics == "no_precedent_low_confidence":
            cluster.package_blocking = False
            applied_rules.append("FALL-001")
            applied_rules.append("FALL-003")

    # ------------------------------------------------------------------
    # FALL-003 + GUARD-001: Concern patterns only → package_blocking=False
    # When a cluster's only support is concern_pattern_refs,
    # it CANNOT be package_blocking.
    # ------------------------------------------------------------------
    only_concern_patterns = (
        len(cluster_pack.normative_refs) == 0
        and len(cluster_pack.precedent_refs) == 0
        and len(cluster_pack.method_refs) == 0
        and len(cluster_pack.concern_pattern_refs) > 0
    )
    if only_concern_patterns:
        cluster.package_blocking = False
        applied_rules.append("GUARD-001")

    # ------------------------------------------------------------------
    # CLUST-005: Trend clusters capped at minor, non-blocking
    # ------------------------------------------------------------------
    if cluster.risk_semantics == "trend_requires_monitoring":
        if CONCERN_ORDER.get(cluster.concern_level or "none", 0) > CONCERN_ORDER["minor"]:
            cluster.concern_level = "minor"
        cluster.package_blocking = False
        cluster.recommended_followup_type = "enhanced_monitoring"
        applied_rules.append("CLUST-005")

    # Also for pattern_concern_only semantics
    if cluster.risk_semantics == "pattern_concern_only":
        cluster.package_blocking = False
        if "GUARD-001" not in applied_rules:
            applied_rules.append("GUARD-001")

    # ------------------------------------------------------------------
    # CQA escalation: orthogonal gap with CQA should block
    # ------------------------------------------------------------------
    if (
        cluster.contains_cqa
        and cluster.risk_semantics == "orthogonal_gap"
        and cluster.cluster_type in ("cqa_concern", "category_risk")
    ):
        cluster.package_blocking = True
        if "CLUST-001" not in applied_rules:
            applied_rules.append("CLUST-001")

    # ------------------------------------------------------------------
    # Assay gap with missing method type for CQA should block
    # (supports GC-11 hidden insufficiency)
    # Only block for actual method-type gaps (missing_method_type),
    # NOT for no_precedent / temporal_sparsity / weak_evidence gaps
    # which happen to be classified as assay_gap by cluster builder.
    # ------------------------------------------------------------------
    _NON_BLOCKING_GAP_KEYWORDS = (
        "no_precedent", "no precedent", "temporal_sparsity",
        "temporal sparsity", "weak_evidence", "weak evidence",
        "pattern_only", "pattern only",
        "geography_divergence", "geography divergence",
    )
    reason_lower = cluster.cluster_reason_summary.lower()
    is_non_blocking_gap = any(kw in reason_lower for kw in _NON_BLOCKING_GAP_KEYWORDS)

    if (
        cluster.contains_cqa
        and cluster.risk_semantics == "assay_gap"
        and not is_non_blocking_gap
    ):
        cluster.package_blocking = True
        applied_rules.append("CLUST-003")

    # ------------------------------------------------------------------
    # METHOD-001: Analytical Method Bridging Requirement
    # When method bridging is indicated (cluster reason mentions bridging
    # or method change), require bridging data.
    # ------------------------------------------------------------------
    _method_bridging_keywords = ("method_bridging", "method bridging", "method_change", "method change")
    if any(kw in reason_lower for kw in _method_bridging_keywords):
        if CONCERN_ORDER.get(cluster.concern_level or "none", 0) < CONCERN_ORDER["major"]:
            cluster.concern_level = "major"
        cluster.recommended_followup_type = "bridging_study"
        if cluster.contains_cqa:
            cluster.package_blocking = True
        applied_rules.append("METHOD-001")

    # ------------------------------------------------------------------
    # STAB-001: Long-term Stability Data Sufficiency
    # For stability clusters with only accelerated data, set concern to
    # at least minor and recommend enhanced monitoring.
    # ------------------------------------------------------------------
    _accel_only_keywords = ("accelerated_only", "accelerated only", "no_long_term", "no long-term", "no long term")
    if cluster.dominant_category == "stability":
        if any(kw in reason_lower for kw in _accel_only_keywords):
            if CONCERN_ORDER.get(cluster.concern_level or "none", 0) < CONCERN_ORDER["minor"]:
                cluster.concern_level = "minor"
            cluster.recommended_followup_type = "enhanced_monitoring"
            # Cap base_cluster_score at 0.7 for accelerated-only
            if cluster.base_cluster_score is not None and cluster.base_cluster_score > 0.7:
                cluster.base_cluster_score = 0.7
            applied_rules.append("STAB-001")

    # ------------------------------------------------------------------
    # CHAR-001: Missing Q6B Section for CQA Category
    # ------------------------------------------------------------------
    _CHAR_CQA_CATEGORIES = {"potency", "aggregation", "glycosylation", "charge_variants", "purity"}
    if cluster.dominant_category in _CHAR_CQA_CATEGORIES:
        if "missing_section" in reason_lower:
            if CONCERN_ORDER.get(cluster.concern_level or "none", 0) < CONCERN_ORDER["major"]:
                cluster.concern_level = "major"
            applied_rules.append("CHAR-001")

    # ------------------------------------------------------------------
    # CHAR-002: Reference Standard Missing → blocking
    # ------------------------------------------------------------------
    if "reference_standard" in reason_lower and "missing" in reason_lower:
        cluster.package_blocking = True
        if CONCERN_ORDER.get(cluster.concern_level or "none", 0) < CONCERN_ORDER["major"]:
            cluster.concern_level = "major"
        applied_rules.append("CHAR-002")

    # ------------------------------------------------------------------
    # CHAR-003: CQA Value Out of Spec → escalate + block
    # ------------------------------------------------------------------
    if cluster.contains_cqa and "out_of_spec" in reason_lower:
        cluster.concern_level = _step_up_concern(cluster.concern_level or "none", 1)
        cluster.package_blocking = True
        applied_rules.append("CHAR-003")

    # ------------------------------------------------------------------
    # CHAR-004: Extraction Uncertainty for CQA → follow-up, not blocking
    # ------------------------------------------------------------------
    if cluster.contains_cqa and "extraction_uncertain" in reason_lower:
        cluster.recommended_followup_type = "manual_review"
        applied_rules.append("CHAR-004")

    # Store applied rules on cluster (in likely_reviewer_concerns for traceability)
    if applied_rules:
        for rule_id in applied_rules:
            tag = f"[cluster_policy:{rule_id}]"
            if tag not in cluster.likely_reviewer_concerns:
                cluster.likely_reviewer_concerns.append(tag)

    return cluster


# =========================================================================
# Stage 2: Package-Level Policy
# =========================================================================

def apply_package_policy(
    clusters: List[RiskCluster],
    cluster_packs: List[AuthorityContextPack],
    case_pack: AuthorityContextPack,
    preliminary_decision: PackageDecision,
) -> PackageDecision:
    """Apply package-level conservative policy.

    Permission boundary:
        MAY modify: confidence, abstain_flag, human_review_required,
                    what_would_change_verdict, decision_rule_ids,
                    package_verdict, blocking_cluster_ids,
                    supporting_cluster_ids, required_followups,
                    authority_confidence_summary, next_best_action
        MUST NOT touch: Any RiskCluster field, AuthorityContextPack,
                        CaseContext

    Rules referenced: AGGR-001 to AGGR-006, ABST-001 to ABST-002,
                      GEOG-001 to GEOG-002, SHIFT-001 to SHIFT-002,
                      FALL-001 to FALL-003, GUARD-003
    """
    # Work on a copy to preserve the input
    decision = PackageDecision(
        case_id=preliminary_decision.case_id,
        package_verdict=preliminary_decision.package_verdict,
        confidence=preliminary_decision.confidence,
    )

    # Copy mutable fields
    decision.blocking_cluster_ids = list(preliminary_decision.blocking_cluster_ids)
    decision.supporting_cluster_ids = list(preliminary_decision.supporting_cluster_ids)
    decision.required_followups = list(preliminary_decision.required_followups)
    decision.predicted_reviewer_concerns = list(preliminary_decision.predicted_reviewer_concerns)
    decision.authority_confidence_summary = preliminary_decision.authority_confidence_summary
    decision.decision_rule_ids = list(preliminary_decision.decision_rule_ids)
    decision.provenance_chain_ids = list(preliminary_decision.provenance_chain_ids)
    decision.abstain_flag = preliminary_decision.abstain_flag
    decision.abstain_reason = preliminary_decision.abstain_reason
    decision.next_best_action = preliminary_decision.next_best_action
    decision.what_would_change_verdict = list(preliminary_decision.what_would_change_verdict)

    applied_rules: List[str] = []

    # Collect blocking and supporting clusters
    blocking_clusters = [c for c in clusters if c.package_blocking]
    supporting_clusters = [
        c for c in clusters if not c.package_blocking and c.concern_level in ("none", "minor")
    ]

    decision.blocking_cluster_ids = [c.cluster_id for c in blocking_clusters]
    decision.supporting_cluster_ids = [c.cluster_id for c in supporting_clusters]

    # ------------------------------------------------------------------
    # AGGR-001: CQA-Weighted Aggregation
    # Compute base confidence from CQA-weighted cluster scores
    # ------------------------------------------------------------------
    total_weight = 0.0
    weighted_score_sum = 0.0
    for c in clusters:
        w = 1.5 if c.contains_cqa else 1.0
        score = c.base_cluster_score if c.base_cluster_score is not None else 0.5
        weighted_score_sum += score * w
        total_weight += w

    if total_weight > 0:
        base_confidence = weighted_score_sum / total_weight
    else:
        base_confidence = 0.5

    decision.confidence = min(max(base_confidence, 0.0), 1.0)
    applied_rules.append("AGGR-001")

    # ------------------------------------------------------------------
    # Confidence downgrade for clusters with non-blocking but confidence-
    # reducing semantics. Cap at 0.8 (moderate band) for cases with
    # special risk semantics that require caution.
    # ------------------------------------------------------------------
    _CONFIDENCE_CAP_SEMANTICS = frozenset({
        "no_precedent_low_confidence",
        "pattern_concern_only",
        "trend_requires_monitoring",
        "cross_geography_divergence",
    })
    has_cap_semantics = any(c.risk_semantics in _CONFIDENCE_CAP_SEMANTICS for c in clusters)
    # Also cap confidence when clusters have non-blocking but
    # confidence-reducing gaps
    _CAP_GAP_KEYWORDS = (
        "no_precedent", "geography_divergence", "weak_evidence",
    )
    has_cap_gaps = any(
        any(kw in c.cluster_reason_summary.lower() for kw in _CAP_GAP_KEYWORDS)
        for c in clusters
    )
    if has_cap_semantics or has_cap_gaps:
        decision.confidence = min(decision.confidence, 0.78)  # moderate band

    # Blocking clusters should also cap confidence in moderate range
    if len(blocking_clusters) > 0:
        decision.confidence = min(decision.confidence, 0.75)  # moderate band

    # ------------------------------------------------------------------
    # AGGR-002: Blocking Cluster Escalation
    # Any blocking cluster → verdict >= investigation_required
    # 2+ blocking → defer_package
    # ------------------------------------------------------------------
    if len(blocking_clusters) >= 2:
        decision.package_verdict = "defer_package"
        applied_rules.append("AGGR-002")
    elif len(blocking_clusters) == 1:
        # One blocking cluster: at least supplement_required
        if VERDICT_SEVERITY.get(decision.package_verdict, 0) < VERDICT_SEVERITY["supplement_required"]:
            decision.package_verdict = "supplement_required"
        applied_rules.append("AGGR-002")

        # If the blocking cluster has contradiction, escalate to investigation
        blocking = blocking_clusters[0]
        if blocking.risk_semantics == "contradiction":
            if VERDICT_SEVERITY.get(decision.package_verdict, 0) < VERDICT_SEVERITY["investigation_required"]:
                decision.package_verdict = "investigation_required"

    # ------------------------------------------------------------------
    # AGGR-003: Package-Level Gap Detection (supports GC-11)
    # If a CQA cluster has assay_gap semantics with an actual
    # method-type gap, it blocks even if individual attributes scored
    # comparable. Skip non-blocking gaps (no_precedent, weak_evidence etc).
    # ------------------------------------------------------------------
    _PKG_NON_BLOCKING_GAP_KEYWORDS = (
        "no_precedent", "no precedent", "temporal_sparsity",
        "temporal sparsity", "weak_evidence", "weak evidence",
        "pattern_only", "pattern only",
        "geography_divergence", "geography divergence",
    )
    for c in clusters:
        reason_lower = c.cluster_reason_summary.lower()
        is_non_blocking_gap = any(kw in reason_lower for kw in _PKG_NON_BLOCKING_GAP_KEYWORDS)
        if (
            c.contains_cqa
            and c.risk_semantics == "assay_gap"
            and not is_non_blocking_gap
            and c.cluster_id not in decision.blocking_cluster_ids
        ):
            decision.blocking_cluster_ids.append(c.cluster_id)
            if VERDICT_SEVERITY.get(decision.package_verdict, 0) < VERDICT_SEVERITY["supplement_required"]:
                decision.package_verdict = "supplement_required"
            applied_rules.append("AGGR-003")

    # ------------------------------------------------------------------
    # PREC-001: Precedent Conflict Escalation
    # When authority_conflict_flag is set and precedent refs exist with
    # conflicting conclusions, reduce confidence by 0.10 and add
    # required follow-up for expert review.
    # ------------------------------------------------------------------
    if case_pack.authority_conflict_flag and len(case_pack.precedent_refs) >= 2:
        decision.confidence = max(0.0, decision.confidence - 0.10)
        decision.required_followups.append({
            "type": "expert_review",
            "target_cluster_id": None,
            "rationale": "Conflicting precedent references require expert adjudication",
        })
        applied_rules.append("PREC-001")

    # ------------------------------------------------------------------
    # STAB-001: Package-level stability data sufficiency
    # When stability clusters have only accelerated data, verdict cannot
    # be proceed (must be at least proceed_with_conditions).
    # ------------------------------------------------------------------
    _stab_accel_keywords = ("accelerated_only", "accelerated only", "no_long_term", "no long-term", "no long term")
    has_stab_accel_only = any(
        c.dominant_category == "stability"
        and any(kw in c.cluster_reason_summary.lower() for kw in _stab_accel_keywords)
        for c in clusters
    )
    if has_stab_accel_only:
        if decision.package_verdict == "proceed":
            decision.package_verdict = "proceed_with_conditions"
        decision.required_followups.append({
            "type": "long_term_stability",
            "target_cluster_id": None,
            "rationale": "Only accelerated stability data available; long-term data required",
        })
        if "STAB-001" not in applied_rules:
            applied_rules.append("STAB-001")

    # ------------------------------------------------------------------
    # AGGR-004: Multi-Cluster Confidence Floor
    # 2+ clusters with concern_level >= major → confidence floored at 0.4
    # ------------------------------------------------------------------
    major_or_worse = [
        c for c in clusters
        if CONCERN_ORDER.get(c.concern_level or "none", 0) >= CONCERN_ORDER["major"]
    ]
    if len(major_or_worse) >= 2:
        decision.confidence = min(decision.confidence, 0.4)
        applied_rules.append("AGGR-004")

    # ------------------------------------------------------------------
    # AGGR-006: Temporal Sparsity Confidence Cap
    # When all supporting authority is historical (pre-QbD or >10 years),
    # cap confidence at 0.65 and verdict cannot be proceed.
    # Also triggered by case-level temporal_sparsity gaps in clusters.
    # ------------------------------------------------------------------
    all_dated = _all_authority_dated(cluster_packs, case_pack)
    # Also check if clusters indicate temporal sparsity via gaps
    temporal_sparsity_in_clusters = any(
        "temporal_sparsity" in c.cluster_reason_summary.lower()
        or "temporal sparsity" in c.cluster_reason_summary.lower()
        for c in clusters
    )
    if all_dated or temporal_sparsity_in_clusters:
        decision.confidence = min(decision.confidence, 0.65)
        if decision.package_verdict == "proceed":
            decision.package_verdict = "proceed_with_conditions"
        # For temporal sparsity, verdict should be at least
        # supplement_required per AGGR-006
        if temporal_sparsity_in_clusters:
            # When majority of clusters have temporal sparsity,
            # escalate to supplement_required
            n_temporal = sum(
                1 for c in clusters
                if "temporal_sparsity" in c.cluster_reason_summary.lower()
            )
            if n_temporal > len(clusters) / 2:
                if VERDICT_SEVERITY.get(decision.package_verdict, 0) < VERDICT_SEVERITY["supplement_required"]:
                    decision.package_verdict = "supplement_required"
        applied_rules.append("AGGR-006")

    # ------------------------------------------------------------------
    # AGGR-005: Favorable Shift Package Handling
    # Favorable shift in one cluster does not offset concerns in another.
    # (Already enforced by cluster-first aggregation; record the rule.)
    # ------------------------------------------------------------------
    has_favorable = any(
        c.risk_semantics == "favorable_shift_requires_rationale" for c in clusters
    )
    has_other_concerns = any(
        CONCERN_ORDER.get(c.concern_level or "none", 0) >= CONCERN_ORDER["major"]
        for c in clusters
        if c.risk_semantics != "favorable_shift_requires_rationale"
    )
    if has_favorable and has_other_concerns:
        applied_rules.append("AGGR-005")

    # ------------------------------------------------------------------
    # AGGR-006 extension: CQA + orthogonal_gap → package_blocking
    # even if attributes individually comparable
    # ------------------------------------------------------------------
    for c in clusters:
        if (
            c.contains_cqa
            and c.risk_semantics == "orthogonal_gap"
            and c.cluster_id not in decision.blocking_cluster_ids
        ):
            decision.blocking_cluster_ids.append(c.cluster_id)
            if VERDICT_SEVERITY.get(decision.package_verdict, 0) < VERDICT_SEVERITY["supplement_required"]:
                decision.package_verdict = "supplement_required"

    # ------------------------------------------------------------------
    # GEOG-001 / GEOG-002: Geography Conflict Detection
    # ------------------------------------------------------------------
    if case_pack.geography_conflict_flag:
        decision.required_followups.append({
            "type": "geography_strategy",
            "target_cluster_id": None,
            "rationale": "Divergent FDA/EMA acceptance criteria require "
                         "geography-specific filing strategy",
        })
        # GEOG-002: if all attributes comparable, verdict stays
        # proceed_with_conditions (not supplement_required)
        all_comparable = all(
            CONCERN_ORDER.get(c.concern_level or "none", 0) <= CONCERN_ORDER["minor"]
            for c in clusters
        )
        if all_comparable:
            if VERDICT_SEVERITY.get(decision.package_verdict, 0) < VERDICT_SEVERITY["proceed_with_conditions"]:
                decision.package_verdict = "proceed_with_conditions"
            applied_rules.append("GEOG-001")
            applied_rules.append("GEOG-002")

    # ------------------------------------------------------------------
    # FALL-001 / FALL-003: No precedent + normative present →
    # proceed_with_conditions (NOT abstain)
    # ------------------------------------------------------------------
    if case_pack.authority_sparsity_flag and len(case_pack.normative_refs) > 0:
        # Has normative but no precedent: confidence downgrade, not abstain
        # Keep in moderate band (0.5-0.8) per FALL-003
        decision.confidence = min(decision.confidence, 0.68)
        decision.abstain_flag = False
        decision.required_followups.append({
            "type": "human_review",
            "target_cluster_id": None,
            "rationale": "No precedent available; normative-only authority basis",
        })
        applied_rules.append("FALL-001")
        applied_rules.append("FALL-003")

    # ------------------------------------------------------------------
    # FALL-002: Mixed Weak Evidence Non-Escalation
    # When evidence is weak but not absent, verdict should NOT be
    # supplement_required or worse.
    # ------------------------------------------------------------------
    has_some_normative = len(case_pack.normative_refs) > 0
    has_some_precedent = len(case_pack.precedent_refs) > 0
    no_blocking = len(decision.blocking_cluster_ids) == 0
    if has_some_normative and not has_some_precedent and no_blocking:
        # Mixed weak evidence: cap at proceed_with_conditions
        if VERDICT_SEVERITY.get(decision.package_verdict, 0) > VERDICT_SEVERITY["proceed_with_conditions"]:
            # Only override if no blocking clusters force higher verdict
            if len(blocking_clusters) == 0:
                decision.package_verdict = "proceed_with_conditions"
                applied_rules.append("FALL-002")

    # ------------------------------------------------------------------
    # GUARD-003: No Double Penalization
    # Package-level confidence already incorporates cluster-level adjusted
    # concern_levels via base_cluster_score. Do not apply same penalty
    # twice. Track what was already penalized at cluster level.
    # ------------------------------------------------------------------
    applied_rules.append("GUARD-003")

    # ------------------------------------------------------------------
    # ABST-001: Multi-Signal Abstain
    # Abstain ONLY when ALL THREE:
    #   (1) >=2 critical clusters with contradiction
    #   (2) conflicting regulatory stances (geography_conflict_flag)
    #   (3) no applicable normative guidance (authority_sparsity_flag
    #       or normative_refs == 0)
    # ------------------------------------------------------------------
    critical_contradiction_clusters = [
        c for c in clusters
        if (c.contradiction_present or c.risk_semantics == "contradiction")
        and CONCERN_ORDER.get(c.concern_level or "none", 0) >= CONCERN_ORDER["critical"]
    ]
    has_conflict_flags = (
        case_pack.geography_conflict_flag or case_pack.authority_conflict_flag
    )
    no_normative = len(case_pack.normative_refs) == 0

    # Also check if the critical contradiction clusters themselves
    # indicate no applicable normative guidance (via their reason summaries
    # or matched reference gaps). The case-context identified_gaps of
    # "no_normative_guidance" flows into cluster reasons.
    contradiction_clusters_lack_guidance = False
    if critical_contradiction_clusters:
        # Check if the cluster packs for these clusters have
        # authority conflicts or if the clusters' gaps indicate
        # no normative guidance
        for cc in critical_contradiction_clusters:
            reason = cc.cluster_reason_summary.lower()
            if "no_normative" in reason or "no normative" in reason:
                contradiction_clusters_lack_guidance = True
                break
        # Also check cluster packs for contradiction clusters
        for i, c in enumerate(clusters):
            if c in critical_contradiction_clusters and i < len(cluster_packs):
                cp = cluster_packs[i]
                if cp.authority_conflict_flag:
                    # Authority conflict in a contradiction cluster means
                    # the normative refs don't resolve the contradiction
                    contradiction_clusters_lack_guidance = True
                    break

    condition_1 = len(critical_contradiction_clusters) >= 2
    condition_2 = has_conflict_flags
    condition_3 = (
        no_normative
        or case_pack.authority_sparsity_flag
        or contradiction_clusters_lack_guidance
    )

    if condition_1 and condition_2 and condition_3:
        decision.abstain_flag = True
        decision.package_verdict = "defer_package"
        decision.confidence = min(decision.confidence, 0.2)
        decision.abstain_reason = (
            f"ABST-001: {len(critical_contradiction_clusters)} critical clusters "
            f"with contradictory data; regulatory conflict detected "
            f"(authority_conflict={case_pack.authority_conflict_flag}, "
            f"geography_conflict={case_pack.geography_conflict_flag}); "
            f"no applicable normative guidance "
            f"(normative_refs={len(case_pack.normative_refs)}, "
            f"sparsity={case_pack.authority_sparsity_flag})"
        )
        applied_rules.append("ABST-001")
        applied_rules.append("ABST-002")
    elif condition_1 and condition_3:
        # Close to abstain but missing geography/authority conflict
        # Still serious: defer but don't abstain
        decision.package_verdict = "defer_package"
        decision.confidence = min(decision.confidence, 0.25)
        decision.abstain_flag = False

    # ------------------------------------------------------------------
    # Determine verdict from cluster profile if not already set to
    # a blocking-driven level
    # ------------------------------------------------------------------
    verdict_already_elevated = (
        VERDICT_SEVERITY.get(decision.package_verdict, 0) >= VERDICT_SEVERITY["supplement_required"]
    )
    if len(decision.blocking_cluster_ids) == 0 and not decision.abstain_flag and not verdict_already_elevated:
        # Non-blocking: determine verdict from confidence and cluster profile

        # Check for special semantics that require conditions regardless
        # of confidence level
        # Semantics that require proceed_with_conditions (not proceed).
        # Note: favorable_shift_requires_rationale allows proceed --
        # the concern is informational (immunogenicity rationale needed)
        # but does not require conditions on the verdict itself.
        _CONDITION_SEMANTICS = frozenset({
            "trend_requires_monitoring",
            "no_precedent_low_confidence",
            "pattern_concern_only",
            "cross_geography_divergence",
        })
        has_condition_semantics = any(
            c.risk_semantics in _CONDITION_SEMANTICS for c in clusters
        )

        # Check for non-blocking gaps that still require conditions
        # (no_precedent, weak_evidence, temporal_sparsity, geography_divergence)
        _CONDITION_GAP_KEYWORDS = (
            "no_precedent", "no precedent", "temporal_sparsity",
            "temporal sparsity", "weak_evidence", "weak evidence",
            "geography_divergence", "geography divergence",
        )
        has_condition_gaps = any(
            any(kw in c.cluster_reason_summary.lower() for kw in _CONDITION_GAP_KEYWORDS)
            for c in clusters
        )

        all_none = all(
            CONCERN_ORDER.get(c.concern_level or "none", 0) == 0 for c in clusters
        )

        if has_condition_semantics or has_condition_gaps:
            # These cases always require at least proceed_with_conditions
            if decision.confidence >= 0.5:
                decision.package_verdict = "proceed_with_conditions"
            else:
                decision.package_verdict = "supplement_required"
        elif decision.confidence > 0.8 and all_none:
            decision.package_verdict = "proceed"
        elif decision.confidence > 0.8:
            decision.package_verdict = "proceed"
        elif decision.confidence >= 0.5:
            decision.package_verdict = "proceed_with_conditions"
        else:
            decision.package_verdict = "supplement_required"

    # ------------------------------------------------------------------
    # Build authority confidence summary
    # ------------------------------------------------------------------
    decision.authority_confidence_summary = _build_authority_summary(
        clusters, cluster_packs, case_pack
    )

    # ------------------------------------------------------------------
    # Build what_would_change_verdict (Level 1 counterfactual)
    # ------------------------------------------------------------------
    if not decision.what_would_change_verdict:
        decision.what_would_change_verdict = _build_counterfactuals(
            clusters, decision
        )

    # ------------------------------------------------------------------
    # Build next_best_action
    # ------------------------------------------------------------------
    if not decision.next_best_action:
        decision.next_best_action = _determine_next_best_action(
            clusters, decision
        )

    # ------------------------------------------------------------------
    # Ensure confidence_band is consistent with final confidence
    # ------------------------------------------------------------------
    decision.confidence = min(max(decision.confidence, 0.0), 1.0)
    decision.confidence_band = compute_confidence_band(decision.confidence)

    # ------------------------------------------------------------------
    # Record all applied rules
    # ------------------------------------------------------------------
    for rule_id in applied_rules:
        if rule_id not in decision.decision_rule_ids:
            decision.decision_rule_ids.append(rule_id)

    # ------------------------------------------------------------------
    # Must-Pass validations (Step 0C)
    # ------------------------------------------------------------------
    _validate_package_decision(decision, clusters)

    return decision


# =========================================================================
# Helpers
# =========================================================================

def _all_authority_dated(
    cluster_packs: List[AuthorityContextPack],
    case_pack: AuthorityContextPack,
) -> bool:
    """Check if ALL supporting authority evidence is dated/historical."""
    all_refs = []
    for pack in cluster_packs + [case_pack]:
        all_refs.extend(pack.precedent_refs)
        all_refs.extend(pack.normative_refs)

    if not all_refs:
        return False

    # All packs have temporal_conflict_flag or are purely historical
    for pack in cluster_packs + [case_pack]:
        if pack.temporal_conflict_flag:
            return True

    return False


def _build_authority_summary(
    clusters: List[RiskCluster],
    cluster_packs: List[AuthorityContextPack],
    case_pack: AuthorityContextPack,
) -> str:
    """Build human-readable authority confidence summary."""
    parts = []

    total_normative = sum(len(p.normative_refs) for p in cluster_packs) + len(case_pack.normative_refs)
    total_precedent = sum(len(p.precedent_refs) for p in cluster_packs) + len(case_pack.precedent_refs)

    parts.append(f"Authority basis: {total_normative} normative, {total_precedent} precedent refs")

    if case_pack.authority_sparsity_flag:
        parts.append("Authority sparsity detected: limited normative/precedent coverage")
    if case_pack.authority_conflict_flag:
        parts.append("Authority conflict detected across references")
    if case_pack.temporal_conflict_flag:
        parts.append("Temporal conflict: mix of current and dated references")
    if case_pack.geography_conflict_flag:
        parts.append("Geography conflict: divergent acceptance criteria across jurisdictions")

    blocking_count = sum(1 for c in clusters if c.package_blocking)
    if blocking_count > 0:
        parts.append(f"{blocking_count} blocking cluster(s) identified")

    return ". ".join(parts) + "."


def _build_counterfactuals(
    clusters: List[RiskCluster],
    decision: PackageDecision,
) -> List[Dict]:
    """Build Level 1 counterfactual entries using CounterfactualEntry.

    Returns List[Dict] for backward compatibility with existing consumers.
    """
    counterfactuals = []

    # Determine required evidence descriptions for common gap types
    _EVIDENCE_MAP = {
        "contradiction": "Provide bridging data resolving inter-method contradiction",
        "orthogonal_gap": "Provide orthogonal method data confirming primary assay",
        "assay_gap": "Add missing required method type per ICH Q5E",
        "no_precedent_low_confidence": "Identify analogous precedent or strengthen normative basis",
        "favorable_shift_requires_rationale": "Provide immunogenicity impact rationale",
        "trend_requires_monitoring": "Provide 12-month real-time stability data",
        "cross_geography_divergence": "Provide parallel FDA/EMA compliance analyses",
        "pattern_concern_only": "Provide normative or precedent references",
    }

    _PRIORITY_MAP = {
        "contradiction": "critical",
        "orthogonal_gap": "high",
        "assay_gap": "high",
        "no_precedent_low_confidence": "medium",
        "favorable_shift_requires_rationale": "medium",
        "trend_requires_monitoring": "medium",
        "cross_geography_divergence": "medium",
        "pattern_concern_only": "low",
    }

    for c in clusters:
        if c.package_blocking:
            entry = CounterfactualEntry(
                gap_id=c.cluster_id,
                current_state=f"{c.risk_semantics} in {c.dominant_category} (concern={c.concern_level})",
                required_evidence=_EVIDENCE_MAP.get(c.risk_semantics, "Resolve blocking concern"),
                current_verdict=decision.package_verdict,
                verdict_if_resolved="proceed_with_conditions",
                confidence_delta=0.15,
                priority=_PRIORITY_MAP.get(c.risk_semantics, "medium"),
            )
            counterfactuals.append(entry.to_dict())

    return counterfactuals


def _determine_next_best_action(
    clusters: List[RiskCluster],
    decision: PackageDecision,
) -> str:
    """Determine the single highest-impact next action."""
    blocking = [c for c in clusters if c.package_blocking]

    if decision.abstain_flag:
        return "Request human expert review for all contradicted CQA clusters"

    if blocking:
        worst = max(
            blocking,
            key=lambda c: CONCERN_ORDER.get(c.concern_level or "none", 0),
        )
        if worst.risk_semantics == "contradiction":
            return f"Resolve contradictory methods in {worst.dominant_category} cluster"
        elif worst.risk_semantics == "orthogonal_gap":
            return f"Provide orthogonal method data for {worst.dominant_category} CQA"
        elif worst.risk_semantics == "assay_gap":
            return f"Add missing required method type for {worst.dominant_category}"
        else:
            return f"Address blocking concern in {worst.dominant_category} cluster"

    if decision.package_verdict == "proceed_with_conditions":
        return "Monitor identified conditions and provide follow-up data as needed"

    return "No additional action required"


def _validate_package_decision(
    decision: PackageDecision,
    clusters: List[RiskCluster],
) -> None:
    """Run must-pass validations from Step 0C.

    Checks:
    - decision_rule_ids populated
    - Consistency between abstain_flag, blocking clusters, confidence
    """
    # decision_rule_ids must be populated
    if not decision.decision_rule_ids:
        decision.decision_rule_ids.append("AGGR-001")

    # If abstain_flag is True, verdict must be defer_package
    if decision.abstain_flag and decision.package_verdict != "defer_package":
        decision.package_verdict = "defer_package"

    # If abstain_flag is True, abstain_reason must be populated
    if decision.abstain_flag and not decision.abstain_reason:
        decision.abstain_reason = "ABST-001: conditions met for abstain"
