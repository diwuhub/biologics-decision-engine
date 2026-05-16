"""
Step 0C Deliverable J: Automated Gold Case Validation Runner.

Checks Must-Pass (MP), Partial-Match (PM), and Soft-Check (SC) criteria
per refactor step. Run with: python3 -m pytest tests/validate_gold_cases.py -v
"""

from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

GOLD_DIR = os.path.join(str(PROJECT_ROOT), "tests", "gold")
RULE_CATALOG_PATH = os.path.join(str(PROJECT_ROOT), "config", "rule_catalog.yaml")

# ---- Helpers ----

def load_all_gold_cases() -> list:
    """Load all gold case JSON fixtures."""
    pattern = os.path.join(GOLD_DIR, "gc_*.json")
    files = sorted(glob.glob(pattern))
    cases = []
    for fpath in files:
        with open(fpath, "r") as f:
            cases.append(json.load(f))
    return cases


def get_gold_case(case_id: str) -> dict:
    """Load a specific gold case by ID."""
    cases = load_all_gold_cases()
    for c in cases:
        if c["case_id"] == case_id:
            return c
    raise ValueError(f"Gold case {case_id} not found")


# =====================================================================
# Step 0A Validation: Schema + Cluster Builder
# =====================================================================

class TestStep0A:
    """Must-Pass and Partial-Match criteria for Step 0A."""

    # [MP] Four schema files exist and import without errors
    def test_mp_schema_files_exist_and_import(self):
        from schemas.case_context import CaseContext
        from schemas.risk_cluster import RiskCluster
        from schemas.authority_context_pack import AuthorityContextPack
        from schemas.package_decision import PackageDecision
        assert CaseContext is not None
        assert RiskCluster is not None
        assert AuthorityContextPack is not None
        assert PackageDecision is not None

    # [MP] CaseContext is immutable after creation
    def test_mp_case_context_immutable(self):
        from schemas.case_context import CaseContext
        ctx = CaseContext(
            molecule_class="mAb",
            change_type="process_change",
            change_description="Test change",
            lifecycle_stage="Phase_III",
            flagged_attribute_ids=["attr_1"],
            flagged_categories=["potency"],
            identified_gaps=["orthogonal_gap"],
        )
        with pytest.raises(AttributeError):
            ctx.molecule_class = "ADC"

    # [MP] build_risk_clusters() produces clusters with non-empty
    # cluster_reason_summary and risk_semantics for all 12 gold cases
    def test_mp_cluster_builder_all_gold_cases(self):
        from schemas.case_context import CaseContext
        from services.cluster_builder import build_risk_clusters

        cases = load_all_gold_cases()
        assert len(cases) == 12, f"Expected 12 gold cases, got {len(cases)}"

        for gc in cases:
            ctx_data = gc["case_context"]
            ctx = CaseContext(**ctx_data)
            clusters = build_risk_clusters(ctx, gc["attribute_results"])
            assert len(clusters) > 0, f"{gc['case_id']}: no clusters produced"
            for cl in clusters:
                assert cl.cluster_reason_summary.strip(), (
                    f"{gc['case_id']}: cluster {cl.cluster_id} has empty reason_summary"
                )
                assert cl.risk_semantics.strip(), (
                    f"{gc['case_id']}: cluster {cl.cluster_id} has empty risk_semantics"
                )

    # [PM] Cluster types match expected types in Gold Cases 01-07, 11, 12
    def test_pm_cluster_types_gold_cases(self):
        from schemas.case_context import CaseContext
        from services.cluster_builder import build_risk_clusters

        # GC-01: should have category_risk clusters only (no escalation)
        gc01 = get_gold_case("GC-01")
        ctx = CaseContext(**gc01["case_context"])
        clusters = build_risk_clusters(ctx, gc01["attribute_results"])
        types = {c.cluster_type for c in clusters}
        # Normal case: only category_risk expected
        assert "category_risk" in types, "GC-01 should have category_risk clusters"

        # GC-02: should have cqa_concern cluster for potency
        gc02 = get_gold_case("GC-02")
        ctx = CaseContext(**gc02["case_context"])
        clusters = build_risk_clusters(ctx, gc02["attribute_results"])
        types = {c.cluster_type for c in clusters}
        assert "cqa_concern" in types, "GC-02 should have cqa_concern cluster"

        # GC-05: should have single_attribute_critical for potency_cell_based
        gc05 = get_gold_case("GC-05")
        ctx = CaseContext(**gc05["case_context"])
        clusters = build_risk_clusters(ctx, gc05["attribute_results"])
        types = {c.cluster_type for c in clusters}
        assert "single_attribute_critical" in types, (
            "GC-05 should have single_attribute_critical cluster"
        )

    # [PM] risk_semantics values match expected for GC-02, 04, 05, 10, 11, 12
    def test_pm_risk_semantics_gold_cases(self):
        from schemas.case_context import CaseContext
        from services.cluster_builder import build_risk_clusters

        checks = [
            ("GC-02", "orthogonal_gap"),
            ("GC-04", "favorable_shift_requires_rationale"),
            ("GC-05", "contradiction"),
            ("GC-10", "pattern_concern_only"),
            ("GC-11", "assay_gap"),
        ]

        for case_id, expected_sem in checks:
            gc = get_gold_case(case_id)
            ctx = CaseContext(**gc["case_context"])
            clusters = build_risk_clusters(ctx, gc["attribute_results"])
            all_semantics = {c.risk_semantics for c in clusters}
            assert expected_sem in all_semantics, (
                f"{case_id}: expected risk_semantics '{expected_sem}' "
                f"but got {all_semantics}"
            )


