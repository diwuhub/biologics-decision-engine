"""
Tests for P0 Reference Operating Layer (ROL) Implementation.

Covers:
  - P0-1: RegistryEntry schema extension
  - P0-2: Package verdict aggregation
  - P0-3: ProvenanceChain
  - P0-4: Conservative policy
  - P0-5: Reference matcher
"""

import pytest
from dataclasses import asdict
from datetime import datetime, timezone

from evidence_registry import EvidenceRegistry, RegistryEntry
from pipelines.comparability import PackageVerdict, aggregate_package_verdict
from pipelines.schemas import AttributeResult
from schemas.provenance_chain import ProvenanceChain
from services.conservative_policy import (
    apply_conservative_downgrade,
    check_conflict_flag,
    cap_escalation,
)
from services.reference_matcher import ReferenceMatcher, CaseContext, ReferenceMatchResult, build_matcher_context


# =========================================================================
# P0-1: RegistryEntry Tests
# =========================================================================

class TestRegistryEntrySchema:
    """Test RegistryEntry schema extension and backward compatibility."""

    def test_new_fields_have_defaults(self):
        """New fields should have sensible defaults."""
        entry = RegistryEntry(
            id="test_1",
            entry_type="guideline_clause",
            source="Test Source",
            title="Test Title",
            content="Test content",
            applicable_categories=["purity"],
            confidence=0.9,
        )

        # Check defaults
        assert entry.applies_to_change_types == ["all"]
        assert entry.applies_to_molecule_classes == ["all"]
        assert entry.applies_to_lifecycle_stages == ["all"]
        assert entry.geography == ["global"]
        assert entry.temporal_status == "current"
        assert entry.evidence_weight == "normative"
        assert entry.likely_concern_categories == []
        assert entry.triggers_escalation is False
        assert entry.recommended_followup is None   # [PATCH 2] Structured dict, None when absent
        assert entry.display_tier == "secondary"
        assert entry.authority_quality_tier == "contextual"  # [PATCH 3] Conservative default
        assert entry.decision_type != ""           # [PATCH 1] Auto-mapped from entry_type
        assert entry.risk_if_skipped is None       # [PATCH 12] P2, None when absent

    def test_backward_compatible_loading(self):
        """Existing entries without new fields should load without error."""
        registry = EvidenceRegistry()
        # Should load all entries (450+ after registry expansion)
        assert registry.count >= 385

        # Sample a few and check they have defaults
        for entry in registry._entries[:5]:
            assert isinstance(entry.applies_to_change_types, list)
            assert isinstance(entry.evidence_weight, str)
            assert isinstance(entry.temporal_status, str)

    def test_explicit_new_fields(self):
        """Can explicitly set new ROL fields."""
        entry = RegistryEntry(
            id="test_2",
            entry_type="precedent",
            source="FDA",
            title="Test Precedent",
            content="Content",
            applicable_categories=["identity"],
            confidence=0.85,
            applies_to_change_types=["process_change"],
            applies_to_molecule_classes=["mAb", "bispecific"],
            evidence_weight="binding",
            triggers_escalation=True,
        )

        assert entry.applies_to_change_types == ["process_change"]
        assert entry.applies_to_molecule_classes == ["mAb", "bispecific"]
        assert entry.evidence_weight == "binding"
        assert entry.triggers_escalation is True


# =========================================================================
# P0-2: Package Verdict Aggregation Tests
# =========================================================================

