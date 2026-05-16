"""
Tests for Step 0A: Judgment Core Refactor — schemas and cluster builder.

Covers:
  - CaseContext immutability and validation
  - RiskCluster construction and validation
  - AuthorityContextPack construction and validation
  - PackageDecision construction and validation
  - Cluster builder formation policy
"""

import pytest

from schemas.case_context import CaseContext
from schemas.risk_cluster import (
    RiskCluster,
    VALID_CLUSTER_TYPES,
    VALID_CONCERN_LEVELS,
    VALID_RISK_SEMANTICS,
)
from schemas.authority_context_pack import (
    AuthorityContextPack,
    RefEntry,
    VALID_SCOPE_LEVELS,
)
from schemas.package_decision import (
    PackageDecision,
    VALID_PACKAGE_VERDICTS,
    compute_confidence_band,
)
from services.cluster_builder import build_risk_clusters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_case_context(**overrides):
    defaults = dict(
        molecule_class="mAb",
        change_type="process_change",
        change_description="Scale-up from 2L to 2000L",
        lifecycle_stage="Phase_III",
        flagged_attribute_ids=["attr_1"],
        flagged_categories=["potency"],
        identified_gaps=["orthogonal_gap"],
    )
    defaults.update(overrides)
    return CaseContext(**defaults)


def _make_attribute(attr_id, category, concern_level="none", is_cqa=False,
                    score=0.5, gaps=None, **extra):
    result = {
        "attribute_id": attr_id,
        "category": category,
        "concern_level": concern_level,
        "is_cqa": is_cqa,
        "score": score,
        "gaps": gaps or [],
    }
    result.update(extra)
    return result


# ===========================================================================
# CaseContext Tests
# ===========================================================================


class TestCaseContext:

    def test_construction_with_required_fields(self):
        ctx = _make_case_context()
        assert ctx.molecule_class == "mAb"
        assert ctx.target_geography == "global"
        assert ctx.case_id.startswith("CASE-")

    def test_immutability_setattr(self):
        ctx = _make_case_context()
        with pytest.raises(AttributeError, match="immutable"):
            ctx.molecule_class = "ADC"

    def test_immutability_delattr(self):
        ctx = _make_case_context()
        with pytest.raises(AttributeError, match="immutable"):
            del ctx.molecule_class

    def test_future_reserved_fields_optional(self):
        ctx = _make_case_context()
        assert ctx.molecule_name is None
        assert ctx.modality is None
        assert ctx.input_completeness_ratio is None

    def test_future_reserved_fields_settable_at_construction(self):
        ctx = _make_case_context(molecule_name="Trastuzumab", modality="injectable")
        assert ctx.molecule_name == "Trastuzumab"
        assert ctx.modality == "injectable"

    def test_custom_geography(self):
        ctx = _make_case_context(target_geography="EU")
        assert ctx.target_geography == "EU"

    def test_empty_molecule_class_rejected(self):
        with pytest.raises(ValueError):
            _make_case_context(molecule_class="")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValueError):
            _make_case_context(change_description="   ")

    def test_non_list_flagged_ids_rejected(self):
        with pytest.raises(TypeError):
            _make_case_context(flagged_attribute_ids="not_a_list")

    def test_auto_generated_case_id_unique(self):
        ctx1 = _make_case_context()
        ctx2 = _make_case_context()
        assert ctx1.case_id != ctx2.case_id


# ===========================================================================
# RiskCluster Tests
# ===========================================================================


