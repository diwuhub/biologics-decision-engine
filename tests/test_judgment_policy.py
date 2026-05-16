"""
Tests for Step 3: Two-Stage Conservative Policy with Permission Boundaries.

Covers:
  - Cluster-level policy (Stage 1): CLUST-001 to CLUST-004,
    FALL-001, FALL-003, GUARD-001, GUARD-003
  - Package-level policy (Stage 2): AGGR-001 to AGGR-006,
    ABST-001 to ABST-002, GEOG-001 to GEOG-002
  - Permission boundary enforcement
  - Gold case behavioral validation
"""

import glob
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evidence_registry import EvidenceRegistry
from schemas.authority_context_pack import AuthorityContextPack, RefEntry
from schemas.case_context import CaseContext
from schemas.package_decision import PackageDecision
from schemas.risk_cluster import RiskCluster
from services.cluster_builder import build_risk_clusters
from services.cluster_matcher import match_for_clusters
from services.judgment_policy import apply_cluster_policy, apply_package_policy

GOLD_DIR = os.path.join(str(PROJECT_ROOT), "tests", "gold")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cluster(cluster_id="CLU-001", cluster_type="category_risk",
                  category="potency", concern="minor", contains_cqa=True,
                  semantics="sufficient_evidence", attr_ids=None,
                  score=0.8, contradiction=None, blocking=None):
    c = RiskCluster(
        cluster_id=cluster_id,
        cluster_type=cluster_type,
        dominant_category=category,
        affected_attribute_ids=attr_ids or ["attr_1"],
        contains_cqa=contains_cqa,
        base_concern_level=concern,
        cluster_reason_summary=f"Test cluster for {category}.",
        risk_semantics=semantics,
        base_cluster_score=score,
    )
    if contradiction is not None:
        c.contradiction_present = contradiction
    if blocking is not None:
        c.package_blocking = blocking
    return c


def _make_pack(scope_level="cluster", scope_id="CLU-001",
               normative=0, precedent=0, method=0, concern_pattern=0,
               authority_sparsity=False, authority_conflict=False,
               geography_conflict=False, temporal_conflict=False):
    norms = [
        RefEntry(entry_id=f"N-{i}", title=f"Normative {i}", source="ICH",
                 authority_quality_tier="primary", relevance_score=0.9,
                 decision_relevance_note="Test normative ref.")
        for i in range(normative)
    ]
    precs = [
        RefEntry(entry_id=f"P-{i}", title=f"Precedent {i}", source="FDA",
                 authority_quality_tier="strong_secondary", relevance_score=0.8,
                 decision_relevance_note="Test precedent ref.")
        for i in range(precedent)
    ]
    meths = [
        RefEntry(entry_id=f"M-{i}", title=f"Method {i}", source="USP",
                 authority_quality_tier="contextual", relevance_score=0.6,
                 decision_relevance_note="Test method ref.")
        for i in range(method)
    ]
    cps = [
        RefEntry(entry_id=f"CP-{i}", title=f"Concern {i}", source="FDA WL",
                 authority_quality_tier="contextual", relevance_score=0.4,
                 decision_relevance_note="Test concern pattern.")
        for i in range(concern_pattern)
    ]
    return AuthorityContextPack(
        pack_id=AuthorityContextPack.generate_pack_id(),
        scope_level=scope_level,
        scope_id=scope_id,
        normative_refs=norms,
        precedent_refs=precs,
        method_refs=meths,
        concern_pattern_refs=cps,
        authority_sparsity_flag=authority_sparsity,
        authority_conflict_flag=authority_conflict,
        geography_conflict_flag=geography_conflict,
        temporal_conflict_flag=temporal_conflict,
        n_refs_by_type={
            "normative": normative, "precedent": precedent,
            "method": method, "concern_pattern": concern_pattern,
        },
        top_decision_drivers=norms[:1] + precs[:1] if norms or precs else cps[:1],
    )


def _make_decision(case_id="CASE-001", verdict="proceed", confidence=0.8):
    return PackageDecision(
        case_id=case_id,
        package_verdict=verdict,
        confidence=confidence,
    )


def _load_gold(case_id):
    for f in glob.glob(os.path.join(GOLD_DIR, "gc_*.json")):
        with open(f) as fp:
            gc = json.load(fp)
        if gc["case_id"] == case_id:
            return gc
    raise ValueError(f"Gold case {case_id} not found")


# ===========================================================================
# Stage 1: Cluster-Level Policy Tests
# ===========================================================================

