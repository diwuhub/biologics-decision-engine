"""
Cluster-Aware Matcher — Step 2 of the Judgment Core Refactor.

Produces one AuthorityContextPack per RiskCluster plus one INDEPENDENT
case-level pack. Each cluster gets a DISTINCT pack (not shared). The
case-level pack is independently constructed -- NOT a union of cluster packs.

Scoring factors:
  1. Category match (cluster.dominant_category vs entry.applicable_categories)
  2. Change type match (case_ctx.change_type vs entry.applies_to_change_types)
  3. Molecule class match (case_ctx.molecule_class vs entry.applies_to_molecule_classes)
  4. Authority tier (primary > strong_secondary > contextual)
  5. Temporal weight (current > dated_but_informative > historical_only)
  6. CQA relevance bonus (if cluster.contains_cqa)
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Set, Tuple

from evidence_registry import EvidenceRegistry, RegistryEntry
from schemas.authority_context_pack import AuthorityContextPack, RefEntry
from schemas.case_context import CaseContext
from schemas.risk_cluster import RiskCluster


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

SCORE_WEIGHTS = {
    "category_match": 0.25,
    "change_type_match": 0.20,
    "molecule_class_match": 0.15,
    "authority_tier": 0.20,
    "temporal_weight": 0.10,
    "cqa_bonus": 0.10,
}

AUTHORITY_TIER_SCORES = {
    "primary": 1.0,
    "strong_secondary": 0.7,
    "contextual": 0.3,
}

TEMPORAL_SCORES = {
    "current": 1.0,
    "dated_but_informative": 0.5,
    "historical_only": 0.15,
}

# Decision type -> ref list category mapping
DECISION_TYPE_TO_REF_CATEGORY = {
    "Normative": "normative",
    "Precedent": "precedent",
    "Method": "method",
    "Concern Pattern": "concern_pattern",
}

# Selection limits per decision_type per pack
TYPE_LIMITS = {
    "Normative": 5,
    "Precedent": 3,
    "Method": 2,
    "Concern Pattern": 2,
}

# How many top_decision_drivers to include
TOP_DRIVERS_COUNT = 3


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------

def _score_entry_for_cluster(
    entry: RegistryEntry,
    cluster: RiskCluster,
    case_ctx: CaseContext,
) -> float:
    """Score a registry entry's relevance to a specific cluster."""
    scores: Dict[str, float] = {}

    # 1. Category match
    if cluster.dominant_category in entry.applicable_categories:
        scores["category_match"] = 1.0
    elif any(
        cat in entry.applicable_categories
        for cat in _related_categories(cluster.dominant_category)
    ):
        scores["category_match"] = 0.5
    else:
        scores["category_match"] = 0.0

    # 2. Change type match
    if (
        case_ctx.change_type in entry.applies_to_change_types
        or "all" in entry.applies_to_change_types
    ):
        scores["change_type_match"] = 1.0
    else:
        scores["change_type_match"] = 0.0

    # 3. Molecule class match
    if (
        case_ctx.molecule_class in entry.applies_to_molecule_classes
        or "all" in entry.applies_to_molecule_classes
    ):
        scores["molecule_class_match"] = 1.0
    else:
        scores["molecule_class_match"] = 0.0

    # 4. Authority tier
    tier = entry.authority_quality_tier or "contextual"
    scores["authority_tier"] = AUTHORITY_TIER_SCORES.get(tier, 0.3)

    # 5. Temporal weight
    temporal = entry.temporal_status or "current"
    if temporal.startswith("superseded_by:"):
        scores["temporal_weight"] = 0.05
    else:
        scores["temporal_weight"] = TEMPORAL_SCORES.get(temporal, 0.5)

    # 6. CQA relevance bonus
    if cluster.contains_cqa and any(
        "CQA" in cat or cluster.dominant_category in cat
        for cat in entry.likely_concern_categories
    ):
        scores["cqa_bonus"] = 1.0
    elif cluster.contains_cqa:
        scores["cqa_bonus"] = 0.3
    else:
        scores["cqa_bonus"] = 0.0

    # Weighted sum
    total = sum(
        scores[k] * SCORE_WEIGHTS[k]
        for k in SCORE_WEIGHTS
    )
    return round(total, 4)


