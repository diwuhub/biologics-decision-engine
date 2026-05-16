"""Tests for adjudicator.py — 4-tier label adjudication policy."""
import os, sys, tempfile, shutil, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.label_schema import LabelRecord
from schemas.label_store import LabelStore
from schemas.adjudicator import (
    auto_adjudicate_tier1,
    auto_adjudicate_tier2,
    get_adjudication_tier,
    compute_confidence_delta,
)


class TestTier1AutoLabels(unittest.TestCase):
    """Tier 1: Deterministic auto-labeling for high-confidence records."""

    def test_comparability_all_above_threshold(self):
        """All attribute scores > 0.85 => auto-label as COMPARABLE."""
        record = LabelRecord(
            module="comparability_graph",
            prediction={
                "verdict": "comparable",
                "attribute_scores": {"glycosylation": 0.92, "charge_variants": 0.88, "purity": 0.91},
            },
        )
        result = auto_adjudicate_tier1(record)
        self.assertIsNotNone(result)
        self.assertEqual(result.annotation_source, "deterministic")
        self.assertEqual(result.confidence_delta, 0.0)
        self.assertEqual(result.ground_truth["verdict"], "comparable")
        self.assertEqual(result.annotator, "auto_adjudicator_t1")

    def test_comparability_below_threshold_not_eligible(self):
        """One attribute score <= 0.85 => not eligible for Tier 1."""
        record = LabelRecord(
            module="comparability_graph",
            prediction={
                "verdict": "comparable",
                "attribute_scores": {"glycosylation": 0.92, "charge_variants": 0.80},
            },
        )
        result = auto_adjudicate_tier1(record)
        self.assertIsNone(result)

    def test_cqa_selector_with_rpn(self):
        """CQA selector with RPN data => eligible for Tier 1."""
        record = LabelRecord(
            module="cqa_selector",
            prediction={"rpn_score": 72, "rpn_threshold": 50, "classification": "critical"},
        )
        result = auto_adjudicate_tier1(record)
        self.assertIsNotNone(result)
        self.assertEqual(result.annotation_source, "deterministic")

    def test_section_classifier_high_confidence(self):
        """Section classifier with confidence > 0.95 => Tier 1."""
        record = LabelRecord(
            module="ctd_section_classifier",
            prediction={"section": "3.2.S.2.3", "confidence": 0.98},
        )
        result = auto_adjudicate_tier1(record)
        self.assertIsNotNone(result)
        self.assertEqual(result.annotation_source, "deterministic")

    def test_section_classifier_low_confidence_not_eligible(self):
        """Section classifier with confidence <= 0.95 => not Tier 1."""
        record = LabelRecord(
            module="ctd_section_classifier",
            prediction={"section": "3.2.S.2.3", "confidence": 0.90},
        )
        result = auto_adjudicate_tier1(record)
        self.assertIsNone(result)

    def test_wrong_module_not_eligible(self):
        """Non-Tier-1 module => returns None."""
        record = LabelRecord(
            module="fda_warning_letters",
            prediction={"category": "GMP", "confidence": 0.99},
        )
        result = auto_adjudicate_tier1(record)
        self.assertIsNone(result)


class TestTier2SilverLabels(unittest.TestCase):
    """Tier 2: LLM silver-label for classifier outputs."""

    def test_warning_letters_high_confidence(self):
        """Warning letter classifier with confidence >= 0.70 => silver label."""
        record = LabelRecord(
            module="fda_warning_letters",
            prediction={
                "category": "GMP_violation",
                "classifier_confidence": 0.82,
                "classifier_output": {"category": "GMP_violation", "severity": "major"},
            },
        )
        result = auto_adjudicate_tier2(record)
        self.assertIsNotNone(result)
        self.assertEqual(result.annotation_source, "llm_silver")
        self.assertEqual(result.annotator, "auto_adjudicator_t2")
        self.assertEqual(result.ground_truth["category"], "GMP_violation")

    def test_evidence_grader_silver_label(self):
        """Evidence grader with sufficient confidence => silver label."""
        record = LabelRecord(
            module="claim_evidence_grader",
            prediction={
                "strength": "moderate",
                "confidence": 0.75,
            },
        )
        result = auto_adjudicate_tier2(record)
        self.assertIsNotNone(result)
        self.assertEqual(result.annotation_source, "llm_silver")

    def test_low_confidence_not_eligible(self):
        """Classifier confidence < 0.70 => not eligible for Tier 2."""
        record = LabelRecord(
            module="fda_warning_letters",
            prediction={"category": "GMP", "classifier_confidence": 0.55},
        )
        result = auto_adjudicate_tier2(record)
        self.assertIsNone(result)

    def test_wrong_module_not_eligible(self):
        """Tier 1 module not eligible for Tier 2."""
        record = LabelRecord(
            module="comparability_graph",
            prediction={"confidence": 0.99},
        )
        result = auto_adjudicate_tier2(record)
        self.assertIsNone(result)