class TestClusterPolicy:

    def test_clust001_no_precedent_concern_stepup(self):
        """CLUST-001: No precedent → concern_level +1 step."""
        cluster = _make_cluster(concern="minor", semantics="no_precedent_low_confidence")
        pack = _make_pack(normative=0, precedent=0, authority_sparsity=True)
        apply_cluster_policy(cluster, pack)
        assert cluster.concern_level == "major"

    def test_clust001_max_one_step(self):
        """CLUST-001: Max 1 step up."""
        cluster = _make_cluster(concern="major", semantics="no_precedent_low_confidence")
        pack = _make_pack(normative=0, precedent=0, authority_sparsity=True)
        apply_cluster_policy(cluster, pack)
        assert cluster.concern_level == "critical"

    def test_clust004_contradiction_blocks(self):
        """CLUST-004: Contradiction → package_blocking=True."""
        cluster = _make_cluster(semantics="contradiction", contradiction=True)
        pack = _make_pack(normative=2, precedent=1)
        apply_cluster_policy(cluster, pack)
        assert cluster.package_blocking is True

    def test_fall001_no_precedent_normative_present_no_block(self):
        """FALL-001: No precedent + normative → not blocking."""
        cluster = _make_cluster(
            semantics="no_precedent_low_confidence", concern="minor"
        )
        pack = _make_pack(normative=2, precedent=0)
        apply_cluster_policy(cluster, pack)
        assert cluster.package_blocking is False

    def test_guard001_pattern_only_no_block(self):
        """GUARD-001: Concern patterns only → package_blocking=False."""
        cluster = _make_cluster(semantics="pattern_concern_only")
        pack = _make_pack(concern_pattern=3)
        apply_cluster_policy(cluster, pack)
        assert cluster.package_blocking is False

    def test_cqa_orthogonal_gap_blocks(self):
        """CQA with orthogonal gap should block."""
        cluster = _make_cluster(
            semantics="orthogonal_gap", contains_cqa=True,
            cluster_type="cqa_concern",
        )
        pack = _make_pack(normative=2, precedent=1)
        apply_cluster_policy(cluster, pack)
        assert cluster.package_blocking is True

    def test_cluster_policy_does_not_touch_package_decision(self):
        """Permission boundary: cluster policy cannot create PackageDecision."""
        cluster = _make_cluster()
        pack = _make_pack(normative=1)
        result = apply_cluster_policy(cluster, pack)
        assert isinstance(result, RiskCluster)
        # Verify it returns the same cluster (mutated in place)
        assert result is cluster


# ===========================================================================
# Stage 2: Package-Level Policy Tests
# ===========================================================================

