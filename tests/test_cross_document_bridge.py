"""
Tests for Cross-Document Intelligence Bridge (SP v5 P4).

Verifies:
  1. Interfaces are importable and abstract methods enforced.
  2. Value objects (ExtractedAttribute, etc.) instantiate correctly.
  3. CSVBridge parses a simple CSV and produces pipeline-ready output.
"""

from __future__ import annotations

import csv
import os
import tempfile
import unittest

# ---------------------------------------------------------------------------
# 1. Interface import and ABC enforcement
# ---------------------------------------------------------------------------

class TestInterfacesImportable(unittest.TestCase):
    """All interfaces and value objects should be importable."""

    def test_import_interfaces(self):
        from specs.cross_document_bridge import (
            DocumentParser,
            TableExtractor,
            AttributeExtractor,
            CrossDocumentReconciler,
            BridgeOrchestrator,
        )
        # They exist and are classes
        for cls in (DocumentParser, TableExtractor, AttributeExtractor,
                    CrossDocumentReconciler, BridgeOrchestrator):
            self.assertTrue(callable(cls))

    def test_import_value_objects(self):
        from specs.cross_document_bridge import (
            DocumentType,
            ExtractedAttribute,
            ReconciliationConflict,
            ReconciliationResult,
        )
        self.assertIn("CTD_MODULE_3", DocumentType.__members__)
        self.assertIn("CERTIFICATE_OF_ANALYSIS", DocumentType.__members__)

    def test_cannot_instantiate_abstract(self):
        from specs.cross_document_bridge import DocumentParser
        with self.assertRaises(TypeError):
            DocumentParser()

    def test_cannot_instantiate_abstract_bridge(self):
        from specs.cross_document_bridge import BridgeOrchestrator
        with self.assertRaises(TypeError):
            BridgeOrchestrator()


# ---------------------------------------------------------------------------
# 2. Value objects
# ---------------------------------------------------------------------------

class TestExtractedAttribute(unittest.TestCase):

    def test_create_extracted_attribute(self):
        from specs.cross_document_bridge import ExtractedAttribute
        attr = ExtractedAttribute(
            name="SEC Purity (Main Peak)",
            value=98.5,
            unit="%",
            source_document="comparability_report.pdf",
            source_page=12,
            source_table="Table 3.2.S.4.1-1",
            confidence=0.95,
            context="The main peak purity was 98.5%.",
            category="purity",
        )
        self.assertEqual(attr.name, "SEC Purity (Main Peak)")
        self.assertEqual(attr.value, 98.5)
        self.assertEqual(attr.confidence, 0.95)
        self.assertEqual(attr.category, "purity")
        self.assertEqual(attr.metadata, {})  # default

    def test_reconciliation_result(self):
        from specs.cross_document_bridge import (
            ExtractedAttribute, ReconciliationConflict, ReconciliationResult,
        )
        result = ReconciliationResult(
            harmonized_attributes=[],
            conflicts=[
                ReconciliationConflict(
                    attribute_name="SEC Purity",
                    values=[
                        {"source": "CoA", "value": 98.5, "confidence": 1.0},
                        {"source": "Report", "value": 97.8, "confidence": 0.9},
                    ],
                    severity="major",
                ),
            ],
            source_documents=["coa.pdf", "report.pdf"],
            n_total_extracted=10,
            n_conflicts=1,
            n_resolved=0,
        )
        self.assertEqual(result.n_conflicts, 1)
        self.assertEqual(result.conflicts[0].severity, "major")


# ---------------------------------------------------------------------------
# 3. CSV Adapter
# ---------------------------------------------------------------------------

