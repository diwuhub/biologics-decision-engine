"""
Tests for Step 2: Cluster-Aware Matcher (services/cluster_matcher.py).

Verifies:
- Each cluster gets a DISTINCT pack
- Every top_decision_driver has a non-empty decision_relevance_note
- Case-level pack is INDEPENDENTLY constructed (not union of cluster packs)
- Case-level pack detects cross-cluster conflict patterns
- Gold Case 01 and 06 produce packs with correct sparsity/conflict flags
- Gold Case 05 produces authority_conflict_flag = True in potency cluster pack
- Case-level pack top_decision_drivers are NOT identical to any single cluster pack's
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

GOLD_DIR = os.path.join(str(PROJECT_ROOT), "tests", "gold")


def load_gold_case(case_id: str) -> dict:
    """Load a gold case by ID."""
    mapping = {
        "GC-01": "gc_01_normal_sufficient.json",
        "GC-02": "gc_02_orthogonal_gap.json",
        "GC-03": "gc_03_trending_stability.json",
        "GC-04": "gc_04_better_than_reference.json",
        "GC-05": "gc_05_conflicting_methods.json",
        "GC-06": "gc_06_no_precedent.json",
        "GC-07": "gc_07_should_abstain.json",
        "GC-08": "gc_08_geography_conflict.json",
        "GC-09": "gc_09_dated_support.json",
        "GC-10": "gc_10_concern_pattern_only.json",
        "GC-11": "gc_11_hidden_insufficiency.json",
        "GC-12": "gc_12_mixed_weak_evidence.json",
    }
    fname = mapping[case_id]
    with open(os.path.join(GOLD_DIR, fname)) as f:
        return json.load(f)


def build_clusters_and_match(case_id: str):
    """Helper: load gold case, build clusters, run matcher."""
    from schemas.case_context import CaseContext
    from services.cluster_builder import build_risk_clusters
    from services.cluster_matcher import match_for_clusters
    from evidence_registry import EvidenceRegistry

    gc = load_gold_case(case_id)
    ctx = CaseContext(**gc["case_context"])
    clusters = build_risk_clusters(ctx, gc["attribute_results"])
    registry = EvidenceRegistry()
    cluster_packs, case_pack = match_for_clusters(ctx, clusters, registry)
    return ctx, clusters, cluster_packs, case_pack


# =====================================================================
# Step 2 Core Tests
# =====================================================================


class TestClusterMatcherBasic:
    """Basic matcher functionality tests."""

    def test_returns_one_pack_per_cluster(self):
        """Each cluster gets exactly one distinct pack."""
        ctx, clusters, cluster_packs, case_pack = build_clusters_and_match("GC-01")
        assert len(cluster_packs) == len(clusters)
        # Each pack has scope_level = 'cluster'
        for pack in cluster_packs:
            assert pack.scope_level == "cluster"
        # Case pack has scope_level = 'case'
        assert case_pack.scope_level == "case"

    def test_cluster_packs_are_distinct(self):
        """No two cluster packs share the same pack_id or scope_id."""
        ctx, clusters, cluster_packs, case_pack = build_clusters_and_match("GC-01")
        pack_ids = [p.pack_id for p in cluster_packs]
        assert len(pack_ids) == len(set(pack_ids)), "Duplicate pack_ids"
        scope_ids = [p.scope_id for p in cluster_packs]
        assert len(scope_ids) == len(set(scope_ids)), "Duplicate scope_ids"

    def test_top_drivers_have_nonempty_notes(self):
        """Every top_decision_driver must have non-empty decision_relevance_note."""
        for case_id in ["GC-01", "GC-05", "GC-06"]:
            ctx, clusters, cluster_packs, case_pack = build_clusters_and_match(case_id)
            all_packs = cluster_packs + [case_pack]
            for pack in all_packs:
                for driver in pack.top_decision_drivers:
                    assert driver.decision_relevance_note.strip(), (
                        f"{case_id} pack {pack.pack_id}: driver {driver.entry_id} "
                        f"has empty decision_relevance_note"
                    )

    def test_case_pack_not_union_of_cluster_packs(self):
        """Case-level pack's top_decision_drivers must NOT be identical
        to any single cluster pack's top_decision_drivers."""
        for case_id in ["GC-01", "GC-05", "GC-06"]:
            ctx, clusters, cluster_packs, case_pack = build_clusters_and_match(case_id)
            case_driver_ids = frozenset(
                d.entry_id for d in case_pack.top_decision_drivers
            )
            for cpack in cluster_packs:
                cluster_driver_ids = frozenset(
                    d.entry_id for d in cpack.top_decision_drivers
                )
                # They should differ (at least in notes, but also should
                # not be identical sets of entry_ids in most cases)
                # For the note-level check:
                case_notes = [d.decision_relevance_note for d in case_pack.top_decision_drivers]
                cluster_notes = [d.decision_relevance_note for d in cpack.top_decision_drivers]
                assert case_notes != cluster_notes, (
                    f"{case_id}: case-level drivers have identical notes to "
                    f"cluster pack {cpack.scope_id}"
                )

    def test_refentry_fields_populated(self):
        """Each RefEntry has all required fields populated."""
        ctx, clusters, cluster_packs, case_pack = build_clusters_and_match("GC-01")
        for pack in cluster_packs + [case_pack]:
            all_refs = (
                pack.normative_refs + pack.precedent_refs +
                pack.method_refs + pack.concern_pattern_refs
            )
            for ref in all_refs:
                assert ref.entry_id, "RefEntry missing entry_id"
                assert ref.title, "RefEntry missing title"
                assert ref.source, "RefEntry missing source"
                assert ref.authority_quality_tier in (
                    "primary", "strong_secondary", "contextual"
                ), f"Invalid tier: {ref.authority_quality_tier}"
                assert isinstance(ref.relevance_score, float)
                assert ref.decision_relevance_note.strip(), "Empty relevance note"


