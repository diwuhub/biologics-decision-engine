"""
Conservative Policy Layer (P0-4).

Implements regulatory conservatism guardrails:
  - Downgrade confidence when normative basis weak
  - Flag cross-agency conflicts for human review
  - Cap escalation when only concern patterns (no normative/precedent)

These rules are applied AFTER action_recommender, BEFORE package aggregation.
They prevent over-confident escalations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def apply_conservative_downgrade(
    recommendation: Dict[str, Any],
    matched_refs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Downgrade confidence if normative basis is weak.

    Rule: If (count of normative refs < 2) AND (count of precedent refs < 1),
    then reduce confidence by 0.2.
    """
    if not recommendation:
        return recommendation

    # Count by type
    normative_count = sum(1 for ref in matched_refs if ref.get('type') == 'normative')
    precedent_count = sum(1 for ref in matched_refs if ref.get('type') == 'precedent')

    # Apply downgrade
    if normative_count < 2 and precedent_count < 1:
        original_confidence = recommendation.get('confidence', 0.5)
        recommendation['confidence'] = max(0.0, original_confidence - 0.2)
        recommendation['_confidence_downgrade_applied'] = True
        recommendation['_downgrade_reason'] = (
            f"Weak normative basis: {normative_count} normative, {precedent_count} precedent"
        )

    return recommendation