class TestRiskCluster:

    def test_valid_construction(self):
        rc = RiskCluster(
            cluster_id="CLU-001",
            cluster_type="category_risk",
            dominant_category="potency",
            affected_attribute_ids=["attr_1", "attr_2"],
            contains_cqa=True,
            base_concern_level="minor",
            cluster_reason_summary="Potency shifts within acceptable range.",
            risk_semantics="sufficient_evidence",
        )
        assert rc.cluster_id == "CLU-001"
        assert rc.orthogonal_support_level is None
        assert rc.package_blocking is None

    def test_invalid_cluster_type(self):
        with pytest.raises(ValueError, match="cluster_type"):
            RiskCluster(
                cluster_id="CLU-002",
                cluster_type="invalid_type",
                dominant_category="potency",
                affected_attribute_ids=["attr_1"],
                contains_cqa=False,
                base_concern_level="none",
                cluster_reason_summary="Test.",
                risk_semantics="sufficient_evidence",
            )

    def test_invalid_risk_semantics(self):
        with pytest.raises(ValueError, match="risk_semantics"):
            RiskCluster(
                cluster_id="CLU-003",
                cluster_type="category_risk",
                dominant_category="potency",
                affected_attribute_ids=["attr_1"],
                contains_cqa=False,
                base_concern_level="none",
                cluster_reason_summary="Test.",
                risk_semantics="invalid_semantic",
            )

    def test_empty_reason_summary_rejected(self):
        with pytest.raises(ValueError, match="cluster_reason_summary"):
            RiskCluster(
                cluster_id="CLU-004",
                cluster_type="category_risk",
                dominant_category="potency",
                affected_attribute_ids=["attr_1"],
                contains_cqa=False,
                base_concern_level="none",
                cluster_reason_summary="",
                risk_semantics="sufficient_evidence",
            )

    def test_invalid_concern_level(self):
        with pytest.raises(ValueError, match="base_concern_level"):
            RiskCluster(
                cluster_id="CLU-005",
                cluster_type="category_risk",
                dominant_category="potency",
                affected_attribute_ids=["attr_1"],
                contains_cqa=False,
                base_concern_level="extreme",
                cluster_reason_summary="Test.",
                risk_semantics="sufficient_evidence",
            )

    def test_progressive_fields_mutable(self):
        rc = RiskCluster(
            cluster_id="CLU-006",
            cluster_type="category_risk",
            dominant_category="purity",
            affected_attribute_ids=["attr_1"],
            contains_cqa=False,
            base_concern_level="none",
            cluster_reason_summary="Purity acceptable.",
            risk_semantics="sufficient_evidence",
        )
        rc.orthogonal_support_level = "strong"
        rc.package_blocking = False
        rc.concern_level = "minor"
        assert rc.orthogonal_support_level == "strong"
        assert rc.package_blocking is False

    def test_all_cluster_types_valid(self):
        for ct in VALID_CLUSTER_TYPES:
            rc = RiskCluster(
                cluster_id=f"CLU-{ct}",
                cluster_type=ct,
                dominant_category="potency",
                affected_attribute_ids=["attr_1"],
                contains_cqa=False,
                base_concern_level="none",
                cluster_reason_summary=f"Test {ct}.",
                risk_semantics="sufficient_evidence",
            )
            assert rc.cluster_type == ct

    def test_all_risk_semantics_valid(self):
        for rs in VALID_RISK_SEMANTICS:
            rc = RiskCluster(
                cluster_id=f"CLU-{rs}",
                cluster_type="category_risk",
                dominant_category="potency",
                affected_attribute_ids=["attr_1"],
                contains_cqa=False,
                base_concern_level="none",
                cluster_reason_summary=f"Test {rs}.",
                risk_semantics=rs,
            )
            assert rc.risk_semantics == rs


# ===========================================================================
# AuthorityContextPack Tests
# ===========================================================================