def _score_entry_for_case(
    entry: RegistryEntry,
    case_ctx: CaseContext,
    all_categories: Set[str],
) -> float:
    """Score a registry entry's relevance at the case level.

    This is INDEPENDENT from cluster scoring -- it considers all flagged
    categories, identified gaps, and overall authority posture.
    """
    scores: Dict[str, float] = {}

    # Category breadth: what fraction of case categories does this entry cover?
    if all_categories:
        overlap = len(set(entry.applicable_categories) & all_categories)
        scores["category_match"] = min(overlap / max(len(all_categories), 1), 1.0)
    else:
        # No flagged categories -- any entry with broad applicability is relevant
        scores["category_match"] = 0.5 if len(entry.applicable_categories) >= 3 else 0.2

    # Change type match
    if (
        case_ctx.change_type in entry.applies_to_change_types
        or "all" in entry.applies_to_change_types
    ):
        scores["change_type_match"] = 1.0
    else:
        scores["change_type_match"] = 0.0

    # Molecule class match
    if (
        case_ctx.molecule_class in entry.applies_to_molecule_classes
        or "all" in entry.applies_to_molecule_classes
    ):
        scores["molecule_class_match"] = 1.0
    else:
        scores["molecule_class_match"] = 0.0

    # Authority tier
    tier = entry.authority_quality_tier or "contextual"
    scores["authority_tier"] = AUTHORITY_TIER_SCORES.get(tier, 0.3)

    # Temporal weight
    temporal = entry.temporal_status or "current"
    if temporal.startswith("superseded_by:"):
        scores["temporal_weight"] = 0.05
    else:
        scores["temporal_weight"] = TEMPORAL_SCORES.get(temporal, 0.5)

    # CQA bonus -- case-level: any CQA in the flagged attributes?
    has_cqa_concern = any("CQA" in cat for cat in all_categories)
    if has_cqa_concern and entry.likely_concern_categories:
        scores["cqa_bonus"] = 0.5
    else:
        scores["cqa_bonus"] = 0.0

    total = sum(
        scores[k] * SCORE_WEIGHTS[k]
        for k in SCORE_WEIGHTS
    )
    return round(total, 4)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RELATED_CATEGORIES: Dict[str, List[str]] = {
    "potency": ["biological_activity", "bioassay", "functional_assay", "efficacy"],
    "purity": ["aggregation", "impurities", "impurity_profile", "monomer_purity"],
    "identity": ["primary_structure", "sequence_verification", "product_identity"],
    "stability": ["shelf_life", "degradation", "trending", "formulation_stability"],
    "safety": ["immunogenicity", "hcp_residuals", "endotoxin", "viral_clearance"],
    "physicochemical": [
        "glycosylation", "charge_variants", "higher_order_structure",
        "disulfide_bonds", "oxidation",
    ],
    "process_validation": ["manufacturing", "process_control", "process_change"],
}


def _related_categories(category: str) -> List[str]:
    """Return related category names for fuzzy matching."""
    return _RELATED_CATEGORIES.get(category, [])


def _entry_to_ref(
    entry: RegistryEntry,
    relevance_score: float,
    cluster: Optional[RiskCluster] = None,
    case_ctx: Optional[CaseContext] = None,
) -> RefEntry:
    """Convert a RegistryEntry to a RefEntry with a relevance note."""
    note = _build_relevance_note(entry, cluster, case_ctx)
    return RefEntry(
        entry_id=entry.id,
        title=entry.title,
        source=entry.source,
        authority_quality_tier=entry.authority_quality_tier or "contextual",
        relevance_score=relevance_score,
        decision_relevance_note=note,
    )


def _build_relevance_note(
    entry: RegistryEntry,
    cluster: Optional[RiskCluster] = None,
    case_ctx: Optional[CaseContext] = None,
) -> str:
    """Build a non-empty decision_relevance_note for a RefEntry."""
    parts = []

    # Describe the authority basis
    tier = entry.authority_quality_tier or "contextual"
    tier_label = {
        "primary": "Primary authority (ICH guideline)",
        "strong_secondary": "Strong secondary authority (regulatory guidance)",
        "contextual": "Contextual reference",
    }.get(tier, "Reference")
    parts.append(tier_label)

    # Describe category relevance
    if cluster:
        if cluster.dominant_category in entry.applicable_categories:
            parts.append(
                f"directly applicable to {cluster.dominant_category}"
            )
        else:
            parts.append(
                f"related to {cluster.dominant_category} via cross-category coverage"
            )
    elif case_ctx:
        parts.append(f"relevant to {case_ctx.change_type} assessment")

    # Source info
    if entry.source:
        parts.append(f"from {entry.source}")

    return "; ".join(parts) + "."