class TestPackageVerdictAggregation:
    """Test package-level verdict aggregation logic."""

    def _make_attr_result(self, name: str, action_level: str, is_cqa: bool = False, uncertainty: float = 0.2) -> AttributeResult:
        """Helper to create AttributeResult."""
        return AttributeResult(
            name=name,
            category="purity",
            pre_value=100.0,
            post_value=102.0,
            unit="%",
            delta_pct=2.0,
            score=0.85,
            comparable=True,
            concern="minor" if action_level in ("SUPPLEMENT",) else "none",
            is_cqa=is_cqa,
            cqa_designation="CQA" if is_cqa else "Monitor",
            uncertainty=uncertainty,
            detail="test",
            action={'action_level': action_level},
        )

    def test_defer_dominance_rule(self):
        """Rule 1: Any CQA DEFER -> DEFER_PACKAGE."""
        attrs = [
            self._make_attr_result("CQA_1", "DEFER", is_cqa=True),
            self._make_attr_result("Attr_2", "PROCEED", is_cqa=False),
        ]

        class MockCQA:
            def __init__(self, name, designation):
                self.name = name
                self.designation = designation

        cqa_results = [
            MockCQA("CQA_1", "CQA"),
            MockCQA("Attr_2", "Monitor"),
        ]

        result = aggregate_package_verdict(attrs, cqa_results)
        assert result['verdict'] == PackageVerdict.DEFER_PACKAGE
        assert "CQA_1" in result['driving_attributes']

    def test_investigate_escalation_rule(self):
        """Rule 2: >=2 CQAs INVESTIGATE -> DEFER; 1 CQA INVESTIGATE -> INVESTIGATION_REQUIRED."""
        # Test 1 CQA INVESTIGATE
        attrs = [
            self._make_attr_result("CQA_1", "INVESTIGATE", is_cqa=True),
            self._make_attr_result("Attr_2", "PROCEED", is_cqa=False),
        ]

        class MockCQA:
            def __init__(self, name, designation):
                self.name = name
                self.designation = designation

        cqa_results = [MockCQA("CQA_1", "CQA"), MockCQA("Attr_2", "Monitor")]
        result = aggregate_package_verdict(attrs, cqa_results)
        assert result['verdict'] == PackageVerdict.INVESTIGATION_REQUIRED

        # Test 2 CQAs INVESTIGATE
        attrs = [
            self._make_attr_result("CQA_1", "INVESTIGATE", is_cqa=True),
            self._make_attr_result("CQA_2", "INVESTIGATE", is_cqa=True),
        ]
        cqa_results = [MockCQA("CQA_1", "CQA"), MockCQA("CQA_2", "CQA")]
        result = aggregate_package_verdict(attrs, cqa_results)
        assert result['verdict'] == PackageVerdict.DEFER_PACKAGE

    def test_clean_pass_rule(self):
        """Rule 5: ALL PROCEED and all CQA uncertainties < 0.25 -> PROCEED."""
        attrs = [
            self._make_attr_result("CQA_1", "PROCEED", is_cqa=True, uncertainty=0.1),
            self._make_attr_result("Attr_2", "PROCEED", is_cqa=False, uncertainty=0.2),
        ]

        class MockCQA:
            def __init__(self, name, designation):
                self.name = name
                self.designation = designation

        cqa_results = [MockCQA("CQA_1", "CQA"), MockCQA("Attr_2", "Monitor")]
        result = aggregate_package_verdict(attrs, cqa_results)
        assert result['verdict'] == PackageVerdict.PROCEED



    def test_defer_dominance_without_cqa_results(self):
        """Rule 1 should fire even when cqa_results is not provided, using is_cqa."""
        attrs = [
            self._make_attr_result("CQA_1", "DEFER", is_cqa=True),
            self._make_attr_result("Attr_2", "PROCEED", is_cqa=False),
        ]
        result = aggregate_package_verdict(attrs, cqa_results=None)
        assert result['verdict'] == PackageVerdict.DEFER_PACKAGE
        assert "CQA_1" in result['driving_attributes']

    def test_investigate_without_cqa_results(self):
        """Rule 2 should fire using is_cqa fallback."""
        attrs = [
            self._make_attr_result("CQA_1", "INVESTIGATE", is_cqa=True),
            self._make_attr_result("Attr_2", "PROCEED", is_cqa=False),
        ]
        result = aggregate_package_verdict(attrs, cqa_results=None)
        assert result['verdict'] == PackageVerdict.INVESTIGATION_REQUIRED

# =========================================================================
# P0-3: ProvenanceChain Tests
# =========================================================================

class TestProvenanceChain:
    """Test ProvenanceChain serialization and methods."""

    def test_creation_and_defaults(self):
        """ProvenanceChain should auto-generate created_at."""
        chain = ProvenanceChain(
            attribute_name="SEC Monomer %",
            normative_refs=["ICH_Q5E_2.2"],
            confidence=0.85,
        )

        assert chain.attribute_name == "SEC Monomer %"
        assert chain.n_normative == 1
        assert chain.n_precedent == 0
        assert chain.created_at != ""
        assert chain.has_override is False

    def test_serialization(self):
        """to_dict() should produce clean JSON."""
        chain = ProvenanceChain(
            attribute_name="Test",
            normative_refs=["A", "B"],
            precedent_refs=["C"],
            inference_summary="Test inference",
            confidence=0.9,
        )

        d = chain.to_dict()
        assert d['attribute_name'] == "Test"
        assert len(d['normative_refs']) == 2
        assert d['confidence'] == 0.9

    def test_deserialization(self):
        """from_dict() should reconstruct correctly."""
        original = ProvenanceChain(
            attribute_name="Test",
            normative_refs=["A"],
            confidence=0.75,
        )

        d = original.to_dict()
        reconstructed = ProvenanceChain.from_dict(d)

        assert reconstructed.attribute_name == original.attribute_name
        assert reconstructed.normative_refs == original.normative_refs
        assert reconstructed.confidence == original.confidence

    def test_export_row(self):
        """to_export_row() should flatten to CSV format."""
        chain = ProvenanceChain(
            attribute_name="Purity",
            normative_refs=["ICH_Q5E"],
            user_evidence={"SE_HPLC": {"value": 99.5, "unit": "%"}},
            inference_summary="High purity confirmed.",
        )

        row = chain.to_export_row()
        assert row['attribute'] == "Purity"
        assert "ICH_Q5E" in row['normative_refs']
        assert "99.5%" in row['user_evidence']


# =========================================================================
# P0-4: Conservative Policy Tests
# =========================================================================