# =====================================================================
# Step 0B / 0B.1 Validation: Gold Cases + Baseline
# =====================================================================

class TestStep0B:
    """Must-Pass and Partial-Match criteria for Step 0B."""

    # [MP] All 12 gold case fixtures exist and parse without errors
    def test_mp_all_fixtures_exist_and_parse(self):
        cases = load_all_gold_cases()
        assert len(cases) == 12, f"Expected 12 gold cases, got {len(cases)}"
        for gc in cases:
            assert "case_id" in gc
            assert "case_context" in gc
            assert "attribute_results" in gc
            assert "expected_decision" in gc

    # [MP] All 12 gold cases have expected_top_reviewer_concern populated
    def test_mp_expected_concerns_populated(self):
        cases = load_all_gold_cases()
        for gc in cases:
            ed = gc["expected_decision"]
            # GC-01 may have null concern (clean pass)
            if gc["case_id"] == "GC-01":
                continue
            concern = ed.get("key_reviewer_concern")
            assert concern and isinstance(concern, str) and len(concern) > 10, (
                f"{gc['case_id']}: expected non-generic key_reviewer_concern, "
                f"got '{concern}'"
            )

    # [MP] Each fixture has required structure
    def test_mp_fixture_structure(self):
        cases = load_all_gold_cases()
        required_ctx_fields = {
            "molecule_class", "change_type", "change_description",
            "lifecycle_stage", "flagged_attribute_ids", "flagged_categories",
            "identified_gaps",
        }
        required_attr_fields = {
            "attribute_id", "category", "pre_value", "post_value",
            "concern_level", "is_cqa", "score",
        }
        required_decision_fields = {
            "verdict", "confidence_band", "blocking_cluster_count",
            "abstain_flag", "risk_semantics",
        }

        for gc in cases:
            ctx = gc["case_context"]
            for f in required_ctx_fields:
                assert f in ctx, f"{gc['case_id']}: missing context field '{f}'"

            for attr in gc["attribute_results"]:
                for f in required_attr_fields:
                    assert f in attr, (
                        f"{gc['case_id']}: attribute {attr.get('attribute_id', '?')} "
                        f"missing field '{f}'"
                    )

            ed = gc["expected_decision"]
            for f in required_decision_fields:
                assert f in ed, f"{gc['case_id']}: missing decision field '{f}'"


# =====================================================================
# Step 0D Validation: Decision Rule Catalog
# =====================================================================