def _filter_applicable(
    registry: EvidenceRegistry,
    case_ctx: CaseContext,
) -> List[RegistryEntry]:
    """Pre-filter registry entries by case-level applicability."""
    result = []
    for entry in registry._entries:
        # Change type
        if (
            case_ctx.change_type not in entry.applies_to_change_types
            and "all" not in entry.applies_to_change_types
        ):
            continue
        # Molecule class
        if (
            case_ctx.molecule_class not in entry.applies_to_molecule_classes
            and "all" not in entry.applies_to_molecule_classes
        ):
            continue
        # Lifecycle stage
        if (
            case_ctx.lifecycle_stage not in entry.applies_to_lifecycle_stages
            and "all" not in entry.applies_to_lifecycle_stages
        ):
            continue
        # Geography
        if (
            case_ctx.target_geography not in entry.geography
            and "global" not in entry.geography
        ):
            continue
        # Skip fully superseded
        if entry.temporal_status.startswith("superseded_by:"):
            continue
        result.append(entry)
    return result


def _select_type_diverse(
    scored: List[Tuple[RegistryEntry, float]],
) -> List[Tuple[RegistryEntry, float]]:
    """Select type-diverse top entries."""
    by_type: Dict[str, List[Tuple[RegistryEntry, float]]] = defaultdict(list)
    for entry, score in scored:
        by_type[entry.decision_type].append((entry, score))

    selected: List[Tuple[RegistryEntry, float]] = []
    for dtype, items in by_type.items():
        items.sort(key=lambda x: x[1], reverse=True)
        limit = TYPE_LIMITS.get(dtype, 2)
        selected.extend(items[:limit])

    selected.sort(key=lambda x: x[1], reverse=True)
    return selected


def _build_cluster_pack(
    cluster: RiskCluster,
    case_ctx: CaseContext,
    applicable_entries: List[RegistryEntry],
) -> AuthorityContextPack:
    """Build an AuthorityContextPack for a single cluster."""
    # Score all entries for this cluster
    scored = [
        (entry, _score_entry_for_cluster(entry, cluster, case_ctx))
        for entry in applicable_entries
    ]
    # Filter out zero-relevance
    scored = [(e, s) for e, s in scored if s > 0.05]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Type-diverse selection
    selected = _select_type_diverse(scored)

    # Categorize into ref lists
    normative_refs: List[RefEntry] = []
    precedent_refs: List[RefEntry] = []
    method_refs: List[RefEntry] = []
    concern_pattern_refs: List[RefEntry] = []

    for entry, score in selected:
        ref = _entry_to_ref(entry, score, cluster=cluster)
        cat = DECISION_TYPE_TO_REF_CATEGORY.get(entry.decision_type, "concern_pattern")
        if cat == "normative":
            normative_refs.append(ref)
        elif cat == "precedent":
            precedent_refs.append(ref)
        elif cat == "method":
            method_refs.append(ref)
        else:
            concern_pattern_refs.append(ref)

    # Counts
    n_refs_by_type = {
        "normative": len(normative_refs),
        "precedent": len(precedent_refs),
        "method": len(method_refs),
        "concern_pattern": len(concern_pattern_refs),
    }
    # Descriptive conclusion counts (GUARD-005: descriptive only)
    n_refs_by_conclusion = _count_by_conclusion(selected)

    # Flags
    authority_conflict_flag = _detect_authority_conflict(selected, cluster)
    temporal_conflict_flag = _detect_temporal_conflict(selected)
    geography_conflict_flag = _detect_geography_conflict(selected, case_ctx)
    authority_sparsity_flag = (
        len(normative_refs) == 0 and len(precedent_refs) == 0
    )

    # Top decision drivers: best scoring refs with non-empty notes
    all_refs = normative_refs + precedent_refs + method_refs + concern_pattern_refs
    all_refs_sorted = sorted(all_refs, key=lambda r: r.relevance_score, reverse=True)
    top_drivers = all_refs_sorted[:TOP_DRIVERS_COUNT]
    # Guarantee non-empty decision_relevance_note
    for drv in top_drivers:
        if not drv.decision_relevance_note or not drv.decision_relevance_note.strip():
            drv.decision_relevance_note = (
                f"Top-ranked reference for {cluster.dominant_category} cluster "
                f"({drv.authority_quality_tier} tier)."
            )

    # Fallback flags
    fallback_flags: List[str] = []
    if authority_sparsity_flag:
        fallback_flags.append("no_normative_or_precedent")
    if len(selected) == 0:
        fallback_flags.append("no_matching_evidence")

    pack = AuthorityContextPack(
        pack_id=AuthorityContextPack.generate_pack_id(),
        scope_level="cluster",
        scope_id=cluster.cluster_id,
        normative_refs=normative_refs,
        precedent_refs=precedent_refs,
        method_refs=method_refs,
        concern_pattern_refs=concern_pattern_refs,
        n_refs_by_type=n_refs_by_type,
        n_refs_by_conclusion=n_refs_by_conclusion,
        authority_conflict_flag=authority_conflict_flag,
        temporal_conflict_flag=temporal_conflict_flag,
        geography_conflict_flag=geography_conflict_flag,
        authority_sparsity_flag=authority_sparsity_flag,
        top_decision_drivers=top_drivers,
        fallback_flags=fallback_flags,
    )

    return pack