class TestCSVBridge(unittest.TestCase):

    def setUp(self):
        """Create a temporary CSV with sample comparability data."""
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
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_csv_parser_returns_parsed_schema(self):
        from specs.csv_adapter import CSVDocumentParser
        parser = CSVDocumentParser()
        result = parser.parse(self._csv_path)

        self.assertIn("document_path", result)
        self.assertIn("pages", result)
        self.assertEqual(len(result["pages"]), 1)
        tables = result["pages"][0]["tables"]
        self.assertEqual(len(tables), 1)
        self.assertEqual(len(tables[0]["rows"]), 3)

    def test_csv_parser_maps_columns(self):
        from specs.csv_adapter import CSVDocumentParser
        parser = CSVDocumentParser()
        result = parser.parse(self._csv_path)
        row = result["pages"][0]["tables"][0]["rows"][0]
        self.assertEqual(row["name"], "SEC Purity (Main Peak)")
        self.assertAlmostEqual(row["pre_value"], 98.5)
        self.assertAlmostEqual(row["post_value"], 97.8)
        self.assertEqual(row["unit"], "%")

    def test_csv_parser_numeric_conversion(self):
        from specs.csv_adapter import CSVDocumentParser
        parser = CSVDocumentParser()
        result = parser.parse(self._csv_path)
        row = result["pages"][0]["tables"][0]["rows"][1]
        self.assertIsInstance(row["pre_value"], float)
        self.assertIsInstance(row["n_lots"], float)

    def test_csv_parser_file_not_found(self):
        from specs.csv_adapter import CSVDocumentParser
        parser = CSVDocumentParser()
        with self.assertRaises(FileNotFoundError):
            parser.parse("/nonexistent/file.csv")

    def test_csv_parser_supported_formats(self):
        from specs.csv_adapter import CSVDocumentParser
        parser = CSVDocumentParser()
        self.assertEqual(parser.supported_formats(), [".csv"])

    def test_csv_attribute_extractor(self):
        from specs.csv_adapter import CSVDocumentParser, CSVAttributeExtractor
        parser = CSVDocumentParser()
        parsed = parser.parse(self._csv_path)

        extractor = CSVAttributeExtractor()
        attrs = extractor.extract_attributes(parsed)

        # 3 rows x 2 phases (pre, post) = 6 ExtractedAttribute objects
        self.assertEqual(len(attrs), 6)
        names = [a.name for a in attrs]
        self.assertIn("SEC Purity (Main Peak)", names)
        self.assertIn("Potency (Cell-Based)", names)

        # Check confidence = 1.0 for CSV
        for a in attrs:
            self.assertEqual(a.confidence, 1.0)

    def test_csv_bridge_ingest(self):
        from specs.csv_adapter import CSVBridge
        bridge = CSVBridge()
        result = bridge.ingest([self._csv_path], product_name="mAb-X")

        self.assertIn("attributes", result)
        self.assertEqual(len(result["attributes"]), 3)

        # Check first attribute
        attr = result["attributes"][0]
        self.assertEqual(attr["name"], "SEC Purity (Main Peak)")
        self.assertAlmostEqual(attr["pre_value"], 98.5)
        self.assertAlmostEqual(attr["post_value"], 97.8)
        self.assertEqual(attr["unit"], "%")
        self.assertEqual(attr["category"], "purity")

    def test_csv_bridge_ingest_has_optional_fields(self):
        from specs.csv_adapter import CSVBridge
        bridge = CSVBridge()
        result = bridge.ingest([self._csv_path])
        attr = result["attributes"][0]
        self.assertAlmostEqual(attr["n_lots"], 5.0)
        self.assertAlmostEqual(attr["cv_pct"], 1.2)

    def test_csv_bridge_multiple_files(self):
        """CSVBridge should concatenate rows from multiple CSVs."""
        # Create a second CSV
        csv2_path = os.path.join(self._tmpdir, "test_batch_2.csv")
        with open(csv2_path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Attribute", "Category", "Pre_Value", "Post_Value", "Unit"])
            writer.writerow(["Charge Variants", "physicochemical", "15.0", "16.5", "%"])

        from specs.csv_adapter import CSVBridge
        bridge = CSVBridge()
        result = bridge.ingest([self._csv_path, csv2_path])
        self.assertEqual(len(result["attributes"]), 4)

    def test_csv_bridge_implements_orchestrator(self):
        from specs.cross_document_bridge import BridgeOrchestrator
        from specs.csv_adapter import CSVBridge
        bridge = CSVBridge()
        self.assertIsInstance(bridge, BridgeOrchestrator)


if __name__ == "__main__":
    unittest.main()