class TestTierDetection(unittest.TestCase):
    """get_adjudication_tier correctly classifies records."""

    def test_unlabeled_tier1_eligible(self):
        record = LabelRecord(
            module="comparability_graph",
            prediction={"attribute_scores": {"a": 0.90, "b": 0.95}},
        )
        self.assertEqual(get_adjudication_tier(record), 1)

    def test_unlabeled_tier2_eligible(self):
        record = LabelRecord(
            module="fda_warning_letters",
            prediction={"classifier_confidence": 0.80},
        )
        self.assertEqual(get_adjudication_tier(record), 2)

    def test_unlabeled_no_tier(self):
        record = LabelRecord(
            module="unknown_module",
            prediction={"something": 42},
        )
        self.assertEqual(get_adjudication_tier(record), 0)

    def test_labeled_deterministic(self):
        record = LabelRecord(
            module="cqa_selector",
            prediction={"rpn_score": 80},
            ground_truth={"rpn_score": 80},
            annotation_source="deterministic",
        )
        self.assertEqual(get_adjudication_tier(record), 1)

    def test_labeled_silver(self):
        record = LabelRecord(
            module="fda_warning_letters",
            prediction={"x": 1},
            ground_truth={"x": 1},
            annotation_source="llm_silver",
        )
        self.assertEqual(get_adjudication_tier(record), 2)

    def test_labeled_expert(self):
        record = LabelRecord(
            module="comparability_graph",
            prediction={"x": 1},
            ground_truth={"x": 2},
            annotation_source="expert",
        )
        self.assertEqual(get_adjudication_tier(record), 3)

    def test_labeled_regulatory_outcome(self):
        record = LabelRecord(
            module="gap_memo",
            prediction={"risk": "high"},
            ground_truth={"outcome": "CRL"},
            annotation_source="regulatory_outcome",
        )
        self.assertEqual(get_adjudication_tier(record), 4)

    def test_labeled_experimental(self):
        record = LabelRecord(
            module="comparability_graph",
            prediction={"stability": 0.9},
            ground_truth={"stability": 0.7},
            annotation_source="experimental",
        )
        self.assertEqual(get_adjudication_tier(record), 4)


class TestConfidenceDelta(unittest.TestCase):
    """compute_confidence_delta measures prediction-vs-ground-truth distance."""

    def test_identical_dicts(self):
        d = {"score": 0.8, "label": "high"}
        self.assertAlmostEqual(compute_confidence_delta(d, d), 0.0)

    def test_completely_different_keys(self):
        self.assertAlmostEqual(
            compute_confidence_delta({"a": 1}, {"b": 2}), 1.0
        )

    def test_numeric_difference(self):
        pred = {"score": 0.8}
        gt = {"score": 0.5}
        self.assertAlmostEqual(compute_confidence_delta(pred, gt), 0.3)

    def test_categorical_mismatch(self):
        pred = {"label": "high"}
        gt = {"label": "low"}
        self.assertAlmostEqual(compute_confidence_delta(pred, gt), 1.0)

    def test_mixed_fields(self):
        pred = {"score": 0.8, "label": "high"}
        gt = {"score": 0.6, "label": "high"}
        # score delta = 0.2, label delta = 0.0, average = 0.1
        self.assertAlmostEqual(compute_confidence_delta(pred, gt), 0.1)

    def test_empty_prediction(self):
        self.assertAlmostEqual(compute_confidence_delta({}, {"a": 1}), 1.0)

    def test_empty_ground_truth(self):
        self.assertAlmostEqual(compute_confidence_delta({"a": 1}, {}), 1.0)

    def test_numeric_clamped_to_one(self):
        """Large numeric differences are clamped to 1.0."""
        pred = {"val": 0.0}
        gt = {"val": 5.0}
        self.assertAlmostEqual(compute_confidence_delta(pred, gt), 1.0)