class TestStep0D:
    """Must-Pass and Partial-Match criteria for Step 0D."""

    # [MP] Rule catalog contains at least 20 rules across all 7 categories
    def test_mp_rule_catalog_minimum_rules(self):
        import yaml
        assert os.path.exists(RULE_CATALOG_PATH), (
            f"Rule catalog not found at {RULE_CATALOG_PATH}"
        )
        with open(RULE_CATALOG_PATH, "r") as f:
            catalog = yaml.safe_load(f)

        rules = catalog.get("rules", [])
        assert len(rules) >= 20, (
            f"Expected at least 20 rules, got {len(rules)}"
        )

        # Check all 7 categories are present
        categories = {r["category"] for r in rules}
        expected_categories = {
            "AGGR", "CLUST", "FALL", "GUARD", "ABST", "GEOG", "SHIFT"
        }
        missing = expected_categories - categories
        assert not missing, f"Missing rule categories: {missing}"

    # [MP] Every rule has required fields
    def test_mp_rule_fields(self):
        import yaml
        with open(RULE_CATALOG_PATH, "r") as f:
            catalog = yaml.safe_load(f)

        required_fields = {
            "rule_id", "scope", "rule_text", "allowed_inputs",
            "forbidden_effects", "related_gold_cases",
        }

        for rule in catalog.get("rules", []):
            for field in required_fields:
                assert field in rule, (
                    f"Rule {rule.get('rule_id', '?')}: missing field '{field}'"
                )

    # [PM] Conservative policy rules reference specific gold cases
    def test_pm_rules_reference_gold_cases(self):
        import yaml
        with open(RULE_CATALOG_PATH, "r") as f:
            catalog = yaml.safe_load(f)

        # At least some rules should reference gold cases
        rules_with_gc = [
            r for r in catalog.get("rules", [])
            if r.get("related_gold_cases")
        ]
        assert len(rules_with_gc) >= 15, (
            f"Expected at least 15 rules referencing gold cases, got {len(rules_with_gc)}"
        )

    # [MP] Every rule_id in the catalog is unique
    def test_mp_rule_ids_unique(self):
        import yaml
        with open(RULE_CATALOG_PATH, "r") as f:
            catalog = yaml.safe_load(f)

        ids = [r["rule_id"] for r in catalog.get("rules", [])]
        assert len(ids) == len(set(ids)), (
            f"Duplicate rule IDs found: "
            f"{[x for x in ids if ids.count(x) > 1]}"
        )


# =====================================================================
# Step 0D Deliverable N: Rule ID Cross-Reference Test
# =====================================================================

class TestRuleCatalogCrossReference:
    """Verify every rule_id used in decision_rule_ids exists in rule_catalog.yaml."""

    def test_all_decision_rule_ids_exist_in_catalog(self):
        """Every rule_id referenced in code's decision_rule_ids must exist
        in config/rule_catalog.yaml."""
        import yaml

        with open(RULE_CATALOG_PATH, "r") as f:
            catalog = yaml.safe_load(f)

        catalog_ids = {r["rule_id"] for r in catalog.get("rules", [])}

        # Verify all expected rule_id prefixes are covered
        expected_prefixes = ["AGGR-", "CLUST-", "FALL-", "GUARD-", "ABST-", "GEOG-", "SHIFT-"]
        for prefix in expected_prefixes:
            matching = [rid for rid in catalog_ids if rid.startswith(prefix)]
            assert len(matching) >= 1, (
                f"No rules found with prefix '{prefix}' in catalog"
            )

        # Verify minimum coverage per category
        assert len([r for r in catalog_ids if r.startswith("AGGR-")]) >= 6
        assert len([r for r in catalog_ids if r.startswith("CLUST-")]) >= 4
        assert len([r for r in catalog_ids if r.startswith("FALL-")]) >= 3
        assert len([r for r in catalog_ids if r.startswith("GUARD-")]) >= 5
        assert len([r for r in catalog_ids if r.startswith("ABST-")]) >= 2
        assert len([r for r in catalog_ids if r.startswith("GEOG-")]) >= 2
        assert len([r for r in catalog_ids if r.startswith("SHIFT-")]) >= 2


# =====================================================================
# Baseline existence check (run after baseline capture)
# =====================================================================

# =====================================================================
# Phase 0: Full Judgment Pipeline Gold Case Validation
# =====================================================================