def _build_case_level_pack(
    case_ctx: CaseContext,
    clusters: List[RiskCluster],
    cluster_packs: List[AuthorityContextPack],
    applicable_entries: List[RegistryEntry],
) -> AuthorityContextPack:
    """Build an INDEPENDENT case-level AuthorityContextPack.

    CRITICAL: This is NOT the union of cluster packs. It independently
    assesses package-wide authority posture, detects cross-cluster conflict
    patterns, and summarizes overall normative coverage.
    """
    # Gather all categories across clusters
    all_categories: Set[str] = set()
    for cl in clusters:
        all_categories.add(cl.dominant_category)
    # Also add flagged categories from case context
    all_categories.update(case_ctx.flagged_categories)

    # Score entries at case level (independent scoring)
    scored = [
        (entry, _score_entry_for_case(entry, case_ctx, all_categories))
        for entry in applicable_entries
    ]
    scored = [(e, s) for e, s in scored if s > 0.05]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Type-diverse selection (independent from cluster selections)
    selected = _select_type_diverse(scored)

    # Categorize
    normative_refs: List[RefEntry] = []
    precedent_refs: List[RefEntry] = []
    method_refs: List[RefEntry] = []
    concern_pattern_refs: List[RefEntry] = []

    for entry, score in selected:
        ref = _entry_to_ref(entry, score, case_ctx=case_ctx)
        cat = DECISION_TYPE_TO_REF_CATEGORY.get(entry.decision_type, "concern_pattern")
        if cat == "normative":
            normative_refs.append(ref)
        elif cat == "precedent":
            precedent_refs.append(ref)
        elif cat == "method":
            method_refs.append(ref)
        else:
            concern_pattern_refs.append(ref)

    n_refs_by_type = {
        "normative": len(normative_refs),
        "precedent": len(precedent_refs),
        "method": len(method_refs),
        "concern_pattern": len(concern_pattern_refs),
    }
    n_refs_by_conclusion = _count_by_conclusion(selected)

    # Cross-cluster conflict detection (case-level exclusive logic)
    authority_conflict_flag = _detect_cross_cluster_authority_conflict(
        cluster_packs, clusters
    )
    temporal_conflict_flag = _detect_cross_cluster_temporal_conflict(cluster_packs)
    geography_conflict_flag = _detect_cross_cluster_geography_conflict(
        cluster_packs, case_ctx
    )
    authority_sparsity_flag = (
        len(normative_refs) == 0 and len(precedent_refs) == 0
    )

    # Top decision drivers: MUST differ from any single cluster pack
    all_refs = normative_refs + precedent_refs + method_refs + concern_pattern_refs
    all_refs_sorted = sorted(all_refs, key=lambda r: r.relevance_score, reverse=True)

    # Build case-level drivers that incorporate cross-cluster concerns
    top_drivers = _build_case_level_drivers(
        all_refs_sorted, cluster_packs, clusters, case_ctx
    )

    # Guarantee non-empty decision_relevance_note on all drivers
    for drv in top_drivers:
        if not drv.decision_relevance_note or not drv.decision_relevance_note.strip():
            drv.decision_relevance_note = (
                f"Package-level reference for {case_ctx.change_type} assessment."
            )

    fallback_flags: List[str] = []
    if authority_sparsity_flag:
        fallback_flags.append("no_normative_or_precedent")
    if len(selected) == 0:
        fallback_flags.append("no_matching_evidence")
    # Cross-cluster sparsity
    sparse_clusters = [p for p in cluster_packs if p.authority_sparsity_flag]
    if len(sparse_clusters) > len(cluster_packs) / 2:
        fallback_flags.append("majority_clusters_sparse")

    pack = AuthorityContextPack(
        pack_id=AuthorityContextPack.generate_pack_id(),
        scope_level="case",
        scope_id=case_ctx.case_id,
        normative_refs=normative_refs,
        precedent_refs=precedent_refs,
        method_refs=method_refs,
        concern_pattern_refs=concern_pattern_refs,
        n_refs_by_type=n_refs_by_type,
        n_refs_by_conclusion=n_refs_by_conclusion,
        authority_conflict_flag=authority_conflict_flag,
        temporal_conflict_flag=temporal_conflict_flag,
        geography_conflict_flag=geography_conflict_flag,
        authority_sparsity_flag=authority_sparsity_flag,
        top_decision_drivers=top_drivers,
        fallback_flags=fallback_flags,
    )

    return pack


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def _detect_authority_conflict(
    selected: List[Tuple[RegistryEntry, float]],
    cluster: RiskCluster,
) -> bool:
    """Detect authority conflict within a cluster's matched references.

    Conflict exists when primary-tier and strong_secondary-tier entries
    point to different conclusions for the same category, or when
    the cluster has contradiction semantics.
    """
    if cluster.risk_semantics == "contradiction":
        return True

    tiers = set()
    for entry, _ in selected:
        tier = entry.authority_quality_tier or "contextual"
        if tier in ("primary", "strong_secondary"):
            tiers.add(tier)

    # If entries from different authority tiers exist and cluster has
    # evidence suggesting tension (e.g., contradiction_present)
    if len(tiers) >= 2 and cluster.contradiction_present:
        return True

    return False