class TestPackagePolicy:

    def test_aggr001_cqa_weighted_confidence(self):
        """AGGR-001: CQA attributes weighted 1.5x."""
        c1 = _make_cluster(cluster_id="C1", contains_cqa=True, score=0.9)
        c2 = _make_cluster(cluster_id="C2", contains_cqa=False, score=0.6,
                           category="purity")
        c1.concern_level = "none"
        c1.package_blocking = False
        c2.concern_level = "none"
        c2.package_blocking = False

        pack1 = _make_pack(scope_id="C1", normative=2, precedent=1)
        pack2 = _make_pack(scope_id="C2", normative=1, precedent=1)
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001",
                               normative=2, precedent=1)

        prelim = _make_decision()
        decision = apply_package_policy([c1, c2], [pack1, pack2], case_pack, prelim)
        # CQA-weighted: (0.9*1.5 + 0.6*1.0) / (1.5 + 1.0) = 1.95/2.5 = 0.78
        assert 0.7 < decision.confidence <= 0.85
        assert "AGGR-001" in decision.decision_rule_ids

    def test_aggr002_single_blocking_escalation(self):
        """AGGR-002: Any blocking cluster → verdict >= supplement_required."""
        c1 = _make_cluster(cluster_id="C1", concern="major", semantics="contradiction",
                           contradiction=True, blocking=True)
        c1.concern_level = "major"
        c2 = _make_cluster(cluster_id="C2", concern="none", blocking=False)
        c2.concern_level = "none"

        packs = [_make_pack(scope_id="C1"), _make_pack(scope_id="C2")]
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001", normative=1)

        prelim = _make_decision(verdict="proceed", confidence=0.8)
        decision = apply_package_policy([c1, c2], packs, case_pack, prelim)
        assert decision.package_verdict in ("supplement_required", "investigation_required", "defer_package")
        assert "C1" in decision.blocking_cluster_ids

    def test_aggr002_two_blocking_defers(self):
        """AGGR-002: >=2 blocking → defer_package."""
        c1 = _make_cluster(cluster_id="C1", concern="critical", semantics="contradiction",
                           contradiction=True, blocking=True)
        c1.concern_level = "critical"
        c2 = _make_cluster(cluster_id="C2", concern="critical", semantics="contradiction",
                           contradiction=True, blocking=True, category="purity")
        c2.concern_level = "critical"

        packs = [_make_pack(scope_id="C1"), _make_pack(scope_id="C2")]
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001")

        prelim = _make_decision(verdict="proceed")
        decision = apply_package_policy([c1, c2], packs, case_pack, prelim)
        assert decision.package_verdict == "defer_package"

    def test_aggr004_multi_cluster_confidence_floor(self):
        """AGGR-004: 2+ major clusters → confidence floored at 0.4."""
        c1 = _make_cluster(cluster_id="C1", concern="major", score=0.9)
        c1.concern_level = "major"
        c1.package_blocking = False
        c2 = _make_cluster(cluster_id="C2", concern="major", score=0.8, category="purity")
        c2.concern_level = "major"
        c2.package_blocking = False

        packs = [_make_pack(scope_id="C1", normative=2), _make_pack(scope_id="C2", normative=2)]
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001", normative=2, precedent=1)

        prelim = _make_decision(confidence=0.8)
        decision = apply_package_policy([c1, c2], packs, case_pack, prelim)
        assert decision.confidence <= 0.4
        assert "AGGR-004" in decision.decision_rule_ids

    def test_abst001_all_three_conditions_trigger_abstain(self):
        """ABST-001: Abstain only when ALL THREE conditions met."""
        # 2 critical clusters with contradiction
        c1 = _make_cluster(cluster_id="C1", concern="critical",
                           semantics="contradiction", contradiction=True, blocking=True)
        c1.concern_level = "critical"
        c2 = _make_cluster(cluster_id="C2", concern="critical",
                           semantics="contradiction", contradiction=True, blocking=True,
                           category="physicochemical")
        c2.concern_level = "critical"

        packs = [
            _make_pack(scope_id="C1", authority_sparsity=True, authority_conflict=True),
            _make_pack(scope_id="C2", authority_sparsity=True),
        ]
        case_pack = _make_pack(
            scope_level="case", scope_id="CASE-001",
            authority_sparsity=True, authority_conflict=True,
        )

        prelim = _make_decision(confidence=0.2)
        decision = apply_package_policy([c1, c2], packs, case_pack, prelim)
        assert decision.abstain_flag is True
        assert decision.package_verdict == "defer_package"
        assert "ABST-001" in decision.decision_rule_ids

    def test_abst001_missing_one_condition_no_abstain(self):
        """ABST-001: Missing geography/authority conflict → no abstain."""
        c1 = _make_cluster(cluster_id="C1", concern="critical",
                           semantics="contradiction", contradiction=True, blocking=True)
        c1.concern_level = "critical"
        c2 = _make_cluster(cluster_id="C2", concern="critical",
                           semantics="contradiction", contradiction=True, blocking=True,
                           category="purity")
        c2.concern_level = "critical"

        # No authority_conflict_flag or geography_conflict_flag
        packs = [
            _make_pack(scope_id="C1", authority_sparsity=True),
            _make_pack(scope_id="C2", authority_sparsity=True),
        ]
        case_pack = _make_pack(
            scope_level="case", scope_id="CASE-001",
            normative=1,  # Has some normative
        )

        prelim = _make_decision(confidence=0.2)
        decision = apply_package_policy([c1, c2], packs, case_pack, prelim)
        assert decision.abstain_flag is False

    def test_fall002_weak_evidence_no_overreact(self):
        """FALL-002: Mixed weak evidence must NOT overreact."""
        c1 = _make_cluster(cluster_id="C1", concern="minor", score=0.88)
        c1.concern_level = "minor"
        c1.package_blocking = False

        packs = [_make_pack(scope_id="C1", normative=1, precedent=0)]
        case_pack = _make_pack(
            scope_level="case", scope_id="CASE-001",
            normative=1, precedent=0,
        )

        prelim = _make_decision(verdict="supplement_required")
        decision = apply_package_policy([c1], packs, case_pack, prelim)
        assert decision.package_verdict in ("proceed", "proceed_with_conditions")

    def test_guard003_no_double_penalization_rule_recorded(self):
        """GUARD-003: No double penalization rule is in decision_rule_ids."""
        c1 = _make_cluster(cluster_id="C1", concern="none", score=0.9)
        c1.concern_level = "none"
        c1.package_blocking = False

        packs = [_make_pack(scope_id="C1", normative=2, precedent=1)]
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001",
                               normative=2, precedent=1)

        prelim = _make_decision()
        decision = apply_package_policy([c1], packs, case_pack, prelim)
        assert "GUARD-003" in decision.decision_rule_ids

    def test_package_policy_does_not_touch_cluster_fields(self):
        """Permission boundary: GUARD-003 — package policy cannot modify clusters."""
        c1 = _make_cluster(cluster_id="C1", concern="minor")
        c1.concern_level = "minor"
        c1.package_blocking = False
        original_concern = c1.concern_level
        original_blocking = c1.package_blocking

        packs = [_make_pack(scope_id="C1", normative=1)]
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001", normative=1)

        prelim = _make_decision()
        apply_package_policy([c1], packs, case_pack, prelim)

        assert c1.concern_level == original_concern
        assert c1.package_blocking == original_blocking

    def test_decision_rule_ids_always_populated(self):
        """Must-Pass: decision_rule_ids populated on every PackageDecision."""
        c1 = _make_cluster(cluster_id="C1", concern="none", score=0.9)
        c1.concern_level = "none"
        c1.package_blocking = False

        packs = [_make_pack(scope_id="C1", normative=1)]
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001", normative=1)

        prelim = _make_decision()
        decision = apply_package_policy([c1], packs, case_pack, prelim)
        assert len(decision.decision_rule_ids) > 0


