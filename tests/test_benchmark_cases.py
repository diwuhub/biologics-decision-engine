"""
Tests for S-5 Public Case-Pack Benchmark Library.

Validates:
  1. All benchmark case files load as valid JSON with required fields
  2. Comparability cases produce expected verdicts through the pipeline
  3. Benchmark runner produces a valid summary

Run:
    python3 -m pytest tests/test_benchmark_cases.py -v --tb=short
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

CASES_DIR = os.path.join(PROJECT_ROOT, "benchmarks", "cases")


# =========================================================================
# Helpers
# =========================================================================

def _load_case(case_id: str) -> dict:
    fpath = os.path.join(CASES_DIR, f"{case_id}.json")
    with open(fpath) as f:
        return json.load(f)


def _all_case_files():
    """Return list of all .json files in benchmarks/cases/."""
    if not os.path.isdir(CASES_DIR):
        return []
    return sorted(
        f for f in os.listdir(CASES_DIR) if f.endswith(".json")
    )


# =========================================================================
# Test 1: All cases load as valid JSON with required fields
# =========================================================================

class TestAllCasesLoadValidJSON:

    def test_cases_directory_exists(self):
        assert os.path.isdir(CASES_DIR), f"Missing cases directory: {CASES_DIR}"

    def test_at_least_10_cases(self):
        cases = _all_case_files()
        assert len(cases) >= 10, f"Expected >= 10 cases, found {len(cases)}"

    @pytest.mark.parametrize("fname", _all_case_files())
    def test_case_loads_valid_json(self, fname):
        fpath = os.path.join(CASES_DIR, fname)
        with open(fpath) as f:
            data = json.load(f)
        assert isinstance(data, dict), f"{fname} root is not a dict"

    @pytest.mark.parametrize("fname", _all_case_files())
    def test_case_has_required_fields(self, fname):
        fpath = os.path.join(CASES_DIR, fname)
        with open(fpath) as f:
            data = json.load(f)

        # All cases must have these fields
        assert "case_id" in data, f"{fname} missing case_id"
        assert "title" in data, f"{fname} missing title"
        assert "category" in data, f"{fname} missing category"
        assert "source" in data, f"{fname} missing source"

        # Category-specific required fields
        cat = data["category"]
        if cat == "comparability":
            assert "expected_verdict" in data, f"{fname} missing expected_verdict"
            assert "attributes" in data, f"{fname} missing attributes"
            assert "product_name" in data, f"{fname} missing product_name"
            assert "change_description" in data, f"{fname} missing change_description"
            assert len(data["attributes"]) > 0, f"{fname} has empty attributes"
        elif cat == "review_readiness":
            assert "expected_readiness_tier" in data, f"{fname} missing expected_readiness_tier"
            assert "nam_type" in data, f"{fname} missing nam_type"
            assert "context_of_use" in data, f"{fname} missing context_of_use"
        elif cat == "regulatory_classification":
            assert "expected_action" in data, f"{fname} missing expected_action"
            assert "claims" in data, f"{fname} missing claims"
            assert len(data["claims"]) > 0, f"{fname} has empty claims"
        elif cat == "gap_memo":
            assert "expected_readiness" in data, f"{fname} missing expected_readiness"
            assert "sections" in data, f"{fname} missing sections"
            assert len(data["sections"]) > 0, f"{fname} has empty sections"

    @pytest.mark.parametrize("fname", [
        f for f in _all_case_files()
        if json.load(open(os.path.join(CASES_DIR, f))).get("category") == "comparability"
    ])
    def test_comparability_attributes_schema(self, fname):
        """Each attribute in a comparability case has the required fields."""
        fpath = os.path.join(CASES_DIR, fname)
        with open(fpath) as f:
            data = json.load(f)

        required_attr_fields = [
            "name", "category", "pre_value", "post_value", "unit"
        ]
        for i, attr in enumerate(data["attributes"]):
            for field_name in required_attr_fields:
                assert field_name in attr, (
                    f"{fname} attribute[{i}] ({attr.get('name', '?')}) "
                    f"missing field: {field_name}"
                )


# =========================================================================
# Test 2: Comparability cases produce expected verdicts
# =========================================================================

_COMP_CASES = [
    "COMP-001", "COMP-002", "COMP-003", "COMP-004", "COMP-005",
    "COMP-006", "COMP-007", "COMP-008", "COMP-009",
]


class TestComparabilityCasesProduceExpectedVerdicts:

    @pytest.mark.parametrize("case_id", _COMP_CASES)
    def test_verdict_matches_expected(self, case_id):
        from pipelines.comparability import run_comparability_assessment

        case = _load_case(case_id)
        report = run_comparability_assessment(
            pre_change_data=case,
            product_name=case["product_name"],
            change_description=case["change_description"],
        )
        expected = case["expected_verdict"]
        actual = report.overall_verdict
        assert actual == expected, (
            f"{case_id}: expected verdict '{expected}', got '{actual}' "
            f"(confidence={report.evidence_strength_index:.3f})"
        )

    @pytest.mark.parametrize("case_id", _COMP_CASES)
    def test_action_recommendations_match(self, case_id):
        from pipelines.comparability import run_comparability_assessment

        case = _load_case(case_id)
        expected_actions = case.get("expected_actions", {})
        if not expected_actions:
            pytest.skip(f"{case_id} has no expected_actions")

        report = run_comparability_assessment(
            pre_change_data=case,
            product_name=case["product_name"],
            change_description=case["change_description"],
        )

        actual_actions = {}
        for ar in report.attribute_results:
            if ar.action:
                actual_actions[ar.name] = ar.action.get("action_level", "UNKNOWN")

        mismatches = []
        for attr_name, expected_action in expected_actions.items():
            actual = actual_actions.get(attr_name, "NOT_FOUND")
            if actual != expected_action:
                mismatches.append(
                    f"{attr_name}: expected {expected_action}, got {actual}"
                )

        assert not mismatches, (
            f"{case_id} action mismatches:\n  " + "\n  ".join(mismatches)
        )

    @pytest.mark.parametrize("case_id", _COMP_CASES)
    def test_report_structure_valid(self, case_id):
        from pipelines.comparability import run_comparability_assessment
        from pipelines.schemas import ComparabilityReport

        case = _load_case(case_id)
        report = run_comparability_assessment(
            pre_change_data=case,
            product_name=case["product_name"],
            change_description=case["change_description"],
        )

        assert isinstance(report, ComparabilityReport)
        assert report.n_attributes == len(case["attributes"])
        assert 0 <= report.evidence_strength_index <= 1
        assert report.overall_verdict in (
            "Comparable", "Comparable With Caveats", "Not Comparable", "Insufficient Evidence"
        )
        assert report.timestamp


# =========================================================================
# Test 3: Benchmark runner produces a valid summary
# =========================================================================

@pytest.mark.timeout(60)
class TestBenchmarkRunnerProducesSummary:

    def test_run_all_benchmarks_returns_summary(self):
        from benchmarks.run_benchmarks import run_all_benchmarks, BenchmarkSummary

        summary = run_all_benchmarks()
        assert isinstance(summary, BenchmarkSummary)
        assert summary.n_cases >= 10
        assert summary.n_cases == len(summary.results)

    def test_summary_counts_add_up(self):
        from benchmarks.run_benchmarks import run_all_benchmarks

        summary = run_all_benchmarks()
        total = summary.n_pass + summary.n_partial + summary.n_fail + summary.n_error
        assert total == summary.n_cases

    def test_verdict_accuracy_is_computed(self):
        from benchmarks.run_benchmarks import run_all_benchmarks

        summary = run_all_benchmarks()
        assert 0.0 <= summary.verdict_accuracy <= 1.0

    def test_no_errors_in_run(self):
        from benchmarks.run_benchmarks import run_all_benchmarks

        summary = run_all_benchmarks()
        errors = [r for r in summary.results if r.status == "error"]
        assert len(errors) == 0, (
            f"Benchmark errors: "
            + "; ".join(f"{r.case_id}: {r.error}" for r in errors)
        )

    def test_all_cases_have_verdict(self):
        from benchmarks.run_benchmarks import run_all_benchmarks

        summary = run_all_benchmarks()
        for r in summary.results:
            assert r.actual_verdict not in (None, "", "N/A", "ERROR"), (
                f"{r.case_id} has no actual verdict"
            )

    def test_summary_serializes_to_dict(self):
        from benchmarks.run_benchmarks import run_all_benchmarks

        summary = run_all_benchmarks()
        d = summary.to_dict()
        assert isinstance(d, dict)
        assert "n_cases" in d
        assert "results" in d
        assert isinstance(d["results"], list)
        # Verify JSON-serializable
        json.dumps(d)

    def test_single_case_runner(self):
        from benchmarks.run_benchmarks import load_all_cases, run_single_benchmark

        cases = load_all_cases()
        assert len(cases) > 0
        result = run_single_benchmark(cases[0])
        assert result.case_id == cases[0]["case_id"]
        assert result.status in ("pass", "partial", "fail", "error")


# =========================================================================
# Test 4: NAM Readiness cases produce expected tiers
# =========================================================================

_READY_CASES = [
    "READY-001", "READY-002", "READY-003",
    "READY-004", "READY-005", "READY-006", "READY-007",
]


class TestNAMReadinessCasesProduceExpectedTiers:

    @pytest.mark.parametrize("case_id", _READY_CASES)
    def test_readiness_tier_matches_expected(self, case_id):
        from schemas.label_schema import NAMReadinessRecord
        from modules.nam_readiness.readiness_scorer import score_readiness

        case = _load_case(case_id)
        record = NAMReadinessRecord(
            nam_type=case["nam_type"],
            context_of_use=case["context_of_use"],
            species_replaced=case.get("species_replaced"),
            validation_evidence=case.get("validation_evidence", []),
            regulatory_precedent=case.get("regulatory_precedent", []),
            qualification_pathway=case.get("qualification_pathway"),
        )
        result = score_readiness(record)
        score = result.readiness_score

        if score >= 0.75:
            actual_tier = "high"
        elif score >= 0.40:
            actual_tier = "medium"
        else:
            actual_tier = "low"

        expected_tier = case["expected_readiness_tier"]
        assert actual_tier == expected_tier, (
            f"{case_id}: expected tier '{expected_tier}', got '{actual_tier}' "
            f"(score={score:.4f}, gaps={result.readiness_gaps})"
        )

    @pytest.mark.parametrize("case_id", _READY_CASES)
    def test_readiness_score_bounds(self, case_id):
        from schemas.label_schema import NAMReadinessRecord
        from modules.nam_readiness.readiness_scorer import score_readiness

        case = _load_case(case_id)
        record = NAMReadinessRecord(
            nam_type=case["nam_type"],
            context_of_use=case["context_of_use"],
            species_replaced=case.get("species_replaced"),
            validation_evidence=case.get("validation_evidence", []),
            regulatory_precedent=case.get("regulatory_precedent", []),
            qualification_pathway=case.get("qualification_pathway"),
        )
        result = score_readiness(record)
        score = result.readiness_score

        if "expected_readiness_score_min" in case:
            assert score >= case["expected_readiness_score_min"], (
                f"{case_id}: score {score:.4f} < min {case['expected_readiness_score_min']}"
            )
        if "expected_readiness_score_max" in case:
            assert score <= case["expected_readiness_score_max"], (
                f"{case_id}: score {score:.4f} > max {case['expected_readiness_score_max']}"
            )

    @pytest.mark.parametrize("case_id", _READY_CASES)
    def test_readiness_gap_count_bounds(self, case_id):
        from schemas.label_schema import NAMReadinessRecord
        from modules.nam_readiness.readiness_scorer import score_readiness

        case = _load_case(case_id)
        record = NAMReadinessRecord(
            nam_type=case["nam_type"],
            context_of_use=case["context_of_use"],
            species_replaced=case.get("species_replaced"),
            validation_evidence=case.get("validation_evidence", []),
            regulatory_precedent=case.get("regulatory_precedent", []),
            qualification_pathway=case.get("qualification_pathway"),
        )
        result = score_readiness(record)
        n_gaps = len(result.readiness_gaps)

        if "expected_gap_count_max" in case:
            assert n_gaps <= case["expected_gap_count_max"], (
                f"{case_id}: {n_gaps} gaps > max {case['expected_gap_count_max']}: {result.readiness_gaps}"
            )
        if "expected_gap_count_min" in case:
            assert n_gaps >= case["expected_gap_count_min"], (
                f"{case_id}: {n_gaps} gaps < min {case['expected_gap_count_min']}"
            )

    def test_expanded_taxonomy_contexts_exist(self):
        from modules.nam_readiness.context_taxonomy import CONTEXT_OF_USE_TAXONOMY

        required_contexts = [
            "hepatotoxicity_screening",
            "nephrotoxicity_screening",
            "cardiotoxicity_screening",
            "immunogenicity_prediction",
            "viral_safety",
        ]
        for ctx in required_contexts:
            assert ctx in CONTEXT_OF_USE_TAXONOMY, (
                f"Missing context '{ctx}' in CONTEXT_OF_USE_TAXONOMY"
            )