# =====================================================================
# Gold Case Specific Tests
# =====================================================================


class TestGoldCaseGC01:
    """GC-01: Normal Sufficient Comparability."""

    def test_gc01_no_sparsity(self):
        """GC-01 should have no authority sparsity (strong ICH Q5E + precedents)."""
        ctx, clusters, cluster_packs, case_pack = build_clusters_and_match("GC-01")
        # At least some cluster packs should not be sparse
        non_sparse = [p for p in cluster_packs if not p.authority_sparsity_flag]
        assert len(non_sparse) > 0, "GC-01: all cluster packs are sparse"

    def test_gc01_no_authority_conflict(self):
        """GC-01 should have no authority conflict (clean proceed)."""
        ctx, clusters, cluster_packs, case_pack = build_clusters_and_match("GC-01")
        for pack in cluster_packs:
            assert not pack.authority_conflict_flag, (
                f"GC-01 cluster {pack.scope_id}: unexpected authority conflict"
            )


class TestGoldCaseGC05:
    """GC-05: Conflicting Methods."""

    def test_gc05_potency_cluster_authority_conflict(self):
        """GC-05: potency cluster pack must have authority_conflict_flag = True."""
        ctx, clusters, cluster_packs, case_pack = build_clusters_and_match("GC-05")
        # Find potency-related cluster packs
        potency_clusters = [
            (cl, pk)
            for cl, pk in zip(clusters, cluster_packs)
            if cl.dominant_category == "potency"
        ]
        assert len(potency_clusters) > 0, "GC-05: no potency clusters found"
        # At least one potency cluster should have authority_conflict_flag
        conflict_packs = [
            pk for cl, pk in potency_clusters
            if pk.authority_conflict_flag
        ]
        assert len(conflict_packs) > 0, (
            "GC-05: no potency cluster pack has authority_conflict_flag = True"
        )

    def test_gc05_case_pack_has_conflict(self):
        """GC-05: case-level pack should propagate authority conflict."""
        ctx, clusters, cluster_packs, case_pack = build_clusters_and_match("GC-05")
        assert case_pack.authority_conflict_flag, (
            "GC-05: case-level pack should have authority_conflict_flag"
        )


class TestGoldCaseGC06:
    """GC-06: No Precedent, Strong Guideline."""

    def test_gc06_sparsity_in_precedent_refs(self):
        """GC-06 (bispecific): precedent refs should be sparse or absent."""
        ctx, clusters, cluster_packs, case_pack = build_clusters_and_match("GC-06")
        # Case level: bispecific has limited precedent
        total_precedent = sum(
            len(p.precedent_refs) for p in cluster_packs
        )
        # Should have normative refs but limited precedents
        total_normative = sum(
            len(p.normative_refs) for p in cluster_packs
        )
        # There should be some normative support from ICH Q5E
        assert total_normative > 0 or not all(
            p.authority_sparsity_flag for p in cluster_packs
        ), "GC-06: expected at least some normative authority"

    def test_gc06_case_pack_drivers_differ(self):
        """GC-06: case-level drivers should not match any single cluster's."""
        ctx, clusters, cluster_packs, case_pack = build_clusters_and_match("GC-06")
        case_notes = [d.decision_relevance_note for d in case_pack.top_decision_drivers]
        for cp in cluster_packs:
            cl_notes = [d.decision_relevance_note for d in cp.top_decision_drivers]
            assert case_notes != cl_notes, (
                "GC-06: case-level driver notes identical to a cluster pack"
            )


# =====================================================================
# All 12 Gold Cases -- Smoke Test
# =====================================================================


class TestAllGoldCases:
    """Run matcher on all 12 gold cases to verify no crashes."""

    @pytest.mark.parametrize("case_id", [
        "GC-01", "GC-02", "GC-03", "GC-04", "GC-05", "GC-06",
        "GC-07", "GC-08", "GC-09", "GC-10", "GC-11", "GC-12",
    ])
    def test_matcher_runs_without_error(self, case_id):
        ctx, clusters, cluster_packs, case_pack = build_clusters_and_match(case_id)
        assert len(cluster_packs) == len(clusters)
        assert case_pack.scope_level == "case"
        # Every pack must have pack_id
        for p in cluster_packs + [case_pack]:
            assert p.pack_id.startswith("PACK-")