def check_conflict_flag(
    matched_refs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Detect cross-agency conflicts and flag for human review.

    Rule: If two or more refs from different agencies (FDA, EMA, ICH)
    have conflicting conclusions on the same topic,
    set human_review_required=True.
    """
    if not matched_refs or len(matched_refs) < 2:
        return {
            'conflict_detected': False,
            'conflicting_agencies': [],
            'human_review_required': False,
        }

    # Group by agency and conclusion
    by_agency_conclusion = {}
    for ref in matched_refs:
        agency = ref.get('agency', 'unknown')
        conclusion = ref.get('conclusion', 'neutral')
        key = (agency, conclusion)
        if key not in by_agency_conclusion:
            by_agency_conclusion[key] = []
        by_agency_conclusion[key].append(ref)

    # Check for conflicting conclusions across agencies
    agencies_seen = set()
    conclusions_by_agency = {}

    for (agency, conclusion), refs in by_agency_conclusion.items():
        if agency not in conclusions_by_agency:
            conclusions_by_agency[agency] = set()
        conclusions_by_agency[agency].add(conclusion)
        agencies_seen.add(agency)

    # Conflict if one agency says "approve" and another says "investigate"
    conflict_detected = False
    conflicting_agencies = []

    if len(agencies_seen) >= 2:
        all_conclusions = set()
        for concl_set in conclusions_by_agency.values():
            all_conclusions.update(concl_set)

        # Conflicting if we have both "approve"/"proceed" and "investigate"/"defer"
        positive_conclusions = {'approve', 'proceed', 'comparable'}
        negative_conclusions = {'defer', 'investigate', 'not_comparable'}

        has_positive = bool(all_conclusions & positive_conclusions)
        has_negative = bool(all_conclusions & negative_conclusions)

        if has_positive and has_negative:
            conflict_detected = True
            conflicting_agencies = sorted(agencies_seen)

    return {
        'conflict_detected': conflict_detected,
        'conflicting_agencies': conflicting_agencies,
        'human_review_required': conflict_detected,
    }


def cap_escalation(
    recommendation: Dict[str, Any],
    support_types: List[str],
) -> Dict[str, Any]:
    """Cap escalation if only concern-pattern support (no normative/precedent).

    Rule: If support_types contains only 'concern_pattern' (and no 'normative'
    or 'precedent'), cap action_level at SUPPLEMENT.
    Never escalate to INVESTIGATE/DEFER on concern patterns alone.
    """
    if not recommendation:
        return recommendation

    # Check if ONLY concern patterns
    has_normative = 'normative' in support_types
    has_precedent = 'precedent' in support_types
    has_concern_pattern = 'concern_pattern' in support_types
    only_concern = has_concern_pattern and not has_normative and not has_precedent

    if only_concern:
        action_level = recommendation.get('action_level', 'PROCEED')

        # Cap at SUPPLEMENT
        if action_level in ('INVESTIGATE', 'DEFER'):
            recommendation['action_level'] = 'SUPPLEMENT'
            recommendation['_escalation_capped'] = True
            recommendation['_cap_reason'] = (
                'Escalation capped: only concern-pattern support, no normative/precedent'
            )

    return recommendation


def apply_conservative_policy(
    recommendation: Dict[str, Any],
    matched_refs: List[Dict[str, Any]],
    support_types: List[str] = None,
) -> Dict[str, Any]:
    """Apply all conservative policy rules in sequence.

    Called after action_recommender, before package aggregation.
    """
    if support_types is None:
        support_types = []

    # Apply rules in order
    recommendation = apply_conservative_downgrade(recommendation, matched_refs)
    conflict_info = check_conflict_flag(matched_refs)
    if conflict_info['human_review_required']:
        recommendation['human_review_required'] = True
        recommendation['_conflict_reason'] = f"Cross-agency conflict: {conflict_info['conflicting_agencies']}"
    recommendation = cap_escalation(recommendation, support_types)
    # [PATCH 4] v1.1 fallback rules
    recommendation = apply_fallback_rules(recommendation, matched_refs, support_types)

    return recommendation


# ---------------------------------------------------------------
# [PATCH 4] No-Precedent and Weak-Authority Fallback Rules (v1.1)
# ---------------------------------------------------------------

def apply_fallback_rules(
    recommendation: Dict[str, Any],
    matched_refs: List[Dict[str, Any]],
    support_types: List[str],
) -> Dict[str, Any]:
    """Apply v1.1 Patch 4 fallback rules for edge cases.

    Rule 4a -- No-precedent fallback:
        Absence of precedent alone does NOT force deferral.
        If normative >=1 primary-tier AND user evidence sufficient -> proceed
        with reduced confidence and human-review flag.

    Rule 4b -- Concern-pattern ceiling:
        Concern Pattern refs alone CANNOT escalate beyond SUPPLEMENT_REQUIRED.
        (Already enforced by cap_escalation, but explicitly annotated here.)

    Rule 4c -- Contextual-only downgrade:
        When ALL supporting refs are contextual-tier (no primary/strong_secondary):
        (1) reduce confidence by >=0.15
        (2) add 'weak_authority' flag
        (3) surface human-review recommendation
    """
    if not recommendation or not matched_refs:
        return recommendation

    # Classify authority tiers
    tiers = [ref.get('authority_quality_tier', 'contextual') for ref in matched_refs]
    has_primary = 'primary' in tiers
    has_strong_secondary = 'strong_secondary' in tiers
    all_contextual = all(t == 'contextual' for t in tiers) if tiers else True

    has_precedent = 'precedent' in support_types or 'Precedent' in support_types
    has_normative = 'normative' in support_types or 'Normative' in support_types

    # Rule 4a: No-precedent fallback
    if not has_precedent and has_normative and has_primary:
        # Don't force deferral, but flag
        recommendation.setdefault('_flags', []).append('no_precedent_but_normative_strong')
        recommendation['human_review_required'] = True
        original = recommendation.get('confidence', 0.5)
        recommendation['confidence'] = max(0.0, original - 0.10)
        recommendation['_no_precedent_note'] = (
            'No precedent found. Normative basis strong (primary-tier). '
            'Confidence reduced by 0.10. Human review recommended.'
        )

    # Rule 4c: Contextual-only downgrade
    if all_contextual and len(tiers) > 0:
        original = recommendation.get('confidence', 0.5)
        recommendation['confidence'] = max(0.0, original - 0.15)
        recommendation.setdefault('_flags', []).append('weak_authority')
        recommendation['human_review_required'] = True
        recommendation['_contextual_only_note'] = (
            'All supporting references are contextual-tier. '
            'Confidence reduced by 0.15. Human review required.'
        )

    return recommendation
