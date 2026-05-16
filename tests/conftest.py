"""Shared pytest configuration for optional real-document benchmarks."""

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

REAL_DOCUMENT_TEST_REQUIREMENTS = {
    "tests/test_analytical_method_assessor.py::TestAnalyticalMethodAssessment::test_real_ich_q14": [
        "benchmarks/real_documents/ICH_Q14_2023.pdf",
    ],
    "tests/test_characterization_assessor.py::TestFullAssessment::test_real_nistmab": [
        "benchmarks/real_documents/NISTmAb_SP260-237.pdf",
    ],
    "tests/test_stability_assessor.py::TestStabilityAssessment::test_real_xbonzy": [
        "benchmarks/real_documents/Xbonzy_EPAR.pdf",
    ],
    "tests/test_package_assessor.py::TestPackageAssessment::test_full_package_assessed": [
        "benchmarks/real_documents/NISTmAb_SP260-237.pdf",
        "benchmarks/real_documents/Xbonzy_EPAR.pdf",
        "benchmarks/real_documents/ICH_Q14_2023.pdf",
    ],
    "tests/test_package_assessor.py::TestPackageAssessment::test_coverage_all_types": [
        "benchmarks/real_documents/NISTmAb_SP260-237.pdf",
        "benchmarks/real_documents/Xbonzy_EPAR.pdf",
        "benchmarks/real_documents/ICH_Q14_2023.pdf",
    ],
    "tests/test_package_assessor.py::TestPackageAssessment::test_single_doc_incomplete": [
        "benchmarks/real_documents/NISTmAb_SP260-237.pdf",
    ],
    "tests/test_package_assessor.py::TestPackageAssessment::test_overview_structure": [
        "benchmarks/real_documents/NISTmAb_SP260-237.pdf",
    ],
    "tests/test_package_assessor.py::TestPackageAssessment::test_reviewer_questions_aggregated": [
        "benchmarks/real_documents/NISTmAb_SP260-237.pdf",
        "benchmarks/real_documents/Xbonzy_EPAR.pdf",
    ],
}


def pytest_collection_modifyitems(config, items):
    """Skip optional real-document tests unless the git-ignored PDFs exist."""
    for item in items:
        required_docs = REAL_DOCUMENT_TEST_REQUIREMENTS.get(item.nodeid)
        if not required_docs:
            continue
        missing = [path for path in required_docs if not (PROJECT_ROOT / path).exists()]
        if missing:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "Optional real-document benchmark PDF(s) missing: "
                        f"{', '.join(missing)}. Run "
                        "`python benchmarks/real_documents/download.py` to enable."
                    )
                )
            )