def _detect_temporal_conflict(
    selected: List[Tuple[RegistryEntry, float]],
) -> bool:
    """Detect temporal conflict: mix of current and dated references."""
    statuses = set()
    for entry, _ in selected:
        ts = entry.temporal_status or "current"
        if ts.startswith("superseded_by:"):
            ts = "superseded"
        statuses.add(ts)
    return "current" in statuses and (
        "dated_but_informative" in statuses or "historical_only" in statuses
    )


def _detect_geography_conflict(
    selected: List[Tuple[RegistryEntry, float]],
    case_ctx: CaseContext,
) -> bool:
    """Detect geography conflict when references span different jurisdictions."""
    if case_ctx.target_geography != "global":
        return False
    geos: Set[str] = set()
    for entry, _ in selected:
        for g in entry.geography:
            if g != "global":
                geos.add(g)
    return len(geos) >= 2


def _detect_cross_cluster_authority_conflict(
    cluster_packs: List[AuthorityContextPack],
    clusters: List[RiskCluster],
) -> bool:
    """Case-level: detect authority conflict across clusters."""
    # If any cluster pack has authority_conflict_flag, propagate
    if any(p.authority_conflict_flag for p in cluster_packs):
        return True
    # Cross-cluster: one cluster has strong authority, another is sparse
    has_strong = any(
        not p.authority_sparsity_flag and len(p.normative_refs) >= 2
        for p in cluster_packs
    )
    has_sparse = any(p.authority_sparsity_flag for p in cluster_packs)
    return has_strong and has_sparse


def _detect_cross_cluster_temporal_conflict(
    cluster_packs: List[AuthorityContextPack],
) -> bool:
    """Case-level: detect temporal conflicts across clusters."""
    return any(p.temporal_conflict_flag for p in cluster_packs)


def _detect_cross_cluster_geography_conflict(
    cluster_packs: List[AuthorityContextPack],
    case_ctx: CaseContext,
) -> bool:
    """Case-level: detect geography conflicts across clusters."""
    return any(p.geography_conflict_flag for p in cluster_packs)


# ---------------------------------------------------------------------------
# Conclusion counting (GUARD-005: descriptive only)
# ---------------------------------------------------------------------------

def _count_by_conclusion(
    selected: List[Tuple[RegistryEntry, float]],
) -> Dict[str, int]:
    """Count refs by their evidence_weight (DESCRIPTIVE ONLY).

    WARNING: n_refs_by_conclusion must NEVER be used to infer verdict
    direction or make judgment calls. (GUARD-005)
    """
    counts: Dict[str, int] = Counter()
    for entry, _ in selected:
        counts[entry.evidence_weight] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Case-level driver construction
