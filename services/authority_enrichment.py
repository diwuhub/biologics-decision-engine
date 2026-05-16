"""
Gold-Case-Driven Authority Semantics Enrichment — Step 5.

Enriches evidence registry entries that DRIVE judgment in gold cases.
Not global frequency — behavioral priority based on which entries appear
as top_decision_drivers or in blocking cluster packs.

Steps:
  1. Run all 12 gold cases through the matcher (Step 2)
  2. Collect all entry_ids from top_decision_drivers and blocking cluster packs
  3. These are the v1 enrichment priority list (~50-100 entries)
  4. For each, ensure: authority_quality_tier, temporal_status,
     likely_concern_categories are fully populated

Completion criterion: every entry appearing as top_decision_driver in
any gold case has full semantics. Behavioral, not inventory-based.

Fields to enrich: authority_quality_tier, temporal_status,
    likely_concern_categories, recommended_followup, display_tier

Step 5: Judgment Core Refactor.
"""

from __future__ import annotations

import glob
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Project root (for locating gold case files, NOT for sys.path manipulation)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

from evidence_registry import EvidenceRegistry
from evidence_registry.registry import RegistryEntry
from schemas.authority_context_pack import AuthorityContextPack, RefEntry
from schemas.case_context import CaseContext
from schemas.risk_cluster import RiskCluster
from services.cluster_builder import build_risk_clusters
from services.cluster_matcher import match_for_clusters


# ---------------------------------------------------------------------------
# Enrichment defaults by decision type
# ---------------------------------------------------------------------------

_AUTHORITY_TIER_DEFAULTS = {
    "Normative": "primary",
    "Precedent": "strong_secondary",
    "Method": "contextual",
    "Concern Pattern": "contextual",
}

_DISPLAY_TIER_DEFAULTS = {
    "Normative": "primary",
    "Precedent": "primary",
    "Method": "secondary",
    "Concern Pattern": "appendix",
}

