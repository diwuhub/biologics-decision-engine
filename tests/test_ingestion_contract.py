"""
Tests for P7 Ingestion Contract (v4.3.1).

Verifies:
  P7-A: All new schemas import correctly and instantiate.
  P7-B: ExtractedAttribute extensions work without breaking existing fields.
  P7-C: Pipeline adapter converts IngestionResult -> pipeline-ready dict.
  P7-D: Round-trip test -- CSV -> IngestionResult -> pipeline produces same
        results as the direct CSV path.
"""

from __future__ import annotations

import csv
import os
import tempfile
import shutil
import unittest
from datetime import datetime


class TestP7ASchemaImports(unittest.TestCase):
    """P7-A: All new schemas import and instantiate correctly."""

    def test_import_evidence_anchor(self):
        from specs.cross_document_bridge import EvidenceAnchor
        anchor = EvidenceAnchor(
            anchor_id="a1",
            document_id="doc1",
            page=5,
            section_title="Section 3.2.S.4.1",
            paragraph_index=2,
            table_index=1,
            table_row=3,
            table_col=4,
            snippet="Purity was measured at 98.5%.",
            snippet_context="In the comparability study, purity was measured at 98.5% by SEC.",
        )
        self.assertEqual(anchor.anchor_id, "a1")
        self.assertEqual(anchor.page, 5)

    def test_import_extracted_case_context(self):
        from specs.cross_document_bridge import ExtractedCaseContext
        ctx = ExtractedCaseContext(
            product_name="mAb-X",
            molecule_class="mAb",
            molecule_class_confidence=0.95,
            change_type="process",
            change_description="CHO cell line change",
            source_anchors=["a1", "a2"],
            extraction_notes=["High confidence extraction"],
        )
        self.assertEqual(ctx.product_name, "mAb-X")
        self.assertEqual(len(ctx.source_anchors), 2)

    def test_import_extraction_issue(self):
        from specs.cross_document_bridge import ExtractionIssue
        issue = ExtractionIssue(
            issue_id="i1",
            severity="warning",
            description="Ambiguous unit in table",
            affected_attribute="SEC Purity",
            source_anchor_id="a1",
            resolution_hint="Verify unit with source document",
        )
        self.assertEqual(issue.severity, "warning")
        self.assertFalse(issue.resolved)

    def test_import_narrative_signal(self):
        from specs.cross_document_bridge import NarrativeSignal
        signal = NarrativeSignal(
            signal_type="oos",
            text="One lot was OOS for purity.",
            anchor_id="a1",
            confidence=0.8,
            affects_attributes=["SEC Purity (Main Peak)"],
        )
        self.assertEqual(signal.signal_type, "oos")

    def test_import_user_override(self):
        from specs.cross_document_bridge import UserOverride
        override = UserOverride(
            override_id="o1",
            attribute_name="SEC Purity (Main Peak)",
            field_name="pre_value",
            original_value=98.5,
            corrected_value=98.7,
            corrected_by="analyst",
            reason="Typo in source document",
            source_anchor_ids=["a1"],
            resolved_issue_id="i1",
            timestamp="2026-03-30T12:00:00Z",
        )
        self.assertEqual(override.corrected_value, 98.7)

    def test_import_ingestion_result(self):
        from specs.cross_document_bridge import (
            EvidenceAnchor, ExtractedAttribute, ExtractedCaseContext,
            ExtractionIssue, IngestionResult, NarrativeSignal, UserOverride,
        )
        result = IngestionResult(
            document_id="doc1",
            source_filename="report.pdf",
            case_context=ExtractedCaseContext(
                product_name="mAb-X",
                molecule_class="mAb",
                molecule_class_confidence=0.95,
                change_type="process",
                change_description="CHO cell line change",
                source_anchors=[],
                extraction_notes=[],
            ),
            attributes=[],
            anchors=[],
            issues=[],
            narrative_signals=[],
            user_overrides=[],
            extraction_timestamp="2026-03-30T12:00:00Z",
            parser_version="1.0.0",
        )
        self.assertEqual(result.document_id, "doc1")
        self.assertEqual(result.parser_version, "1.0.0")


