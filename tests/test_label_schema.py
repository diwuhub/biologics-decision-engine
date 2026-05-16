"""Tests for label_schema.py — all 4 dataclasses."""
import os, sys, unittest, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from schemas.label_schema import LabelRecord, FeedbackEvent, EvidenceClaim, NAMReadinessRecord


class TestLabelRecord(unittest.TestCase):
    def test_creation_defaults(self):
        r = LabelRecord(module="test", prediction={"score": 0.5})
        self.assertTrue(r.record_id)  # auto-generated UUID
        self.assertTrue(r.timestamp)  # auto-generated
        self.assertFalse(r.is_labeled)
        self.assertIsNone(r.ground_truth)

    def test_round_trip(self):
        r = LabelRecord(module="cqa", prediction={"rpn": 42}, metadata={"version": "1.0"})
        d = r.to_dict()
        r2 = LabelRecord.from_dict(d)
        self.assertEqual(r.module, r2.module)
        self.assertEqual(r.prediction, r2.prediction)
        self.assertEqual(r.record_id, r2.record_id)

    def test_labeled_flag(self):
        r = LabelRecord(module="test", prediction={"a": 1})
        self.assertFalse(r.is_labeled)
        r.ground_truth = {"a": 2}
        self.assertTrue(r.is_labeled)

    def test_optional_fields_none(self):
        r = LabelRecord(module="test", prediction={})
        d = r.to_dict()
        self.assertIsNone(d["annotator"])
        self.assertIsNone(d["annotation_source"])
        self.assertIsNone(d["confidence_delta"])


class TestFeedbackEvent(unittest.TestCase):
    def test_creation(self):
        e = FeedbackEvent(record_id="rec-123", action="accept")
        self.assertTrue(e.event_id)
        self.assertEqual(e.action, "accept")

    def test_round_trip(self):
        e = FeedbackEvent(record_id="rec-123", action="modify",
                          modified_value={"score": 0.9}, reason="expert correction")
        d = e.to_dict()
        e2 = FeedbackEvent.from_dict(d)
        self.assertEqual(e.record_id, e2.record_id)
        self.assertEqual(e.modified_value, e2.modified_value)

    def test_actions(self):
        for action in ["accept", "reject", "modify"]:
            e = FeedbackEvent(record_id="r", action=action)
            self.assertEqual(e.action, action)


class TestEvidenceClaim(unittest.TestCase):
    def test_creation(self):
        c = EvidenceClaim(claim_text="HER2 drives proliferation", source_type="journal_paper")
        self.assertTrue(c.claim_id)
        self.assertEqual(c.source_type, "journal_paper")

    def test_round_trip(self):
        c = EvidenceClaim(
            claim_text="IL-4R blockade reduces inflammation",
            source_type="journal_paper",
            extracted_entities=["IL-4R", "dupilumab"],
            evidence_strength="strong",
            six_question_scores={"biology_credible": 0.9, "signal_measurable": 0.8},
        )
        d = c.to_dict()
        c2 = EvidenceClaim.from_dict(d)
        self.assertEqual(c.claim_text, c2.claim_text)
        self.assertEqual(c.extracted_entities, c2.extracted_entities)
        self.assertEqual(c.six_question_scores, c2.six_question_scores)

    def test_optional_gaps(self):
        c = EvidenceClaim(claim_text="test", source_type="conference_abstract",
                          admissibility_gap=["no clinical data", "no PK model"])
        self.assertEqual(len(c.admissibility_gap), 2)


class TestNAMReadinessRecord(unittest.TestCase):
    def test_creation(self):
        n = NAMReadinessRecord(nam_type="organoid", context_of_use="hepatotoxicity screening")
        self.assertTrue(n.record_id)
        self.assertEqual(n.nam_type, "organoid")

    def test_round_trip(self):
        n = NAMReadinessRecord(
            nam_type="organ_on_chip", context_of_use="cardiac safety",
            species_replaced="dog",
            validation_evidence=[{"study_type": "concordance", "concordance_rate": 0.85, "sample_size": 30}],
            regulatory_precedent=["FDA DDT for liver organoid"],
            readiness_score=0.65,
            readiness_gaps=["No GLP validation", "Limited chemical diversity"],
            qualification_pathway="DDT",
        )
        d = n.to_dict()
        n2 = NAMReadinessRecord.from_dict(d)
        self.assertEqual(n.nam_type, n2.nam_type)
        self.assertEqual(n.readiness_score, n2.readiness_score)
        self.assertEqual(len(n.validation_evidence), len(n2.validation_evidence))
        self.assertEqual(n.qualification_pathway, n2.qualification_pathway)


if __name__ == "__main__":
    unittest.main(verbosity=2)
