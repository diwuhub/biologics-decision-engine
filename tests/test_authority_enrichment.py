"""
Tests for Step 5: Gold-Case-Driven Authority Semantics Enrichment.

Covers:
  - Enrichment runs all 12 gold cases
  - Collects driver entry_ids from top_decision_drivers
  - Enriches required fields
  - Completion criterion: driver entries have full semantics
"""

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evidence_registry import EvidenceRegistry
from services.authority_enrichment import (
    run_gold_case_enrichment,
    EnrichmentReport,
    _enrich_entry,
    _measure_completeness,
)


class TestEnrichmentPipeline:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.registry = EvidenceRegistry()

    def test_enrichment_processes_all_12_cases(self):
        """Step 5 runs all 12 gold cases."""
        report = run_gold_case_enrichment(registry=self.registry)
        assert report.n_gold_cases_processed == 12

    def test_enrichment_finds_driver_entries(self):
        """Step 5 identifies driver entries from gold cases."""
        report = run_gold_case_enrichment(registry=self.registry)
        assert report.n_unique_driver_entries > 0

    def test_enrichment_improves_completeness(self):
        """After enrichment, completeness should not decrease."""
        report = run_gold_case_enrichment(registry=self.registry)
        assert report.completeness_after >= report.completeness_before

    def test_enrichment_report_structure(self):
        """EnrichmentReport has expected fields."""
        report = run_gold_case_enrichment(registry=self.registry)
        assert isinstance(report, EnrichmentReport)
        assert isinstance(report.enriched_entry_ids, list)
        assert isinstance(report.missing_entry_ids, list)

    def test_enriched_entries_have_authority_tier(self):
        """Enriched entries have authority_quality_tier set."""
        report = run_gold_case_enrichment(registry=self.registry)
        for eid in report.enriched_entry_ids:
            entry = self.registry.get(eid)
            if entry:
                assert entry.authority_quality_tier is not None
                assert entry.authority_quality_tier != ""

    def test_enriched_entries_have_temporal_status(self):
        """Enriched entries have temporal_status set."""
        report = run_gold_case_enrichment(registry=self.registry)
        for eid in report.enriched_entry_ids:
            entry = self.registry.get(eid)
            if entry:
                assert entry.temporal_status is not None
                assert entry.temporal_status != ""


class TestEnrichEntryLogic:

    def test_enrich_sets_authority_tier_for_normative(self):
        """Normative entries get primary authority tier."""
        from evidence_registry.registry import RegistryEntry
        entry = RegistryEntry(
            id="TEST-001",
            entry_type="guideline_clause",
            decision_type="Normative",
            authority_quality_tier="contextual",
        )
        was_enriched = _enrich_entry(entry, [])
        assert was_enriched is True
        assert entry.authority_quality_tier == "primary"

    def test_enrich_sets_temporal_from_year(self):
        """Temporal status inferred from year."""
        from evidence_registry.registry import RegistryEntry
        entry = RegistryEntry(
            id="TEST-002",
            entry_type="precedent",
            decision_type="Precedent",
            year=2002,
            temporal_status="current",
        )
        was_enriched = _enrich_entry(entry, [])
        assert was_enriched is True
        assert entry.temporal_status == "historical_only"

    def test_enrich_idempotent(self):
        """Running enrichment twice on same entry doesn't change it further."""
        from evidence_registry.registry import RegistryEntry
        entry = RegistryEntry(
            id="TEST-003",
            entry_type="guideline_clause",
            decision_type="Normative",
            authority_quality_tier="contextual",
            applicable_categories=["potency"],
        )
        _enrich_entry(entry, [])
        tier_after_first = entry.authority_quality_tier
        _enrich_entry(entry, [])
        assert entry.authority_quality_tier == tier_after_first
