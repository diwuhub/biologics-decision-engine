"""
Cluster Builder Service — Constructs RiskClusters from CaseContext and
attribute-level assessment results.

Cluster Formation Policy:
  1. Primary: one ``category_risk`` cluster per analytical category.
  2. CQA escalation: CQA attributes with concern_level >= major break
     into a separate ``cqa_concern`` cluster.
  3. Single-attribute critical: concern_level = critical gets its own
     ``single_attribute_critical`` cluster.
  4. Cross-category gap: 2+ categories sharing the same gap type produce
     an additional ``cross_category_gap`` cluster.
  5. risk_semantics assignment: derived from cluster_type +
     base_concern_level + contains_cqa + identified_gaps.

Every cluster MUST have non-empty cluster_reason_summary and risk_semantics.

Step 0A: Judgment Core Refactor.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from schemas.case_context import CaseContext
from schemas.risk_cluster import RiskCluster


# =========================================================================
# W2-2b: Cross-Attribute Pair Config Loader
# =========================================================================

_CROSS_ATTRIBUTE_PAIRS: Optional[List[Dict]] = None


def _load_cross_attribute_pairs() -> List[Dict]:
    """Load cross_attribute_pairs.yaml (cached after first load)."""
    global _CROSS_ATTRIBUTE_PAIRS
    if _CROSS_ATTRIBUTE_PAIRS is not None:
        return _CROSS_ATTRIBUTE_PAIRS
    if not _HAS_YAML:
        _CROSS_ATTRIBUTE_PAIRS = []
        return _CROSS_ATTRIBUTE_PAIRS
    config_path = Path(__file__).parent.parent / "config" / "cross_attribute_pairs.yaml"
    if config_path.exists():
        with open(config_path) as f:
            data = _yaml.safe_load(f) or {}
        _CROSS_ATTRIBUTE_PAIRS = data.get("pairs", [])
    else:
        _CROSS_ATTRIBUTE_PAIRS = []
    return _CROSS_ATTRIBUTE_PAIRS


def _generate_cluster_id(prefix: str = "CLU") -> str:
    """Generate a unique cluster identifier."""
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _determine_risk_semantics(
    cluster_type: str,
    base_concern_level: str,
    contains_cqa: bool,
    gaps: List[str],
    attribute_results: Optional[List[Dict[str, Any]]] = None,
    attr_ids: Optional[List[str]] = None,
) -> str:
    """Derive risk_semantics from cluster formation inputs.

    Mapping logic:
    - contradiction in attribute results -> 'contradiction'
    - 'orthogonal_gap' in gaps + CQA -> 'orthogonal_gap'
    - any gap type present -> 'assay_gap'
    - favorable shift detected -> 'favorable_shift_requires_rationale'
    - trend detected -> 'trend_requires_monitoring'
    - cross_category_gap cluster type -> 'pattern_concern_only' or gap-based
    - no precedent context -> 'no_precedent_low_confidence'
    - default with no concerns -> 'sufficient_evidence'
    """
    # Check for contradiction in attribute-level results.
    if attribute_results and attr_ids:
        relevant = [
            r for r in attribute_results if r.get("attribute_id") in attr_ids
        ]
        for r in relevant:
            if r.get("contradiction_present", False):
                return "contradiction"
            if r.get("favorable_shift", False):
                return "favorable_shift_requires_rationale"
            if r.get("trend_detected", False):
                return "trend_requires_monitoring"

    # Cross-category gap cluster gets gap-based semantics.
    if cluster_type == "cross_category_gap":
        if any("orthogonal" in g for g in gaps):
            return "orthogonal_gap"
        if gaps:
            return "assay_gap"
        return "pattern_concern_only"

    # CQA concern with orthogonal gap.
    if contains_cqa and any("orthogonal" in g for g in gaps):
        return "orthogonal_gap"

    # Pattern-only gap -> pattern_concern_only semantics.
    if gaps and all("pattern" in g for g in gaps):
        return "pattern_concern_only"

    # General gap-based semantics.
    if gaps:
        if any("orthogonal" in g for g in gaps):
            return "orthogonal_gap"
        return "assay_gap"

    # Critical concern without gaps -> contradiction or single_attribute_critical.
    if base_concern_level == "critical":
        return "contradiction"

    # Default: sufficient evidence.
    return "sufficient_evidence"


def _generate_reason_summary(
    cluster_type: str,
    dominant_category: str,
    attr_ids: List[str],
    contains_cqa: bool,
    base_concern_level: str,
    risk_semantics: str,
    gaps: List[str],
) -> str:
    """Generate a human-readable cluster_reason_summary."""
    n_attrs = len(attr_ids)
    cqa_note = " (includes CQA)" if contains_cqa else ""

    if cluster_type == "single_attribute_critical":
        return (
            f"Critical-level concern on attribute {attr_ids[0]} in "
            f"{dominant_category}{cqa_note}. Requires immediate investigation. "
            f"Risk semantic: {risk_semantics}."
        )
    elif cluster_type == "cqa_concern":
        return (
            f"CQA escalation in {dominant_category}: {n_attrs} attribute(s) "
            f"with concern >= major{cqa_note}. "
            f"{'Gaps: ' + ', '.join(gaps) + '. ' if gaps else ''}"
            f"Risk semantic: {risk_semantics}."
        )
    elif cluster_type == "cross_category_gap":
        return (
            f"Cross-category gap cluster spanning {n_attrs} attribute(s). "
            f"Shared gap type(s): {', '.join(gaps) if gaps else 'unspecified'}. "
            f"Risk semantic: {risk_semantics}."
        )
    else:
        # category_risk
        return (
            f"Category-level risk assessment for {dominant_category}: "
            f"{n_attrs} attribute(s) assessed{cqa_note}. "
            f"Base concern: {base_concern_level}. "
            f"{'Gaps: ' + ', '.join(gaps) + '. ' if gaps else ''}"
            f"Risk semantic: {risk_semantics}."
        )


def _compute_base_cluster_score(
    attribute_results: List[Dict[str, Any]],
    attr_ids: List[str],
    contains_cqa: bool,
) -> float:
    """Compute CQA-weighted average of attribute scores.

    CQA attributes receive 1.5x weight.
    """
    relevant = [r for r in attribute_results if r.get("attribute_id") in attr_ids]
    if not relevant:
        return 0.0

    total_weight = 0.0
    weighted_sum = 0.0
    for r in relevant:
        score = float(r.get("score", 0.0))
        is_cqa = r.get("is_cqa", False)
        weight = 1.5 if is_cqa else 1.0
        weighted_sum += score * weight
        total_weight += weight

    return weighted_sum / total_weight if total_weight > 0 else 0.0


def build_risk_clusters(
    case_context: CaseContext,
    attribute_results: List[Dict[str, Any]],
) -> List[RiskCluster]:
    """Build RiskClusters from CaseContext and attribute-level results.

    Args:
        case_context: The immutable case context.
        attribute_results: List of attribute assessment dicts. Each dict
            should contain at minimum:
            - attribute_id (str)
            - category (str): analytical category
            - concern_level (str): none/minor/major/critical
            - is_cqa (bool): whether this is a Critical Quality Attribute
            - score (float): numeric score for weighting
            Optional:
            - gaps (List[str]): evidence gaps for this attribute
            - contradiction_present (bool)
            - favorable_shift (bool)
            - trend_detected (bool)

    Returns:
        List of RiskCluster objects with identity fields and
        base_cluster_score populated.
    """
    clusters: List[RiskCluster] = []

    # Group attributes by category.
    by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for attr in attribute_results:
        cat = attr.get("category", "unknown")
        by_category[cat].append(attr)

    # Track attributes already escalated to dedicated clusters.
    escalated_attr_ids: set = set()

    # --- Pass 1: Single-attribute critical clusters ---
    for cat, attrs in by_category.items():
        for attr in attrs:
            if attr.get("concern_level") == "critical":
                attr_id = attr["attribute_id"]
                attr_gaps = attr.get("gaps", [])
                # Intersect with case-level gaps.
                all_gaps = list(set(attr_gaps) | (
                    set(case_context.identified_gaps)
                    if attr_id in case_context.flagged_attribute_ids
                    else set()
                ))
                is_cqa = attr.get("is_cqa", False)

                risk_sem = _determine_risk_semantics(
                    "single_attribute_critical", "critical", is_cqa,
                    all_gaps, attribute_results, [attr_id],
                )
                reason = _generate_reason_summary(
                    "single_attribute_critical", cat, [attr_id],
                    is_cqa, "critical", risk_sem, all_gaps,
                )
                score = _compute_base_cluster_score(
                    attribute_results, [attr_id], is_cqa,
                )

                clusters.append(RiskCluster(
                    cluster_id=_generate_cluster_id("CRIT"),
                    cluster_type="single_attribute_critical",
                    dominant_category=cat,
                    affected_attribute_ids=[attr_id],
                    contains_cqa=is_cqa,
                    base_concern_level="critical",
                    cluster_reason_summary=reason,
                    risk_semantics=risk_sem,
                    base_cluster_score=score,
                ))
                escalated_attr_ids.add(attr_id)

    # --- Pass 2: CQA escalation clusters ---
    for cat, attrs in by_category.items():
        cqa_major = [
            a for a in attrs
            if a.get("is_cqa", False)
            and a.get("concern_level") in ("major", "critical")
            and a["attribute_id"] not in escalated_attr_ids
        ]
        if cqa_major:
            attr_ids = [a["attribute_id"] for a in cqa_major]
            attr_gaps = []
            for a in cqa_major:
                attr_gaps.extend(a.get("gaps", []))
            # Merge with case-level identified gaps for flagged attributes.
            for aid in attr_ids:
                if aid in case_context.flagged_attribute_ids:
                    attr_gaps.extend(case_context.identified_gaps)
            all_gaps = list(set(attr_gaps))

            max_concern = "critical" if any(
                a.get("concern_level") == "critical" for a in cqa_major
            ) else "major"

            risk_sem = _determine_risk_semantics(
                "cqa_concern", max_concern, True,
                all_gaps, attribute_results, attr_ids,
            )
            reason = _generate_reason_summary(
                "cqa_concern", cat, attr_ids, True,
                max_concern, risk_sem, all_gaps,
            )
            score = _compute_base_cluster_score(
                attribute_results, attr_ids, True,
            )

            clusters.append(RiskCluster(
                cluster_id=_generate_cluster_id("CQA"),
                cluster_type="cqa_concern",
                dominant_category=cat,
                affected_attribute_ids=attr_ids,
                contains_cqa=True,
                base_concern_level=max_concern,
                cluster_reason_summary=reason,
                risk_semantics=risk_sem,
                base_cluster_score=score,
            ))
            escalated_attr_ids.update(attr_ids)

    # --- Pass 3: Primary category_risk clusters ---
    for cat, attrs in by_category.items():
        remaining = [
            a for a in attrs if a["attribute_id"] not in escalated_attr_ids
        ]
        if not remaining:
            continue

        attr_ids = [a["attribute_id"] for a in remaining]
        contains_cqa = any(a.get("is_cqa", False) for a in remaining)

        # Base concern: highest among remaining attributes.
        concern_order = {"none": 0, "minor": 1, "major": 2, "critical": 3}
        max_concern = max(
            remaining,
            key=lambda a: concern_order.get(a.get("concern_level", "none"), 0),
        )
        base_concern = max_concern.get("concern_level", "none")

        attr_gaps = []
        for a in remaining:
            attr_gaps.extend(a.get("gaps", []))
        for aid in attr_ids:
            if aid in case_context.flagged_attribute_ids:
                attr_gaps.extend(case_context.identified_gaps)
        all_gaps = list(set(attr_gaps))

        risk_sem = _determine_risk_semantics(
            "category_risk", base_concern, contains_cqa,
            all_gaps, attribute_results, attr_ids,
        )
        reason = _generate_reason_summary(
            "category_risk", cat, attr_ids, contains_cqa,
            base_concern, risk_sem, all_gaps,
        )
        score = _compute_base_cluster_score(
            attribute_results, attr_ids, contains_cqa,
        )

        clusters.append(RiskCluster(
            cluster_id=_generate_cluster_id("CAT"),
            cluster_type="category_risk",
            dominant_category=cat,
            affected_attribute_ids=attr_ids,
            contains_cqa=contains_cqa,
            base_concern_level=base_concern,
            cluster_reason_summary=reason,
            risk_semantics=risk_sem,
            base_cluster_score=score,
        ))

    # --- Pass 4: Cross-category gap clusters ---
    # Detect 2+ categories sharing the same gap type.
    gap_to_categories: Dict[str, List[str]] = defaultdict(list)
    gap_to_attr_ids: Dict[str, List[str]] = defaultdict(list)

    for cat, attrs in by_category.items():
        for a in attrs:
            for g in a.get("gaps", []):
                if cat not in gap_to_categories[g]:
                    gap_to_categories[g].append(cat)
                gap_to_attr_ids[g].append(a["attribute_id"])

    for gap_type, cats in gap_to_categories.items():
        if len(cats) >= 2:
            attr_ids = list(set(gap_to_attr_ids[gap_type]))
            contains_cqa = any(
                a.get("is_cqa", False)
                for a in attribute_results
                if a["attribute_id"] in attr_ids
            )
            risk_sem = _determine_risk_semantics(
                "cross_category_gap", "minor", contains_cqa,
                [gap_type], attribute_results, attr_ids,
            )
            reason = _generate_reason_summary(
                "cross_category_gap", ", ".join(cats), attr_ids,
                contains_cqa, "minor", risk_sem, [gap_type],
            )
            score = _compute_base_cluster_score(
                attribute_results, attr_ids, contains_cqa,
            )

            clusters.append(RiskCluster(
                cluster_id=_generate_cluster_id("XCAT"),
                cluster_type="cross_category_gap",
                dominant_category=cats[0],
                affected_attribute_ids=attr_ids,
                contains_cqa=contains_cqa,
                base_concern_level="minor",
                cluster_reason_summary=reason,
                risk_semantics=risk_sem,
                base_cluster_score=score,
            ))

    # --- Pass 4b: W2-2b Cross-Attribute Pair-Based Linked Escalation ---
    # When BOTH attributes in a known pair are shifted (concern >= minor),
    # escalate the pair's cluster concern by 1 step. Record pair_id in trace.
    # Escalation only — NO attenuation from pair independence.
    _cross_pair_config = _load_cross_attribute_pairs()
    _concern_order_map = {"none": 0, "minor": 1, "major": 2, "critical": 3}
    _concern_from_order_map = {0: "none", 1: "minor", 2: "major", 3: "critical"}

    # Build a lookup: attribute_name (lowercased, keywords) -> concern_level
    _attr_concern_lookup: Dict[str, str] = {}
    _attr_category_lookup: Dict[str, str] = {}
    for a in attribute_results:
        _attr_concern_lookup[a["attribute_id"]] = a.get("concern_level", "none")
        _attr_category_lookup[a["attribute_id"]] = a.get("category", "unknown")

    _pair_escalations: List[Dict[str, Any]] = []
    for pair_def in _cross_pair_config:
        pair_id = pair_def.get("pair_id", "UNKNOWN")
        pair_keywords = pair_def.get("attributes", [])  # e.g. ['glycosylation', 'potency']
        if len(pair_keywords) != 2:
            continue

        # Find matching attributes by keyword in attribute_id or category
        matched_sides: List[List[str]] = [[], []]
        for a in attribute_results:
            aid_lower = a["attribute_id"].lower()
            acat_lower = a.get("category", "").lower()
            for side_idx, kw in enumerate(pair_keywords):
                kw_lower = kw.lower()
                if kw_lower in aid_lower or kw_lower in acat_lower:
                    matched_sides[side_idx].append(a["attribute_id"])

        # Both sides must have at least one shifted attribute (concern >= minor)
        side0_shifted = [
            aid for aid in matched_sides[0]
            if _concern_order_map.get(_attr_concern_lookup.get(aid, "none"), 0) >= 1
        ]
        side1_shifted = [
            aid for aid in matched_sides[1]
            if _concern_order_map.get(_attr_concern_lookup.get(aid, "none"), 0) >= 1
        ]

        if side0_shifted and side1_shifted:
            _pair_escalations.append({
                "pair_id": pair_id,
                "relationship": pair_def.get("relationship", ""),
                "both_shifted_message": pair_def.get("both_shifted", ""),
                "side0_attrs": side0_shifted,
                "side1_attrs": side1_shifted,
            })

    # Apply pair escalation to relevant clusters
    for esc in _pair_escalations:
        all_pair_attrs = set(esc["side0_attrs"] + esc["side1_attrs"])
        for cluster in clusters:
            cluster_attr_set = set(cluster.affected_attribute_ids)
            if cluster_attr_set & all_pair_attrs:
                # Escalate concern by 1 step
                current_concern = cluster.base_concern_level
                current_ord = _concern_order_map.get(current_concern, 0)
                new_ord = min(current_ord + 1, 3)
                new_concern = _concern_from_order_map[new_ord]
                if new_ord > current_ord:
                    cluster.base_concern_level = new_concern
                    # Record pair_id in evidence trace (S-4 invariant)
                    _pair_note = (
                        f"[cross_pair:{esc['pair_id']}] "
                        f"{esc['both_shifted_message']}"
                    )
                    cluster.cluster_reason_summary += f" {_pair_note}"
                    cluster.likely_reviewer_concerns.append(
                        f"[cross_pair:{esc['pair_id']}]"
                    )

    # --- Pass 5: CLUST-005 Trend Rule ---
    # Trend-only clusters capped at minor, non-blocking, enhanced_monitoring.
    for cluster in clusters:
        if cluster.risk_semantics == "trend_requires_monitoring":
            if cluster.base_concern_level in ("major", "critical"):
                cluster.concern_level = "minor"  # Cap per CLUST-005
            else:
                cluster.concern_level = cluster.base_concern_level
            cluster.package_blocking = False
            cluster.recommended_followup_type = "enhanced_monitoring"

    # --- Pass 6: Merge same-category contradiction clusters ---
    # When multiple clusters from the same category have contradiction
    # semantics, merge them into a single single_attribute_critical cluster.
    # This prevents over-counting blocking clusters for the same root cause
    # (e.g., two potency assays contradicting each other).
    # Only merges single_attribute_critical and category_risk clusters;
    # leaves cross_category_gap and cqa_concern cluster types intact.
    _MERGEABLE_TYPES = frozenset({"single_attribute_critical", "category_risk"})
    _merged: List[RiskCluster] = []
    _cat_contradiction_groups: Dict[str, List[RiskCluster]] = defaultdict(list)
    for cluster in clusters:
        if (
            cluster.cluster_type in _MERGEABLE_TYPES
            and cluster.risk_semantics == "contradiction"
            and cluster.dominant_category
        ):
            _cat_contradiction_groups[cluster.dominant_category].append(cluster)
        else:
            _merged.append(cluster)

    for cat, group in _cat_contradiction_groups.items():
        if len(group) <= 1:
            _merged.extend(group)
        elif all(c.base_concern_level == "critical" for c in group):
            # All clusters are critical: keep separate so ABST-001 can
            # count them independently (e.g., GC-07 needs >=2 critical
            # contradiction clusters to trigger abstain).
            _merged.extend(group)
        else:
            # Mixed concern levels: merge into single cluster (e.g., GC-05
            # has one critical and one non-critical potency attribute).
            all_attr_ids: List[str] = []
            any_cqa = False
            max_concern_ord = 0
            best_score = 0.0
            for c in group:
                all_attr_ids.extend(c.affected_attribute_ids)
                any_cqa = any_cqa or c.contains_cqa
                concern_ord = {"none": 0, "minor": 1, "major": 2, "critical": 3}
                max_concern_ord = max(max_concern_ord, concern_ord.get(c.base_concern_level, 0))
                if c.base_cluster_score is not None:
                    best_score = max(best_score, c.base_cluster_score)

            concern_from_ord = {0: "none", 1: "minor", 2: "major", 3: "critical"}
            merged_concern = concern_from_ord[max_concern_ord]
            merged_attr_ids = list(dict.fromkeys(all_attr_ids))  # dedupe preserving order

            reason = _generate_reason_summary(
                "single_attribute_critical", cat, merged_attr_ids,
                any_cqa, merged_concern, "contradiction", [],
            )
            merged_cluster = RiskCluster(
                cluster_id=group[0].cluster_id,  # reuse first ID
                cluster_type="single_attribute_critical",
                dominant_category=cat,
                affected_attribute_ids=merged_attr_ids,
                contains_cqa=any_cqa,
                base_concern_level=merged_concern,
                cluster_reason_summary=reason,
                risk_semantics="contradiction",
                base_cluster_score=best_score if best_score > 0 else None,
            )
            _merged.append(merged_cluster)

    clusters = _merged

    return clusters