class TestP7BExtractedAttributeExtensions(unittest.TestCase):
    """P7-B: ExtractedAttribute new fields work without breaking old ones."""

    def test_legacy_fields_still_work(self):
        from specs.cross_document_bridge import ExtractedAttribute
        attr = ExtractedAttribute(
            name="SEC Purity (Main Peak)",
            value=98.5,
            unit="%",
            source_document="report.pdf",
            source_page=12,
            source_table="Table 3.2.S.4.1-1",
            confidence=0.95,
            context="Purity was 98.5%.",
            category="purity",
        )
        self.assertEqual(attr.name, "SEC Purity (Main Peak)")
        self.assertEqual(attr.value, 98.5)
        self.assertEqual(attr.metadata, {})

    def test_new_p7b_fields(self):
        from specs.cross_document_bridge import ExtractedAttribute
        attr = ExtractedAttribute(
            name="SEC Purity (Main Peak)",
            value=98.5,
            unit="%",
            source_document="report.pdf",
            source_page=12,
            source_table="Table 3.2.S.4.1-1",
            confidence=0.95,
            context="Purity was 98.5%.",
            pre_value=98.5,
            post_value=97.8,
            anchor_ids=["a1", "a2"],
            extraction_confidence=0.92,
            n_lots=5,
            cv_pct=1.2,
        )
        self.assertEqual(attr.pre_value, 98.5)
        self.assertEqual(attr.post_value, 97.8)
        self.assertEqual(attr.anchor_ids, ["a1", "a2"])
        self.assertEqual(attr.extraction_confidence, 0.92)
        self.assertEqual(attr.n_lots, 5)
        self.assertAlmostEqual(attr.cv_pct, 1.2)

    def test_new_fields_have_defaults(self):
        from specs.cross_document_bridge import ExtractedAttribute
        attr = ExtractedAttribute(
            name="Test",
            value=1.0,
            unit="%",
            source_document="x",
            source_page=1,
            source_table="t1",
            confidence=1.0,
            context="ctx",
        )
        self.assertIsNone(attr.pre_value)
        self.assertIsNone(attr.post_value)
        self.assertEqual(attr.anchor_ids, [])
        self.assertEqual(attr.extraction_confidence, 1.0)
        self.assertIsNone(attr.n_lots)
        self.assertIsNone(attr.cv_pct)


class TestP7CPipelineAdapter(unittest.TestCase):
    """P7-C: Pipeline adapter correctly converts IngestionResult."""

    def _make_ingestion_result(self, overrides=None):
        from specs.cross_document_bridge import (
            ExtractedAttribute, ExtractedCaseContext,
            IngestionResult, UserOverride,
        )
        attrs = [
            ExtractedAttribute(
                name="SEC Purity (Main Peak)",
                value=0.0,
                unit="%",
                source_document="report.pdf",
                source_page=1,
                source_table="t1",
                confidence=1.0,
                context="ctx",
                category="purity",
                pre_value=98.5,
                post_value=97.8,
                n_lots=5,
                cv_pct=1.2,
            ),
            ExtractedAttribute(
                name="Protein Concentration",
                value=0.0,
                unit="mg/mL",
                source_document="report.pdf",
                source_page=1,
                source_table="t1",
                confidence=1.0,
                context="ctx",
                category="physicochemical",
                pre_value=50.0,
                post_value=49.5,
                n_lots=3,
                cv_pct=2.0,
            ),
            ExtractedAttribute(
                name="Potency (Cell-Based)",
                value=0.0,
                unit="%",
                source_document="report.pdf",
                source_page=1,
                source_table="t1",
                confidence=1.0,
                context="ctx",
                category="biological_activity",
                pre_value=105.0,
                post_value=100.0,
                n_lots=4,
                cv_pct=5.0,
            ),
        ]
        return IngestionResult(
            document_id="doc1",
            source_filename="report.pdf",
            case_context=ExtractedCaseContext(
                product_name="mAb-X",
                molecule_class="mAb",
                molecule_class_confidence=0.95,
                change_type="process",
                change_description="CHO cell line change",
                source_anchors=[],
                extraction_notes=[],
            ),
            attributes=attrs,
            anchors=[],
            issues=[],
            narrative_signals=[],
            user_overrides=overrides or [],
            extraction_timestamp="2026-03-30T12:00:00Z",
            parser_version="1.0.0",
        )

    def test_basic_conversion(self):
        from ingestion.pipeline_adapter import ingestion_to_pipeline_input
        result = self._make_ingestion_result()
        pipeline_input = ingestion_to_pipeline_input(result)

        self.assertIn("attributes", pipeline_input)
        self.assertEqual(len(pipeline_input["attributes"]), 3)
        self.assertEqual(pipeline_input["molecule_class"], "mAb")

        attr0 = pipeline_input["attributes"][0]
        self.assertEqual(attr0["name"], "SEC Purity (Main Peak)")
        self.assertAlmostEqual(attr0["pre_value"], 98.5)
        self.assertAlmostEqual(attr0["post_value"], 97.8)
        self.assertEqual(attr0["unit"], "%")
        self.assertEqual(attr0["n_lots"], 5)

    def test_user_override_applied(self):
        from specs.cross_document_bridge import UserOverride
        from ingestion.pipeline_adapter import ingestion_to_pipeline_input

        overrides = [
            UserOverride(
                override_id="o1",
                attribute_name="SEC Purity (Main Peak)",
                field_name="pre_value",
                original_value=98.5,
                corrected_value=99.0,
                corrected_by="analyst",
                reason="Typo",
                source_anchor_ids=[],
                resolved_issue_id=None,
                timestamp="2026-03-30T12:00:00Z",
            ),
        ]
        result = self._make_ingestion_result(overrides=overrides)
        pipeline_input = ingestion_to_pipeline_input(result)

        attr0 = pipeline_input["attributes"][0]
        self.assertAlmostEqual(attr0["pre_value"], 99.0)