# ---------------------------------------------------------------------------

def _build_case_level_drivers(
    all_refs_sorted: List[RefEntry],
    cluster_packs: List[AuthorityContextPack],
    clusters: List[RiskCluster],
    case_ctx: CaseContext,
) -> List[RefEntry]:
    """Build case-level top_decision_drivers that are NOT identical to
    any single cluster pack's drivers.

    Incorporates cross-cluster conflict patterns into driver notes.
    """
    # Start with top case-level refs
    candidates = all_refs_sorted[:TOP_DRIVERS_COUNT + 2]

    # Collect all cluster driver IDs to ensure case-level differs
    cluster_driver_sets = []
    for pack in cluster_packs:
        driver_ids = frozenset(d.entry_id for d in pack.top_decision_drivers)
        cluster_driver_sets.append(driver_ids)

    # Build case-level drivers
    drivers: List[RefEntry] = []
    for ref in candidates:
        if len(drivers) >= TOP_DRIVERS_COUNT:
            break

        # Annotate with cross-cluster context
        cross_notes = []
        conflict_clusters = [
            p for p in cluster_packs if p.authority_conflict_flag
        ]
        if conflict_clusters:
            cross_notes.append(
                f"cross-cluster authority conflict detected in "
                f"{len(conflict_clusters)} cluster(s)"
            )
        sparse_clusters = [
            p for p in cluster_packs if p.authority_sparsity_flag
        ]
        if sparse_clusters:
            cross_notes.append(
                f"authority sparsity in {len(sparse_clusters)} cluster(s)"
            )

        # Build the case-level note
        base_note = ref.decision_relevance_note or ""
        if cross_notes:
            case_note = (
                f"{base_note} Package-level: {'; '.join(cross_notes)}."
            )
        else:
            case_note = (
                f"{base_note} Package-level: consistent authority across "
                f"{len(cluster_packs)} cluster(s)."
            )

        case_ref = RefEntry(
            entry_id=ref.entry_id,
            title=ref.title,
            source=ref.source,
            authority_quality_tier=ref.authority_quality_tier,
            relevance_score=ref.relevance_score,
            decision_relevance_note=case_note,
        )
        drivers.append(case_ref)

    # Ensure case-level drivers differ from every single cluster pack
    case_driver_ids = frozenset(d.entry_id for d in drivers)
    # If identical to a cluster pack's drivers, swap in a different ref
    for cds in cluster_driver_sets:
        if case_driver_ids == cds and len(all_refs_sorted) > TOP_DRIVERS_COUNT:
            # Replace last driver with next available
            for extra_ref in all_refs_sorted[TOP_DRIVERS_COUNT:]:
                if extra_ref.entry_id not in case_driver_ids:
                    drivers[-1] = RefEntry(
                        entry_id=extra_ref.entry_id,
                        title=extra_ref.title,
                        source=extra_ref.source,
                        authority_quality_tier=extra_ref.authority_quality_tier,
                        relevance_score=extra_ref.relevance_score,
                        decision_relevance_note=(
                            f"{extra_ref.decision_relevance_note} "
                            f"Package-level supplemental driver for cross-cluster coverage."
                        ),
                    )
                    break
            break

    return drivers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_for_clusters(
    case_ctx: CaseContext,
    clusters: List[RiskCluster],
    registry: EvidenceRegistry,
) -> Tuple[List[AuthorityContextPack], AuthorityContextPack]:
    """Match evidence for clusters and produce authority context packs.

    Returns:
        Tuple of:
        - List of per-cluster AuthorityContextPacks (one DISTINCT pack per cluster)
        - One INDEPENDENT case-level AuthorityContextPack

    Each cluster gets a distinct pack. The case-level pack is independently
    constructed -- NOT the union of cluster packs.
    """
    # Pre-filter entries by case-level applicability
    applicable = _filter_applicable(registry, case_ctx)

    # Build one pack per cluster
    cluster_packs: List[AuthorityContextPack] = []
    for cluster in clusters:
        pack = _build_cluster_pack(cluster, case_ctx, applicable)
        cluster_packs.append(pack)

    # Build independent case-level pack
    case_pack = _build_case_level_pack(
        case_ctx, clusters, cluster_packs, applicable
    )

    return cluster_packs, case_pack