class TestAuthorityContextPack:

    def test_valid_construction(self):
        pack = AuthorityContextPack(
            pack_id="PACK-001",
            scope_level="cluster",
            scope_id="CLU-001",
        )
        assert pack.pack_id == "PACK-001"
        assert pack.normative_refs == []
        assert pack.authority_conflict_flag is False

    def test_invalid_scope_level(self):
        with pytest.raises(ValueError, match="scope_level"):
            AuthorityContextPack(
                pack_id="PACK-002",
                scope_level="invalid",
                scope_id="CLU-001",
            )

    def test_case_scope_level(self):
        pack = AuthorityContextPack(
            pack_id="PACK-003",
            scope_level="case",
            scope_id="CASE-001",
        )
        assert pack.scope_level == "case"

    def test_ref_entry_construction(self):
        ref = RefEntry(
            entry_id="REF-001",
            title="ICH Q5E",
            source="ICH",
            authority_quality_tier="primary",
            relevance_score=0.95,
            decision_relevance_note="Directly addresses CQA comparability.",
        )
        assert ref.entry_id == "REF-001"
        assert ref.authority_quality_tier == "primary"

    def test_pack_with_refs(self):
        ref = RefEntry(
            entry_id="REF-001",
            title="ICH Q5E",
            source="ICH",
            authority_quality_tier="primary",
            relevance_score=0.95,
            decision_relevance_note="Direct applicability.",
        )
        pack = AuthorityContextPack(
            pack_id="PACK-004",
            scope_level="cluster",
            scope_id="CLU-001",
            normative_refs=[ref],
            top_decision_drivers=[ref],
            n_refs_by_type={"normative": 1},
        )
        assert len(pack.normative_refs) == 1
        assert len(pack.top_decision_drivers) == 1
        assert pack.n_refs_by_type["normative"] == 1

    def test_generate_pack_id(self):
        pid = AuthorityContextPack.generate_pack_id()
        assert pid.startswith("PACK-")

    def test_no_verdict_direction_fields(self):
        """AuthorityContextPack must NOT have verdict-direction fields."""
        pack = AuthorityContextPack(
            pack_id="PACK-005",
            scope_level="cluster",
            scope_id="CLU-001",
        )
        assert not hasattr(pack, "confidence_modifier")
        assert not hasattr(pack, "support_direction")
        assert not hasattr(pack, "verdict_implication")


# ===========================================================================
# PackageDecision Tests
# ===========================================================================


class TestPackageDecision:

    def test_valid_construction(self):
        pd = PackageDecision(
            case_id="CASE-001",
            package_verdict="proceed",
            confidence=0.9,
        )
        assert pd.confidence_band == "high"
        assert pd.abstain_flag is False

    def test_confidence_band_auto_derived(self):
        assert PackageDecision(
            case_id="C1", package_verdict="proceed", confidence=0.85
        ).confidence_band == "high"
        assert PackageDecision(
            case_id="C2", package_verdict="proceed_with_conditions", confidence=0.7
        ).confidence_band == "moderate"
        assert PackageDecision(
            case_id="C3", package_verdict="defer_package", confidence=0.3
        ).confidence_band == "low"

    def test_confidence_band_boundary(self):
        # 0.8 exactly is moderate (> 0.8 is high).
        assert compute_confidence_band(0.8) == "moderate"
        assert compute_confidence_band(0.81) == "high"
        assert compute_confidence_band(0.5) == "moderate"
        assert compute_confidence_band(0.49) == "low"

    def test_invalid_verdict(self):
        with pytest.raises(ValueError, match="package_verdict"):
            PackageDecision(
                case_id="CASE-002",
                package_verdict="approve",
                confidence=0.9,
            )

    def test_confidence_out_of_range(self):
        with pytest.raises(ValueError, match="confidence"):
            PackageDecision(
                case_id="CASE-003",
                package_verdict="proceed",
                confidence=1.5,
            )

    def test_all_verdicts_valid(self):
        for v in VALID_PACKAGE_VERDICTS:
            pd = PackageDecision(
                case_id="CASE-X",
                package_verdict=v,
                confidence=0.5,
            )
            assert pd.package_verdict == v

    def test_what_would_change_verdict_structure(self):
        pd = PackageDecision(
            case_id="CASE-004",
            package_verdict="supplement_required",
            confidence=0.6,
            what_would_change_verdict=[{
                "cluster_id": "CLU-001",
                "current_gap": "orthogonal_gap",
                "if_gap_resolved": "orthogonal potency data comparable",
                "verdict_would_become": "proceed_with_conditions",
                "confidence_delta": 0.15,
            }],
        )
        assert len(pd.what_would_change_verdict) == 1
        assert pd.what_would_change_verdict[0]["cluster_id"] == "CLU-001"


