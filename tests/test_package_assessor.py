"""Tests for PackageAssessor — multi-document package judgment."""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from services.package_assessor import assess_package, build_package_overview, PackageCase


class TestPackageAssessment:
    def test_full_package_assessed(self):
        """3 real documents — full package assessment."""
        from ingestion import ingest_document
        files = [
            "benchmarks/real_documents/NISTmAb_SP260-237.pdf",
            "benchmarks/real_documents/Xbonzy_EPAR.pdf",
            "benchmarks/real_documents/ICH_Q14_2023.pdf",
        ]
        names = ["NISTmAb.pdf", "Xbonzy.pdf", "ICH_Q14.pdf"]
        results = [ingest_document(f) for f in files]
        pkg = assess_package(results, names)

        assert len(pkg.documents) == 3
        assert all(d.error is None for d in pkg.documents)
        assert pkg.package_verdict != ""
        assert pkg.package_confidence > 0

    def test_coverage_all_types(self):
        from ingestion import ingest_document
        files = [
            "benchmarks/real_documents/NISTmAb_SP260-237.pdf",
            "benchmarks/real_documents/Xbonzy_EPAR.pdf",
            "benchmarks/real_documents/ICH_Q14_2023.pdf",
        ]
        results = [ingest_document(f) for f in files]
        pkg = assess_package(results, ["a.pdf", "b.pdf", "c.pdf"])
        assert pkg.document_coverage.get("CHARACTERIZATION") is True
        assert pkg.document_coverage.get("STABILITY") is True
        assert pkg.document_coverage.get("ANALYTICAL_METHOD") is True
        assert len(pkg.missing_types) == 0

    def test_single_doc_incomplete(self):
        from ingestion import ingest_document
        r = ingest_document("benchmarks/real_documents/NISTmAb_SP260-237.pdf")
        pkg = assess_package([r], ["NISTmAb.pdf"])
        assert "STABILITY" in pkg.missing_types
        assert "ANALYTICAL_METHOD" in pkg.missing_types
        assert pkg.package_verdict in ("PACKAGE_INCOMPLETE", "PACKAGE_NEEDS_SUPPLEMENT")

    def test_overview_structure(self):
        from ingestion import ingest_document
        r = ingest_document("benchmarks/real_documents/NISTmAb_SP260-237.pdf")
        pkg = assess_package([r], ["test.pdf"])
        ov = build_package_overview(pkg)

        required = {"package_id", "package_verdict", "package_verdict_display",
                     "package_confidence", "package_rationale",
                     "document_summaries", "document_coverage",
                     "missing_types", "cross_document_flags",
                     "reviewer_questions", "n_documents"}
        assert required.issubset(ov.keys())

    def test_reviewer_questions_aggregated(self):
        from ingestion import ingest_document
        files = [
            "benchmarks/real_documents/NISTmAb_SP260-237.pdf",
            "benchmarks/real_documents/Xbonzy_EPAR.pdf",
        ]
        results = [ingest_document(f) for f in files]
        pkg = assess_package(results, ["a.pdf", "b.pdf"])
        # Should have per-document questions + package-level questions for missing types
        assert len(pkg.reviewer_questions) > 0
        sources = {q.get("source_doc_type") for q in pkg.reviewer_questions}
        assert "PKG" in sources  # Package-level question for missing ANALYTICAL_METHOD
