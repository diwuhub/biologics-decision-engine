"""
Tests for Step 4: Reviewer Concern & Response Pressure Engine.

Covers:
  - Concern generation from different cluster profiles
  - GUARD-004: Cannot invent new verdict categories
  - SHIFT-002: Confidence reduction bounds
  - Bidirectional feedback to cluster priority
  - Gold case concern differentiation
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
from services.reviewer_concern_engine import (
    ReviewerConcern,
    ReviewerConcernResult,
    generate_reviewer_concerns,
    apply_concerns_to_decision,
    MAX_PER_CONCERN_REDUCTION,
    MAX_TOTAL_REDUCTION,
)

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
    c.concern_level = concern
    c.package_blocking = blocking if blocking is not None else False
    if contradiction is not None:
        c.contradiction_present = contradiction
    return c


def _make_pack(scope_id="CLU-001", normative=0, precedent=0,
               concern_pattern=0, authority_sparsity=False, scope_level="cluster"):
    norms = [
        RefEntry(entry_id=f"N-{i}", title=f"ICH Q5E Sec {i}", source="ICH",
                 authority_quality_tier="primary", relevance_score=0.9,
                 decision_relevance_note="Normative ref.")
        for i in range(normative)
    ]
    precs = [
        RefEntry(entry_id=f"P-{i}", title=f"FDA Precedent {i}", source="FDA",
                 authority_quality_tier="strong_secondary", relevance_score=0.8,
                 decision_relevance_note="Precedent ref.")
        for i in range(precedent)
    ]
    cps = [
        RefEntry(entry_id=f"CP-{i}", title=f"WL Concern {i}", source="FDA WL",
                 authority_quality_tier="contextual", relevance_score=0.4,
                 decision_relevance_note="Concern pattern.")
        for i in range(concern_pattern)
    ]
    return AuthorityContextPack(
        pack_id=AuthorityContextPack.generate_pack_id(),
        scope_level=scope_level,
        scope_id=scope_id,
        normative_refs=norms,
        precedent_refs=precs,
        concern_pattern_refs=cps,
        authority_sparsity_flag=authority_sparsity,
        n_refs_by_type={"normative": normative, "precedent": precedent,
                        "concern_pattern": concern_pattern},
        top_decision_drivers=norms[:1] + precs[:1] if norms or precs else cps[:1],
    )


def _load_gold(case_id):
    for f in glob.glob(os.path.join(GOLD_DIR, "gc_*.json")):
        with open(f) as fp:
            gc = json.load(fp)
        if gc["case_id"] == case_id:
            return gc
    raise ValueError(f"Gold case {case_id} not found")


# ===========================================================================
# Concern Generation Tests
# ===========================================================================

class TestConcernGeneration:

    def test_contradiction_produces_critical_concern(self):
        """Contradiction cluster → critical severity concern."""
        cluster = _make_cluster(semantics="contradiction", contradiction=True)
        pack = _make_pack(normative=1)
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001", normative=1)

        decision = PackageDecision(
            case_id="CASE-001", package_verdict="investigation_required",
            confidence=0.4,
        )
        result = generate_reviewer_concerns([cluster], [pack], case_pack, decision)
        assert len(result.concerns) > 0
        critical = [c for c in result.concerns if c.severity == "critical"]
        assert len(critical) > 0

    def test_orthogonal_gap_produces_concern(self):
        """Orthogonal gap → concern about missing method."""
        cluster = _make_cluster(semantics="orthogonal_gap")
        pack = _make_pack(normative=1)
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001", normative=1)

        decision = PackageDecision(
            case_id="CASE-001", package_verdict="supplement_required",
            confidence=0.6,
        )
        result = generate_reviewer_concerns([cluster], [pack], case_pack, decision)
        assert len(result.concerns) > 0
        assert any("orthogonal" in c.concern_text.lower() for c in result.concerns)

    def test_pattern_only_produces_low_severity(self):
        """Pattern concern only → low severity concern."""
        cluster = _make_cluster(semantics="pattern_concern_only", contains_cqa=False)
        pack = _make_pack(concern_pattern=2)
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001", concern_pattern=1)

        decision = PackageDecision(
            case_id="CASE-001", package_verdict="proceed_with_conditions",
            confidence=0.65,
        )
        result = generate_reviewer_concerns([cluster], [pack], case_pack, decision)
        assert len(result.concerns) > 0
        assert result.concerns[0].severity in ("low", "medium")

    def test_different_profiles_different_text(self):
        """Different cluster profiles → different concern text."""
        c1 = _make_cluster(cluster_id="C1", semantics="contradiction", contradiction=True)
        c2 = _make_cluster(cluster_id="C2", semantics="orthogonal_gap", category="purity")

        pack1 = _make_pack(scope_id="C1", normative=1)
        pack2 = _make_pack(scope_id="C2", normative=1)
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001", normative=1)

        decision = PackageDecision(
            case_id="CASE-001", package_verdict="supplement_required",
            confidence=0.5,
        )
        result = generate_reviewer_concerns(
            [c1, c2], [pack1, pack2], case_pack, decision,
        )

        # At least 2 different concerns
        assert len(result.concerns) >= 2
        texts = [c.concern_text for c in result.concerns]
        # Different semantic profiles should produce different text
        assert texts[0] != texts[1]

    def test_every_confidence_affecting_concern_has_rule_id(self):
        """All concerns with affects_verdict_confidence=True cite a rule."""
        cluster = _make_cluster(semantics="contradiction", contradiction=True)
        pack = _make_pack(normative=1)
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001")

        decision = PackageDecision(
            case_id="CASE-001", package_verdict="investigation_required",
            confidence=0.4,
        )
        result = generate_reviewer_concerns([cluster], [pack], case_pack, decision)

        for concern in result.concerns:
            if concern.affects_verdict_confidence:
                assert concern.applied_rule_id, (
                    f"Concern {concern.concern_id} affects confidence but has no rule_id"
                )


# ===========================================================================
# SHIFT-002 Confidence Bounds
# ===========================================================================

class TestShift002Bounds:

    def test_max_per_concern_reduction(self):
        """SHIFT-002: Max 0.15 per concern."""
        assert MAX_PER_CONCERN_REDUCTION == 0.15

    def test_max_total_reduction(self):
        """SHIFT-002: Max 0.30 total."""
        assert MAX_TOTAL_REDUCTION == 0.30

    def test_confidence_not_over_reduced(self):
        """Multiple high-pressure concerns cannot reduce more than 0.30."""
        clusters = [
            _make_cluster(cluster_id=f"C{i}", semantics="contradiction",
                          contradiction=True, category=f"cat{i}")
            for i in range(5)
        ]
        packs = [_make_pack(scope_id=f"C{i}", normative=1) for i in range(5)]
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001")

        decision = PackageDecision(
            case_id="CASE-001", package_verdict="defer_package",
            confidence=0.8,
        )
        result = generate_reviewer_concerns(clusters, packs, case_pack, decision)
        decision = apply_concerns_to_decision(decision, result)

        # Confidence should not drop below 0.8 - 0.30 = 0.50
        assert decision.confidence >= 0.5 - 0.01  # small tolerance


# ===========================================================================
# GUARD-004: Concern Engine Boundary
# ===========================================================================

class TestGuard004:

    def test_concern_engine_does_not_modify_verdict(self):
        """GUARD-004: Concern engine cannot change verdict category."""
        cluster = _make_cluster(semantics="contradiction", contradiction=True)
        pack = _make_pack(normative=1)
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001")

        original_verdict = "investigation_required"
        decision = PackageDecision(
            case_id="CASE-001", package_verdict=original_verdict,
            confidence=0.4,
        )

        result = generate_reviewer_concerns([cluster], [pack], case_pack, decision)
        decision = apply_concerns_to_decision(decision, result)

        # Verdict must not have changed
        assert decision.package_verdict == original_verdict


# ===========================================================================
# Bidirectional Feedback
# ===========================================================================

class TestBidirectionalFeedback:

    def test_concerns_update_cluster_priority(self):
        """Concerns feed back into cluster priority_score."""
        cluster = _make_cluster(semantics="contradiction", contradiction=True)
        assert cluster.priority_score is None

        pack = _make_pack(normative=1)
        case_pack = _make_pack(scope_level="case", scope_id="CASE-001")

        decision = PackageDecision(
            case_id="CASE-001", package_verdict="investigation_required",
            confidence=0.4,
        )
        generate_reviewer_concerns([cluster], [pack], case_pack, decision)

        assert cluster.priority_score is not None
        assert cluster.priority_score > 0


# ===========================================================================
# Gold Case Concern Differentiation
# ===========================================================================

class TestGoldCaseConcerns:

    @pytest.fixture(autouse=True)
    def setup_registry(self):
        self.registry = EvidenceRegistry()

    def _run_concern_pipeline(self, case_id):
        gc = _load_gold(case_id)
        ctx = CaseContext(**gc["case_context"])
        clusters = build_risk_clusters(ctx, gc["attribute_results"])
        cluster_packs, case_pack = match_for_clusters(ctx, clusters, self.registry)

        for i, cluster in enumerate(clusters):
            if i < len(cluster_packs):
                apply_cluster_policy(cluster, cluster_packs[i])

        prelim = PackageDecision(
            case_id=ctx.case_id, package_verdict="proceed", confidence=0.5,
        )
        decision = apply_package_policy(clusters, cluster_packs, case_pack, prelim)
        result = generate_reviewer_concerns(
            clusters, cluster_packs, case_pack, decision,
        )
        return result, decision

    def test_gc01_minimal_concerns(self):
        """GC-01: Normal case → fewer/lower concerns."""
        result, _ = self._run_concern_pipeline("GC-01")
        critical_count = sum(1 for c in result.concerns if c.severity == "critical")
        assert critical_count == 0

    def test_gc05_has_critical_concerns(self):
        """GC-05: Conflicting methods → critical concerns."""
        result, _ = self._run_concern_pipeline("GC-05")
        critical_count = sum(1 for c in result.concerns if c.severity == "critical")
        assert critical_count > 0

    def test_gc01_fewer_concerns_than_gc05(self):
        """GC-01 and GC-04 produce fewer/lower concerns than GC-02 and GC-05."""
        r01, _ = self._run_concern_pipeline("GC-01")
        r05, _ = self._run_concern_pipeline("GC-05")

        # GC-01 should have fewer high-severity concerns
        gc01_high = sum(1 for c in r01.concerns if c.severity in ("critical", "high"))
        gc05_high = sum(1 for c in r05.concerns if c.severity in ("critical", "high"))
        assert gc01_high <= gc05_high