class TestAutoAdjudicateSkipsLabeled(unittest.TestCase):
    """Auto-adjudication must skip records that already have ground truth."""

    def test_tier1_skips_already_labeled(self):
        record = LabelRecord(
            module="comparability_graph",
            prediction={"attribute_scores": {"a": 0.95}},
            ground_truth={"attribute_scores": {"a": 0.95}},
            annotation_source="expert",
        )
        result = auto_adjudicate_tier1(record)
        self.assertIsNone(result)

    def test_tier2_skips_already_labeled(self):
        record = LabelRecord(
            module="fda_warning_letters",
            prediction={"classifier_confidence": 0.85, "category": "GMP"},
            ground_truth={"category": "GMP"},
            annotation_source="expert",
        )
        result = auto_adjudicate_tier2(record)
        self.assertIsNone(result)


class TestLabelStoreAdjudication(unittest.TestCase):
    """Integration: LabelStore.auto_adjudicate and get_adjudication_stats."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = LabelStore(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_auto_adjudicate_tier1_records(self):
        """auto_adjudicate fills Tier 1 records."""
        self.store.save_record(LabelRecord(
            module="comparability_graph",
            prediction={"attribute_scores": {"a": 0.92, "b": 0.90}},
        ))
        self.store.save_record(LabelRecord(
            module="comparability_graph",
            prediction={"attribute_scores": {"a": 0.50}},  # below threshold
        ))

        count = self.store.auto_adjudicate("comparability_graph")
        self.assertEqual(count, 1)

        records = self.store.get_records("comparability_graph")
        labeled = [r for r in records if r.is_labeled]
        self.assertEqual(len(labeled), 1)
        self.assertEqual(labeled[0].annotation_source, "deterministic")

    def test_auto_adjudicate_tier2_records(self):
        """auto_adjudicate fills Tier 2 records."""
        self.store.save_record(LabelRecord(
            module="fda_warning_letters",
            prediction={"category": "GMP", "classifier_confidence": 0.80},
        ))

        count = self.store.auto_adjudicate("fda_warning_letters")
        self.assertEqual(count, 1)

        records = self.store.get_records("fda_warning_letters")
        self.assertTrue(records[0].is_labeled)
        self.assertEqual(records[0].annotation_source, "llm_silver")

    def test_auto_adjudicate_preserves_already_labeled(self):
        """Already-labeled records are untouched by auto_adjudicate."""
        self.store.save_record(LabelRecord(
            module="comparability_graph",
            prediction={"attribute_scores": {"a": 0.95}},
            ground_truth={"manual": True},
            annotation_source="expert",
        ))

        count = self.store.auto_adjudicate("comparability_graph")
        self.assertEqual(count, 0)

        records = self.store.get_records("comparability_graph")
        self.assertEqual(records[0].annotation_source, "expert")

    def test_auto_adjudicate_empty_module(self):
        """auto_adjudicate on empty/nonexistent module returns 0."""
        self.assertEqual(self.store.auto_adjudicate("nonexistent"), 0)

    def test_get_adjudication_stats(self):
        """get_adjudication_stats returns correct tier breakdown."""
        # Tier 1 labeled
        self.store.save_record(LabelRecord(
            module="m1",
            prediction={"x": 1},
            ground_truth={"x": 1},
            annotation_source="deterministic",
        ))
        # Tier 3 labeled
        self.store.save_record(LabelRecord(
            module="m1",
            prediction={"x": 2},
            ground_truth={"x": 3},
            annotation_source="expert",
        ))
        # Unlabeled (tier 0)
        self.store.save_record(LabelRecord(
            module="m1",
            prediction={"x": 4},
        ))

        stats = self.store.get_adjudication_stats()
        self.assertIn("m1", stats)
        self.assertEqual(stats["m1"]["total"], 3)
        self.assertEqual(stats["m1"]["tier_1_deterministic"], 1)
        self.assertEqual(stats["m1"]["tier_3_expert"], 1)
        self.assertEqual(stats["m1"]["tier_0_unlabeled"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