class TestP7DRoundTrip(unittest.TestCase):
    """P7-D: CSV -> IngestionResult -> pipeline produces same results as direct CSV path."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._csv_path = os.path.join(self._tmpdir, "test_batch.csv")
        with open(self._csv_path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "Attribute", "Category", "Pre_Value", "Post_Value",
                "Unit", "n_lots", "cv_pct",
            ])
            writer.writerow([
                "SEC Purity (Main Peak)", "purity", "98.5", "97.8",
                "%", "5", "1.2",
            ])
            writer.writerow([
                "Protein Concentration", "physicochemical", "50.0", "49.5",
                "mg/mL", "3", "2.0",
            ])
            writer.writerow([
                "Potency (Cell-Based)", "biological_activity", "105", "100",
                "%", "4", "5.0",
            ])

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _csv_to_ingestion_result(self, csv_path):
        """Simulate building an IngestionResult from CSV data (same data the CSVBridge reads)."""
        from specs.cross_document_bridge import (
            ExtractedAttribute, ExtractedCaseContext, IngestionResult,
        )
        from specs.csv_adapter import CSVDocumentParser

        parser = CSVDocumentParser()
        parsed = parser.parse(csv_path)

        attrs = []
        for page in parsed.get("pages", []):
            for table in page.get("tables", []):
                for row in table.get("rows", []):
                    name = row.get("name")
                    if not name:
                        continue
                    attrs.append(ExtractedAttribute(
                        name=name,
                        value=0.0,
                        unit=row.get("unit", ""),
                        source_document=csv_path,
                        source_page=1,
                        source_table=table.get("id", ""),
                        confidence=1.0,
                        context="CSV round-trip",
                        category=row.get("category", "physicochemical"),
                        pre_value=float(row.get("pre_value", 0)),
                        post_value=float(row.get("post_value", 0)),
                        n_lots=int(row["n_lots"]) if "n_lots" in row else None,
                        cv_pct=float(row["cv_pct"]) if "cv_pct" in row else None,
                    ))

        return IngestionResult(
            document_id="roundtrip-doc",
            source_filename=os.path.basename(csv_path),
            case_context=ExtractedCaseContext(
                product_name="mAb-X",
                molecule_class="mAb",
                molecule_class_confidence=1.0,
                change_type="process",
                change_description="Round-trip test",
                source_anchors=[],
                extraction_notes=[],
            ),
            attributes=attrs,
            anchors=[],
            issues=[],
            narrative_signals=[],
            user_overrides=[],
            extraction_timestamp=datetime.utcnow().isoformat(),
            parser_version="1.0.0-test",
        )

    def test_round_trip_same_results(self):
        """CSV -> IngestionResult -> pipeline must match direct CSV -> pipeline."""
        from specs.csv_adapter import CSVBridge
        from ingestion.pipeline_adapter import ingestion_to_pipeline_input
        from pipelines.comparability import run_comparability_assessment

        # Path A: Direct CSV via CSVBridge
        bridge = CSVBridge()
        direct_input = bridge.ingest([self._csv_path], product_name="mAb-X")
        direct_report = run_comparability_assessment(
            direct_input, product_name="mAb-X",
            change_description="Round-trip test",
        )

        # Path B: CSV -> IngestionResult -> pipeline adapter
        ingestion_result = self._csv_to_ingestion_result(self._csv_path)
        adapter_input = ingestion_to_pipeline_input(ingestion_result)
        adapter_report = run_comparability_assessment(
            adapter_input, product_name="mAb-X",
            change_description="Round-trip test",
        )

        # Compare key outcomes
        self.assertEqual(direct_report.overall_verdict, adapter_report.overall_verdict)
        self.assertEqual(direct_report.n_attributes, adapter_report.n_attributes)
        self.assertEqual(direct_report.n_comparable, adapter_report.n_comparable)
        self.assertEqual(direct_report.n_flagged, adapter_report.n_flagged)

        # Compare per-attribute scores
        for d_attr, a_attr in zip(
            sorted(direct_report.attribute_results, key=lambda x: x.name),
            sorted(adapter_report.attribute_results, key=lambda x: x.name),
        ):
            self.assertEqual(d_attr.name, a_attr.name)
            self.assertAlmostEqual(d_attr.pre_value, a_attr.pre_value, places=4)
            self.assertAlmostEqual(d_attr.post_value, a_attr.post_value, places=4)
            self.assertAlmostEqual(d_attr.score, a_attr.score, places=4)
            self.assertEqual(d_attr.concern, a_attr.concern)


if __name__ == "__main__":
    unittest.main()