# ===========================================================================
# Cluster Builder Tests
# ===========================================================================


class TestClusterBuilder:

    def test_basic_category_risk_clusters(self):
        """One category_risk per category when no escalation triggers."""
        ctx = _make_case_context(
            flagged_attribute_ids=["a1", "a2"],
            flagged_categories=["potency", "purity"],
            identified_gaps=[],
        )
        attrs = [
            _make_attribute("a1", "potency", "minor", is_cqa=True, score=0.3),
            _make_attribute("a2", "purity", "none", score=0.1),
        ]
        clusters = build_risk_clusters(ctx, attrs)
        cat_risk = [c for c in clusters if c.cluster_type == "category_risk"]
        assert len(cat_risk) == 2
        categories = {c.dominant_category for c in cat_risk}
        assert "potency" in categories
        assert "purity" in categories

    def test_cqa_escalation(self):
        """CQA with concern >= major creates separate cqa_concern."""
        ctx = _make_case_context(
            flagged_attribute_ids=["a1", "a2"],
            flagged_categories=["potency"],
            identified_gaps=[],
        )
        attrs = [
            _make_attribute("a1", "potency", "major", is_cqa=True, score=0.7),
            _make_attribute("a2", "potency", "minor", is_cqa=False, score=0.3),
        ]
        clusters = build_risk_clusters(ctx, attrs)
        cqa_clusters = [c for c in clusters if c.cluster_type == "cqa_concern"]
        assert len(cqa_clusters) == 1
        assert cqa_clusters[0].contains_cqa is True
        assert "a1" in cqa_clusters[0].affected_attribute_ids

    def test_single_attribute_critical(self):
        """concern = critical creates single_attribute_critical."""
        ctx = _make_case_context(
            flagged_attribute_ids=["a1"],
            flagged_categories=["potency"],
            identified_gaps=[],
        )
        attrs = [
            _make_attribute("a1", "potency", "critical", is_cqa=True, score=0.9),
        ]
        clusters = build_risk_clusters(ctx, attrs)
        crit = [c for c in clusters if c.cluster_type == "single_attribute_critical"]
        assert len(crit) == 1
        assert crit[0].base_concern_level == "critical"

    def test_cross_category_gap(self):
        """2+ categories with same gap type creates cross_category_gap."""
        ctx = _make_case_context(
            flagged_attribute_ids=["a1", "a2"],
            flagged_categories=["potency", "purity"],
            identified_gaps=[],
        )
        attrs = [
            _make_attribute("a1", "potency", "minor", score=0.3, gaps=["orthogonal_gap"]),
            _make_attribute("a2", "purity", "minor", score=0.2, gaps=["orthogonal_gap"]),
        ]
        clusters = build_risk_clusters(ctx, attrs)
        xcat = [c for c in clusters if c.cluster_type == "cross_category_gap"]
        assert len(xcat) == 1
        assert "a1" in xcat[0].affected_attribute_ids or "a2" in xcat[0].affected_attribute_ids

    def test_every_cluster_has_reason_and_semantics(self):
        """Mandatory: non-empty cluster_reason_summary and risk_semantics."""
        ctx = _make_case_context(
            flagged_attribute_ids=["a1", "a2", "a3"],
            flagged_categories=["potency", "purity", "glycosylation"],
            identified_gaps=["orthogonal_gap"],
        )
        attrs = [
            _make_attribute("a1", "potency", "major", is_cqa=True, score=0.7),
            _make_attribute("a2", "purity", "none", score=0.1),
            _make_attribute("a3", "glycosylation", "minor", score=0.3, gaps=["orthogonal_gap"]),
        ]
        clusters = build_risk_clusters(ctx, attrs)
        for c in clusters:
            assert c.cluster_reason_summary, f"Empty reason on {c.cluster_id}"
            assert c.risk_semantics, f"Empty semantics on {c.cluster_id}"
            assert c.risk_semantics in VALID_RISK_SEMANTICS, (
                f"Invalid semantics {c.risk_semantics} on {c.cluster_id}"
            )

    def test_critical_not_in_category_risk(self):
        """Critical attributes should be escalated out of category_risk."""
        ctx = _make_case_context(
            flagged_attribute_ids=["a1", "a2"],
            flagged_categories=["potency"],
            identified_gaps=[],
        )
        attrs = [
            _make_attribute("a1", "potency", "critical", is_cqa=True, score=0.9),
            _make_attribute("a2", "potency", "minor", score=0.2),
        ]
        clusters = build_risk_clusters(ctx, attrs)
        cat_risk = [c for c in clusters if c.cluster_type == "category_risk"]
        for c in cat_risk:
            assert "a1" not in c.affected_attribute_ids

    def test_base_cluster_score_populated(self):
        """base_cluster_score should be set on all clusters."""
        ctx = _make_case_context(
            flagged_attribute_ids=["a1"],
            flagged_categories=["potency"],
            identified_gaps=[],
        )
        attrs = [
            _make_attribute("a1", "potency", "minor", score=0.5),
        ]
        clusters = build_risk_clusters(ctx, attrs)
        for c in clusters:
            assert c.base_cluster_score is not None

    def test_empty_attributes_returns_empty(self):
        ctx = _make_case_context(
            flagged_attribute_ids=[],
            flagged_categories=[],
            identified_gaps=[],
        )
        clusters = build_risk_clusters(ctx, [])
        assert clusters == []

    def test_risk_semantics_orthogonal_gap(self):
        """CQA with orthogonal gap should get orthogonal_gap semantics."""
        ctx = _make_case_context(
            flagged_attribute_ids=["a1"],
            flagged_categories=["potency"],
            identified_gaps=["orthogonal_gap"],
        )
        attrs = [
            _make_attribute("a1", "potency", "major", is_cqa=True, score=0.7),
        ]
        clusters = build_risk_clusters(ctx, attrs)
        cqa_clusters = [c for c in clusters if c.cluster_type == "cqa_concern"]
        assert len(cqa_clusters) == 1
        assert cqa_clusters[0].risk_semantics == "orthogonal_gap"

    def test_favorable_shift_semantics(self):
        """Attribute with favorable_shift should produce correct semantics."""
        ctx = _make_case_context(
            flagged_attribute_ids=["a1"],
            flagged_categories=["purity"],
            identified_gaps=[],
        )
        attrs = [
            _make_attribute("a1", "purity", "minor", score=0.3,
                            favorable_shift=True),
        ]
        clusters = build_risk_clusters(ctx, attrs)
        cat = [c for c in clusters if c.cluster_type == "category_risk"]
        assert len(cat) == 1
        assert cat[0].risk_semantics == "favorable_shift_requires_rationale"

    def test_contradiction_semantics(self):
        """Attribute with contradiction_present should produce contradiction."""
        ctx = _make_case_context(
            flagged_attribute_ids=["a1"],
            flagged_categories=["potency"],
            identified_gaps=[],
        )
        attrs = [
            _make_attribute("a1", "potency", "critical", is_cqa=True, score=0.9,
                            contradiction_present=True),
        ]
        clusters = build_risk_clusters(ctx, attrs)
        crit = [c for c in clusters if c.cluster_type == "single_attribute_critical"]
        assert len(crit) == 1
        assert crit[0].risk_semantics == "contradiction"