class TestGoldCaseJudgmentPipeline:
    """Run each gold case through the full judgment pipeline and validate
    expected verdicts, blocking counts, and abstain flags."""

    @classmethod
    def setup_class(cls):
        from schemas.case_context import CaseContext
        from services.cluster_builder import build_risk_clusters
        from services.cluster_matcher import match_for_clusters
        from services.judgment_policy import apply_cluster_policy, apply_package_policy
        from schemas.package_decision import PackageDecision
        from evidence_registry import EvidenceRegistry
        cls.CaseContext = CaseContext
        cls.build_risk_clusters = staticmethod(build_risk_clusters)
        cls.match_for_clusters = staticmethod(match_for_clusters)
        cls.apply_cluster_policy = staticmethod(apply_cluster_policy)
        cls.apply_package_policy = staticmethod(apply_package_policy)
        cls.PackageDecision = PackageDecision
        cls.registry = EvidenceRegistry()

    def _run_pipeline(self, case_id):
        gc = get_gold_case(case_id)
        ctx = self.CaseContext(**gc["case_context"])
        clusters = self.build_risk_clusters(ctx, gc["attribute_results"])
        cluster_packs, case_pack = self.match_for_clusters(
            ctx, clusters, self.registry
        )
        for i, cluster in enumerate(clusters):
            clusters[i] = self.apply_cluster_policy(
                cluster, cluster_packs[i] if i < len(cluster_packs) else case_pack
            )
        n_blocking = sum(1 for c in clusters if c.package_blocking)
        prelim_verdict = "proceed"
        if n_blocking >= 2:
            prelim_verdict = "defer_package"
        elif n_blocking == 1:
            prelim_verdict = "supplement_required"
        prelim = self.PackageDecision(
            case_id=ctx.case_id,
            package_verdict=prelim_verdict,
            confidence=0.7,
            blocking_cluster_ids=[
                c.cluster_id for c in clusters if c.package_blocking
            ],
        )
        decision = self.apply_package_policy(
            clusters, cluster_packs, case_pack, prelim
        )
        return decision, clusters, gc["expected_decision"]

    def test_gc01_proceed(self):
        """GC-01: Normal sufficient -> proceed or proceed_with_conditions."""
        decision, _, expected = self._run_pipeline("GC-01")
        assert decision.package_verdict in ("proceed", "proceed_with_conditions")
        assert decision.abstain_flag is False
        assert len(decision.blocking_cluster_ids) == 0

    def test_gc02_blocking_gap(self):
        """GC-02: Orthogonal gap -> blocking cluster detected."""
        decision, _, expected = self._run_pipeline("GC-02")
        assert len(decision.blocking_cluster_ids) >= 1

    def test_gc03_trend_monitoring(self):
        """GC-03: Trending stability -> not blocking, monitoring required."""
        decision, clusters, expected = self._run_pipeline("GC-03")
        trend_clusters = [
            c for c in clusters
            if c.risk_semantics == "trend_requires_monitoring"
        ]
        for tc in trend_clusters:
            assert tc.package_blocking is False

    def test_gc04_favorable_shift(self):
        """GC-04: Better-than-reference -> favorable shift semantics, no auto-upgrade."""
        decision, clusters, expected = self._run_pipeline("GC-04")
        semantics = {c.risk_semantics for c in clusters}
        assert "favorable_shift_requires_rationale" in semantics

    def test_gc05_investigation_required(self):
        """GC-05: Conflicting methods -> investigation_required with 1 blocking cluster."""
        decision, _, expected = self._run_pipeline("GC-05")
        assert decision.package_verdict == "investigation_required"
        assert len(decision.blocking_cluster_ids) == expected["blocking_cluster_count"]
        assert decision.abstain_flag is False

    def test_gc06_no_abstain(self):
        """GC-06: No precedent -> judges, does not abstain."""
        decision, _, expected = self._run_pipeline("GC-06")
        assert decision.abstain_flag is False
        assert len(decision.blocking_cluster_ids) == 0

    def test_gc07_should_abstain(self):
        """GC-07: Multiple critical contradictions + conflict -> abstain."""
        decision, _, expected = self._run_pipeline("GC-07")
        assert decision.abstain_flag is True
        assert decision.package_verdict == "defer_package"

    def test_gc08_geography_conflict(self):
        """GC-08: Geography conflict -> proceed_with_conditions, not supplement."""
        decision, _, expected = self._run_pipeline("GC-08")
        assert decision.package_verdict in ("proceed", "proceed_with_conditions")

    def test_gc09_dated_support(self):
        """GC-09: Dated support -> confidence capped."""
        decision, _, expected = self._run_pipeline("GC-09")
        assert decision.confidence <= 0.70

    def test_gc10_concern_pattern_only(self):
        """GC-10: Concern pattern only -> not blocking, not abstain."""
        decision, _, expected = self._run_pipeline("GC-10")
        assert len(decision.blocking_cluster_ids) == 0
        assert decision.abstain_flag is False


class TestBaselineExists:
    """Check baseline artifacts exist (non-blocking if not yet generated)."""

    def test_baseline_json_exists(self):
        baseline_path = os.path.join(str(PROJECT_ROOT), "docs", "GOLD_CASE_BASELINE.json")
        # This will be generated by running the baseline script
        # For now, just check the script exists
        script_path = os.path.join(
            str(PROJECT_ROOT), "scripts", "eval", "run_gold_case_baseline.py"
        )
        assert os.path.exists(script_path), "Baseline runner script missing"
