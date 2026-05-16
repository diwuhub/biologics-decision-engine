"""Tests for label_store.py — JSONL storage."""
import os, sys, json, tempfile, shutil, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from schemas.label_schema import LabelRecord, FeedbackEvent
from schemas.label_store import LabelStore


class TestLabelStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = LabelStore(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_save_and_load(self):
        r = LabelRecord(module="test_mod", prediction={"score": 0.8})
        rid = self.store.save_record(r)
        self.assertEqual(rid, r.record_id)

        records = self.store.get_records("test_mod")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].prediction["score"], 0.8)

    def test_unlabeled_query(self):
        self.store.save_record(LabelRecord(module="m", prediction={"a": 1}))
        self.store.save_record(LabelRecord(module="m", prediction={"a": 2}, ground_truth={"a": 2}))
        self.store.save_record(LabelRecord(module="m", prediction={"a": 3}))

        unlabeled = self.store.get_unlabeled("m")
        self.assertEqual(len(unlabeled), 2)

        labeled = self.store.get_records("m", labeled_only=True)
        self.assertEqual(len(labeled), 1)

    def test_training_pairs(self):
        self.store.save_record(LabelRecord(module="m", prediction={"p": 1}, ground_truth={"g": 1}))
        self.store.save_record(LabelRecord(module="m", prediction={"p": 2}, ground_truth={"g": 2}))
        self.store.save_record(LabelRecord(module="m", prediction={"p": 3}))  # unlabeled

        pairs = self.store.get_training_pairs("m")
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0], ({"p": 1}, {"g": 1}))

    def test_csv_export(self):
        self.store.save_record(LabelRecord(module="m", prediction={"x": 1}, ground_truth={"y": 1}))
        self.store.save_record(LabelRecord(module="m", prediction={"x": 2}, ground_truth={"y": 2}))

        csv_path = os.path.join(self.tmpdir, "export.csv")
        count = self.store.export_training_csv("m", csv_path)
        self.assertEqual(count, 2)
        self.assertTrue(os.path.exists(csv_path))

        with open(csv_path) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 3)  # header + 2 data rows

    def test_multi_module_isolation(self):
        self.store.save_record(LabelRecord(module="mod_a", prediction={"a": 1}))
        self.store.save_record(LabelRecord(module="mod_b", prediction={"b": 2}))
        self.store.save_record(LabelRecord(module="mod_a", prediction={"a": 3}))

        self.assertEqual(len(self.store.get_records("mod_a")), 2)
        self.assertEqual(len(self.store.get_records("mod_b")), 1)
        self.assertEqual(len(self.store.get_records("mod_c")), 0)

    def test_feedback_save_and_load(self):
        e = FeedbackEvent(record_id="rec-1", action="accept", reason="looks correct")
        eid = self.store.save_feedback(e)
        self.assertEqual(eid, e.event_id)

        events = self.store.get_feedback()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, "accept")

    def test_stats(self):
        self.store.save_record(LabelRecord(module="m1", prediction={"a": 1}))
        self.store.save_record(LabelRecord(module="m1", prediction={"a": 2}, ground_truth={"b": 2}))
        self.store.save_record(LabelRecord(module="m2", prediction={"c": 3}))

        s = self.store.stats()
        self.assertIn("m1", s)
        self.assertEqual(s["m1"]["total"], 2)
        self.assertEqual(s["m1"]["labeled"], 1)
        self.assertEqual(s["m1"]["unlabeled"], 1)
        self.assertIn("m2", s)

    def test_empty_module(self):
        records = self.store.get_records("nonexistent")
        self.assertEqual(records, [])
        pairs = self.store.get_training_pairs("nonexistent")
        self.assertEqual(pairs, [])

    def test_csv_export_empty(self):
        csv_path = os.path.join(self.tmpdir, "empty.csv")
        count = self.store.export_training_csv("nonexistent", csv_path)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