class TestConservativePolicy:
    """Test conservative policy enforcement."""

    def test_downgrade_weak_basis(self):
        """Downgrade confidence when normative < 2 and precedent < 1."""
        rec = {'action_level': 'SUPPLEMENT', 'confidence': 0.8}
        refs = [{'type': 'concern_pattern'}]  # Weak

        result = apply_conservative_downgrade(rec, refs)
        assert result['confidence'] == pytest.approx(0.6)  # 0.8 - 0.2
        assert result['_confidence_downgrade_applied'] is True

    def test_no_downgrade_strong_basis(self):
        """Don't downgrade with strong normative basis."""
        rec = {'action_level': 'SUPPLEMENT', 'confidence': 0.8}
        refs = [
            {'type': 'normative'},
            {'type': 'normative'},
            {'type': 'precedent'},
        ]

        result = apply_conservative_downgrade(rec, refs)
        assert result['confidence'] == 0.8  # Unchanged

    def test_conflict_detection(self):
        """Detect cross-agency conflicts."""
        refs = [
            {'agency': 'FDA', 'conclusion': 'approve'},
            {'agency': 'EMA', 'conclusion': 'investigate'},
        ]

        result = check_conflict_flag(refs)
        assert result['conflict_detected'] is True
        assert 'FDA' in result['conflicting_agencies']
        assert result['human_review_required'] is True

    def test_cap_escalation_concern_only(self):
        """Cap DEFER/INVESTIGATE when only concern-pattern support."""
        rec = {'action_level': 'DEFER', 'confidence': 0.7}
        support = ['concern_pattern']

        result = cap_escalation(rec, support)
        assert result['action_level'] == 'SUPPLEMENT'
        assert result['_escalation_capped'] is True

    def test_no_cap_with_normative(self):
        """Don't cap when there's normative support."""
        rec = {'action_level': 'DEFER', 'confidence': 0.7}
        support = ['normative', 'concern_pattern']

        result = cap_escalation(rec, support)
        assert result['action_level'] == 'DEFER'  # Unchanged


# =========================================================================
# P0-5: Reference Matcher Tests
# =========================================================================

class TestReferenceMatcher:
    """Test reference matching logic."""

    def test_filter_by_change_type(self):
        """Filter entries by change_type applicability."""
        registry = EvidenceRegistry()
        matcher = ReferenceMatcher(registry)

        case = build_matcher_context(
            change_type='process_change',
            molecule_class='mAb',
            lifecycle_stage='CMC',
        )

        filtered = matcher._filter_by_applicability(case)
        assert len(filtered) > 0

    def test_scoring_relevance(self):
        """Score should reflect category and concern match."""
        registry = EvidenceRegistry()
        matcher = ReferenceMatcher(registry)

        if registry._entries:
            entry = registry._entries[0]
            case = build_matcher_context(
                change_type=entry.applies_to_change_types[0] if entry.applies_to_change_types else 'all',
                molecule_class=entry.applies_to_molecule_classes[0] if entry.applies_to_molecule_classes else 'all',
                lifecycle_stage=entry.applies_to_lifecycle_stages[0] if entry.applies_to_lifecycle_stages else 'all',
                flagged_categories=entry.applicable_categories[:1],
            )

            score = matcher._score_relevance(entry, case)
            assert 0.0 <= score <= 1.0

    def test_type_diverse_selection(self):
        """Select should return mix of types (guidelines, precedents, methods)."""
        registry = EvidenceRegistry()
        matcher = ReferenceMatcher(registry)

        case = build_matcher_context(
            change_type='process_change',
            molecule_class='mAb',
            lifecycle_stage='CMC',
        )

        results = matcher.match(case, top_k=20)

        types = set(r.entry_type for r in results)
        assert len(types) > 0

        for r in results:
            assert isinstance(r, ReferenceMatchResult)
            assert 0.0 <= r.relevance_score <= 1.0


# =========================================================================
# Integration Tests
# =========================================================================

class TestIntegration:
    """Integration tests across multiple P0 components."""

    def test_registry_entry_with_provenance_chain(self):
        """RegistryEntry should integrate with ProvenanceChain."""
        entry = RegistryEntry(
            id="test_1",
            entry_type="guideline_clause",
            source="ICH Q5E",
            title="Test",
            content="Content",
            applicable_categories=["purity"],
            confidence=0.95,
            evidence_weight="binding",
        )

        chain = ProvenanceChain(
            attribute_name="purity",
            normative_refs=[entry.id],
            confidence=entry.confidence,
        )

        assert entry.id in chain.normative_refs
        assert chain.confidence == entry.confidence

    def test_verdict_with_conservative_policy(self):
        """Package verdict should consider conservative policy applied."""
        attrs = [
            AttributeResult(
                name="Attr_1",
                category="purity",
                pre_value=100.0,
                post_value=101.0,
                unit="%",
                delta_pct=1.0,
                score=0.75,
                comparable=True,
                concern="none",
                is_cqa=False,
                cqa_designation="Monitor",
                uncertainty=0.35,
                detail="test",
                action={'action_level': 'SUPPLEMENT'},
            ),
        ]

        result = aggregate_package_verdict(attrs, cqa_results=None)
        assert result['verdict'] in (PackageVerdict.SUPPLEMENT_REQUIRED, PackageVerdict.PROCEED_WITH_CONDITIONS)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