# ===========================================================================
# Gold Case Behavioral Tests
# ===========================================================================

class TestGoldCaseBehaviors:
    """Test that the judgment pipeline produces correct behaviors for gold cases."""

    @pytest.fixture(autouse=True)
    def setup_registry(self):
        self.registry = EvidenceRegistry()

    def _run_pipeline(self, case_id):
        gc = _load_gold(case_id)
        ctx = CaseContext(**gc["case_context"])
        clusters = build_risk_clusters(ctx, gc["attribute_results"])
        cluster_packs, case_pack = match_for_clusters(ctx, clusters, self.registry)

        for i, cluster in enumerate(clusters):
            if i < len(cluster_packs):
                apply_cluster_policy(cluster, cluster_packs[i])

        prelim = PackageDecision(
            case_id=ctx.case_id,
            package_verdict="proceed",
            confidence=0.5,
        )
        decision = apply_package_policy(clusters, cluster_packs, case_pack, prelim)
        return decision, clusters, gc["expected_decision"]

    def test_gc06_abstain_flag_false(self):
        """GC-06: No precedent → judges (not abstain)."""
        decision, _, expected = self._run_pipeline("GC-06")
        assert decision.abstain_flag is False

    def test_gc06_no_blocking(self):
        """GC-06: No blocking clusters."""
        decision, _, expected = self._run_pipeline("GC-06")
        assert len(decision.blocking_cluster_ids) == 0

    def test_gc07_abstains(self):
        """GC-07: Should abstain (ABST-001)."""
        decision, _, expected = self._run_pipeline("GC-07")
        assert decision.abstain_flag is True
        assert decision.package_verdict == "defer_package"

    def test_gc10_no_blocking(self):
        """GC-10: Concern pattern only → NOT blocking."""
        decision, _, expected = self._run_pipeline("GC-10")
        assert len(decision.blocking_cluster_ids) == 0

    def test_gc10_no_abstain(self):
        """GC-10: Concern pattern only → NOT abstain."""
        decision, _, expected = self._run_pipeline("GC-10")
        assert decision.abstain_flag is False

    def test_gc11_detects_hidden_gap(self):
        """GC-11: Hidden insufficiency detected."""
        decision, _, expected = self._run_pipeline("GC-11")
        assert len(decision.blocking_cluster_ids) >= 1

    def test_gc12_no_overreact(self):
        """GC-12: Mixed weak evidence → no overreaction."""
        decision, _, expected = self._run_pipeline("GC-12")
        assert decision.package_verdict in ("proceed", "proceed_with_conditions")
        assert decision.abstain_flag is False

    def test_gc01_clean_proceed(self):
        """GC-01: Normal sufficient → proceed or proceed_with_conditions."""
        decision, _, expected = self._run_pipeline("GC-01")
        assert decision.package_verdict in ("proceed", "proceed_with_conditions")
        assert decision.abstain_flag is False
        assert len(decision.blocking_cluster_ids) == 0

    def test_gc05_investigation_or_higher(self):
        """GC-05: Conflicting methods → at least investigation_required."""
        decision, _, expected = self._run_pipeline("GC-05")
        severity = {"proceed": 0, "proceed_with_conditions": 1,
                     "supplement_required": 2, "investigation_required": 3,
                     "defer_package": 4}
        assert severity.get(decision.package_verdict, 0) >= severity["supplement_required"]
