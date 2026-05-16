"""
Comprehensive tests for all biologics-decision-engine modules.

Run: python -m pytest tests/test_all_modules.py -v
  or: python tests/test_all_modules.py
"""

import json
import os
import sys
import unittest

# Path setup
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


# =========================================================================
# Comparability Evidence Graph
# =========================================================================

class TestComparabilityGraph(unittest.TestCase):

    def test_load_benchmark_case(self):
        case_path = os.path.join(REPO_ROOT, "benchmarks", "thermal_stability_case.json")
        with open(case_path) as f:
            case = json.load(f)
        self.assertIn("comparison_id", case)
        self.assertIn("lots", case)
        self.assertIn("attributes", case)
        self.assertEqual(len(case["lots"]), 2)
        self.assertEqual(len(case["attributes"]), 7)

    def test_generate_verdict(self):
        from modules.comparability_graph.engine import generate_verdict
        case_path = os.path.join(REPO_ROOT, "benchmarks", "thermal_stability_case.json")
        with open(case_path) as f:
            case = json.load(f)
        verdict = generate_verdict(case)
        self.assertIsNotNone(verdict)
        self.assertIsInstance(verdict.comparable, bool)
        self.assertGreaterEqual(verdict.overall_score, 0)
        self.assertLessEqual(verdict.overall_score, 1)
        self.assertGreaterEqual(verdict.confidence, 0)
        self.assertLessEqual(verdict.confidence, 1)
        self.assertEqual(verdict.n_attributes, 7)

    def test_empty_case(self):
        from modules.comparability_graph.engine import generate_verdict
        case = {"comparison_id": "empty", "lots": [], "attributes": [], "edges": []}
        verdict = generate_verdict(case)
        self.assertEqual(verdict.n_attributes, 0)

    def test_report_generation(self):
        from modules.comparability_graph.engine import generate_verdict, generate_report
        case_path = os.path.join(REPO_ROOT, "benchmarks", "thermal_stability_case.json")
        with open(case_path) as f:
            case = json.load(f)
        verdict = generate_verdict(case)
        report = generate_report(verdict)
        self.assertIn("Comparability Evidence Graph", report)
        self.assertIn("Attribute Scores", report)

    def test_score_range(self):
        from modules.comparability_graph.engine import generate_verdict
        case_path = os.path.join(REPO_ROOT, "benchmarks", "thermal_stability_case.json")
        with open(case_path) as f:
            case = json.load(f)
        verdict = generate_verdict(case)
        for a in verdict.attribute_scores:
            self.assertGreaterEqual(a.score, 0)
            self.assertLessEqual(a.score, 1)
            self.assertIn(a.concern, ["none", "minor", "major", "critical"])


# =========================================================================
# PTM Root-Cause Attribution
# =========================================================================

class TestCQASelector(unittest.TestCase):

    def test_select_demo(self):
        from modules.cqa_selector.engine import select_cqas, DEFAULT_MAB_CANDIDATES
        results = select_cqas(DEFAULT_MAB_CANDIDATES)
        self.assertEqual(len(results), 20)
        # Should have CQAs
        cqas = [r for r in results if r.designation == "CQA"]
        self.assertGreater(len(cqas), 0)

    def test_rpn_range(self):
        from modules.cqa_selector.engine import compute_rpn
        # Min: impact=1, detect=5, control=5 → 1*1*1 = 1
        self.assertEqual(compute_rpn(1, 5, 5), 1)
        # Max: impact=5, detect=1, control=1 → 5*5*5 = 125
        self.assertEqual(compute_rpn(5, 1, 1), 125)

    def test_empty_candidates(self):
        from modules.cqa_selector.engine import select_cqas
        results = select_cqas([])
        self.assertEqual(len(results), 0)

    def test_classification_thresholds(self):
        from modules.cqa_selector.engine import classify_cqa
        self.assertEqual(classify_cqa(50, 5), "CQA")
        self.assertEqual(classify_cqa(25, 3), "KQA")
        self.assertEqual(classify_cqa(15, 2), "QA")
        self.assertEqual(classify_cqa(5, 1), "Monitor")

    def test_input_clamping(self):
        from modules.cqa_selector.engine import select_cqas
        # Out-of-range values should be clamped, not crash
        results = select_cqas([{"name": "test", "category": "purity", "assay": "SEC",
                                "impact": 99, "detectability": -5, "controllability": 0}])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].impact, 5)  # clamped to 5
        self.assertEqual(results[0].detectability, 1)  # clamped to 1


# =========================================================================
# Biosimilar Residual Uncertainty
# =========================================================================

class TestBiosimilarUncertainty(unittest.TestCase):

    def test_score_attribute(self):
        from modules.biosimilar_uncertainty.engine import score_attribute_uncertainty
        result = score_attribute_uncertainty(
            "SEC Monomer", "purity", n_methods=3, n_lots_biosimilar=10,
            n_lots_originator=10, n_replicates=3, cv_pct=2.0,
            has_functional_correlation=False, prior_approvals_with_similar_diff=5,
        )
        self.assertGreaterEqual(result.residual_uncertainty, 0)
        self.assertLessEqual(result.residual_uncertainty, 1)
        self.assertIn(result.confidence_level, ["low", "medium", "high"])

    def test_high_cv_penalty(self):
        from modules.biosimilar_uncertainty.engine import score_attribute_uncertainty
        low_cv = score_attribute_uncertainty("A", "purity", cv_pct=2.0)
        high_cv = score_attribute_uncertainty("A", "purity", cv_pct=25.0)
        self.assertLess(low_cv.residual_uncertainty, high_cv.residual_uncertainty)

    def test_negative_inputs(self):
        from modules.biosimilar_uncertainty.engine import score_attribute_uncertainty
        # Should handle gracefully (clamp to 0)
        result = score_attribute_uncertainty("A", "purity", n_methods=-1, cv_pct=-5)
        self.assertGreaterEqual(result.residual_uncertainty, 0)


# =========================================================================
# Lifecycle Evidence Memory
# =========================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