_RECOMMENDED_FOLLOWUP_DEFAULTS = {
    "orthogonal_gap": {
        "type": "additional_testing",
        "description": "Provide orthogonal analytical method data",
        "priority": "high",
    },
    "assay_gap": {
        "type": "additional_testing",
        "description": "Add missing required method type",
        "priority": "high",
    },
    "contradiction": {
        "type": "bridging_study",
        "description": "Resolve inter-method contradiction",
        "priority": "critical",
    },
    "no_precedent_low_confidence": {
        "type": "human_review",
        "description": "Expert review of normative-only authority basis",
        "priority": "medium",
    },
    "pattern_concern_only": {
        "type": "none",
        "description": "No additional testing required",
        "priority": "low",
    },
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EnrichmentReport:
    """Report of enrichment actions taken."""
    n_gold_cases_processed: int = 0
    n_unique_driver_entries: int = 0
    n_entries_enriched: int = 0
    n_already_complete: int = 0
    enriched_entry_ids: List[str] = field(default_factory=list)
    missing_entry_ids: List[str] = field(default_factory=list)
    completeness_before: float = 0.0
    completeness_after: float = 0.0


# ---------------------------------------------------------------------------
# Step 5 main: collect and enrich
# ---------------------------------------------------------------------------

def run_gold_case_enrichment(
    gold_dir: Optional[str] = None,
    registry: Optional[EvidenceRegistry] = None,
) -> EnrichmentReport:
    """Run all 12 gold cases through matcher, collect driver entries,
    and enrich their semantics.

    Returns an EnrichmentReport summarizing actions taken.
    """
    if gold_dir is None:
        gold_dir = str(_PROJECT_ROOT / "tests" / "gold")

    if registry is None:
        registry = EvidenceRegistry()

    report = EnrichmentReport()

    # Step 1: Load all gold cases
    gold_cases = _load_gold_cases(gold_dir)
    report.n_gold_cases_processed = len(gold_cases)

    # Step 2: Run each through cluster builder + matcher, collect entry_ids
    driver_entry_ids: Set[str] = set()
    blocking_entry_ids: Set[str] = set()
    entry_contexts: Dict[str, List[str]] = defaultdict(list)

    for gc in gold_cases:
        case_id = gc["case_id"]

        try:
            ctx = CaseContext(**gc["case_context"])
            clusters = build_risk_clusters(ctx, gc["attribute_results"])
            cluster_packs, case_pack = match_for_clusters(ctx, clusters, registry)

            # Collect from top_decision_drivers (all packs)
            for pack in cluster_packs + [case_pack]:
                for driver in pack.top_decision_drivers:
                    driver_entry_ids.add(driver.entry_id)
                    entry_contexts[driver.entry_id].append(
                        f"{case_id}:{pack.scope_level}:{pack.scope_id}"
                    )

            # Collect from blocking cluster packs
            blocking_clusters = [c for c in clusters if c.package_blocking]
            for bc in blocking_clusters:
                idx = next(
                    (i for i, c in enumerate(clusters) if c.cluster_id == bc.cluster_id),
                    None,
                )
                if idx is not None and idx < len(cluster_packs):
                    bp = cluster_packs[idx]
                    for ref in bp.normative_refs + bp.precedent_refs + bp.method_refs:
                        blocking_entry_ids.add(ref.entry_id)
                        entry_contexts[ref.entry_id].append(
                            f"{case_id}:blocking:{bc.cluster_id}"
                        )

        except Exception as e:
            # Log but continue
            entry_contexts[f"ERROR:{case_id}"].append(str(e))

    # Step 3: Merge into priority list
    priority_ids = driver_entry_ids | blocking_entry_ids
    report.n_unique_driver_entries = len(priority_ids)

    # Step 4: Check completeness before enrichment
    report.completeness_before = _measure_completeness(priority_ids, registry)

    # Step 5: Enrich each entry
    for entry_id in sorted(priority_ids):
        entry = registry.get(entry_id)
        if entry is None:
            report.missing_entry_ids.append(entry_id)
            continue

        was_enriched = _enrich_entry(entry, entry_contexts.get(entry_id, []))
        if was_enriched:
            report.n_entries_enriched += 1
            report.enriched_entry_ids.append(entry_id)
        else:
            report.n_already_complete += 1

    # Step 6: Check completeness after enrichment
    report.completeness_after = _measure_completeness(priority_ids, registry)

    return report


# ---------------------------------------------------------------------------
# Enrichment logic
# ---------------------------------------------------------------------------

def _enrich_entry(
    entry: RegistryEntry,
    contexts: List[str],
) -> bool:
    """Enrich a single registry entry's semantic fields.

    Returns True if any field was modified.
    """
    modified = False

    # authority_quality_tier
    if entry.authority_quality_tier == "contextual" or not entry.authority_quality_tier:
        default = _AUTHORITY_TIER_DEFAULTS.get(entry.decision_type, "contextual")
        if default != entry.authority_quality_tier:
            entry.authority_quality_tier = default
            modified = True

    # temporal_status
    if not entry.temporal_status or entry.temporal_status == "current":
        # Infer from year if available
        if entry.year > 0:
            if entry.year >= 2015:
                entry.temporal_status = "current"
            elif entry.year >= 2005:
                entry.temporal_status = "dated_but_informative"
            else:
                entry.temporal_status = "historical_only"
            modified = True

    # likely_concern_categories
    if not entry.likely_concern_categories:
        categories = _infer_concern_categories(entry)
        if categories:
            entry.likely_concern_categories = categories
            modified = True

    # recommended_followup
    if entry.recommended_followup is None:
        followup = _infer_recommended_followup(entry, contexts)
        if followup:
            entry.recommended_followup = followup
            modified = True

    # display_tier
    if entry.display_tier == "secondary" or not entry.display_tier:
        default = _DISPLAY_TIER_DEFAULTS.get(entry.decision_type, "secondary")
        if default != entry.display_tier:
            entry.display_tier = default
            modified = True

    return modified


def _infer_concern_categories(entry: RegistryEntry) -> List[str]:
    """Infer likely_concern_categories from entry metadata."""
    categories = []

    for cat in entry.applicable_categories:
        if cat in ("potency", "purity", "stability", "safety",
                    "physicochemical", "identity"):
            # Mark CQA concern categories based on entry type
            if entry.decision_type == "Normative" and entry.authority_quality_tier == "primary":
                categories.append(f"CQA_{cat}")
            categories.append(cat)

    # Deduplicate
    return list(dict.fromkeys(categories))


def _infer_recommended_followup(
    entry: RegistryEntry,
    contexts: List[str],
) -> Optional[Dict]:
    """Infer recommended_followup from entry context."""
    # Look at contexts for blocking clusters
    for ctx_str in contexts:
        if "blocking" in ctx_str:
            # Entry is involved in blocking: recommend additional testing
            return {
                "type": "additional_testing",
                "description": (
                    f"Evidence supporting {entry.title} is involved in "
                    f"blocking cluster evaluation"
                ),
                "priority": "high",
            }

    # Default based on decision type
    if entry.decision_type == "Concern Pattern":
        return {
            "type": "monitoring",
            "description": "Monitor concern pattern for emerging regulatory signals",
            "priority": "low",
        }

    return None


# ---------------------------------------------------------------------------
# Completeness measurement
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "authority_quality_tier",
    "temporal_status",
    "likely_concern_categories",
]


def _measure_completeness(
    entry_ids: Set[str],
    registry: EvidenceRegistry,
) -> float:
    """Measure what fraction of entries have all required enrichment fields."""
    if not entry_ids:
        return 1.0

    complete = 0
    total = 0

    for eid in entry_ids:
        entry = registry.get(eid)
        if entry is None:
            continue
        total += 1

        is_complete = True
        if not entry.authority_quality_tier or entry.authority_quality_tier == "contextual":
            # contextual is the default unset value for non-normative
            if entry.decision_type in ("Normative", "Precedent"):
                is_complete = False
        if not entry.temporal_status:
            is_complete = False
        if not entry.likely_concern_categories:
            is_complete = False

        if is_complete:
            complete += 1

    return complete / total if total > 0 else 1.0


# ---------------------------------------------------------------------------
# Gold case loading
# ---------------------------------------------------------------------------

def _load_gold_cases(gold_dir: str) -> List[dict]:
    """Load all gold case JSON fixtures."""
    pattern = os.path.join(gold_dir, "gc_*.json")
    files = sorted(glob.glob(pattern))
    cases = []
    for fpath in files:
        with open(fpath, "r") as f:
            cases.append(json.load(f))
    return cases


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    report = run_gold_case_enrichment()
    print(f"Gold Case Enrichment Report:")
    print(f"  Cases processed: {report.n_gold_cases_processed}")
    print(f"  Unique driver entries: {report.n_unique_driver_entries}")
    print(f"  Entries enriched: {report.n_entries_enriched}")
    print(f"  Already complete: {report.n_already_complete}")
    print(f"  Missing entries: {len(report.missing_entry_ids)}")
    print(f"  Completeness before: {report.completeness_before:.1%}")
    print(f"  Completeness after: {report.completeness_after:.1%}")
    if report.missing_entry_ids:
        print(f"  Missing IDs: {report.missing_entry_ids[:10]}")
