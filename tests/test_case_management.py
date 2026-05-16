"""
Tests for the Phase A Case Management endpoints.

Covers:
- POST /api/cases (create case)
- GET /api/cases (list cases)
- GET /api/cases/{id}/overview (package overview)
- GET /api/cases/{id}/attributes/{name} (attribute deep dive)
- GET /api/cases/{id}/gaps (gap inventory)
- GET /api/cases/{id}/provenance/{id} (provenance detail)
- POST /api/cases/{id}/export (export)
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient
from api.main import app
from api.models import get_case_store

client = TestClient(app)


# =========================================================================
# Fixtures
# =========================================================================

SAMPLE_BATCH_DATA = {
    "product_name": "mAb-X",
    "molecule_class": "mAb",
    "modality": "IV",
    "attributes": [
        {
            "name": "SEC Monomer",
            "category": "purity",
            "pre_value": 98.5,
            "post_value": 98.3,
            "unit": "%",
            "n_lots": 5,
            "cv_pct": 1.0,
            "n_methods": 1,
            "functional_support_level": "none",
            "orthogonal_coverage": "none",
        },
        {
            "name": "Potency",
            "category": "potency",
            "pre_value": 100.0,
            "post_value": 99.0,
            "unit": "%",
            "n_lots": 5,
            "cv_pct": 5.0,
            "n_methods": 2,
            "functional_support_level": "direct",
            "orthogonal_coverage": "partial",
        },
    ],
}


@pytest.fixture(autouse=True)
def clean_store():
    """Reset the in-memory case store before each test."""
    store = get_case_store()
    store.cases.clear()
    yield
    store.cases.clear()


def _create_case() -> str:
    """Helper: create a case and return case_id."""
    payload = {
        "product_name": "mAb-X",
        "product_type": "mAb",
        "molecule_class": "mAb",
        "change_type": "formulation",
        "product_stage": "commercial",
        "batch_data": SAMPLE_BATCH_DATA,
        "change_description": "Formulation buffer change",
    }
    r = client.post("/api/cases", json=payload)
    assert r.status_code == 200, f"Create case failed: {r.text}"
    return r.json()["case_id"]


# =========================================================================
# POST /api/cases
# =========================================================================

def test_create_case():
    """Creating a case returns case_id and data_loaded status."""
    payload = {
        "product_name": "mAb-X",
        "product_type": "mAb",
        "molecule_class": "mAb",
        "change_type": "formulation",
        "product_stage": "commercial",
        "batch_data": SAMPLE_BATCH_DATA,
    }
    r = client.post("/api/cases", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert "case_id" in body
    assert body["status"] == "data_loaded"
    assert body["batch_count"] == 2
    assert "created_at" in body


def test_create_case_validation_warnings():
    """Creating a case with missing optional fields produces warnings."""
    payload = {
        "product_name": "mAb-Y",
        "batch_data": {
            "attributes": [
                {
                    "name": "pH",
                    "category": "physicochemical",
                    "pre_value": 6.0,
                    "post_value": 6.1,
                },
            ],
        },
    }
    r = client.post("/api/cases", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["case_id"]
    # Should have validation warnings for missing molecule_class, modality, etc.
    assert "validation_warnings" in body


# =========================================================================
# GET /api/cases
# =========================================================================

def test_list_cases_empty():
    """Empty store returns empty list."""
    r = client.get("/api/cases")
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 0
    assert body["cases"] == []


def test_list_cases_after_create():
    """After creating a case, list returns it."""
    case_id = _create_case()

    r = client.get("/api/cases")
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 1
    case_summary = body["cases"][0]
    assert case_summary["case_id"] == case_id
    assert case_summary["product_name"] == "mAb-X"
    assert case_summary["molecule_class"] == "mAb"
    assert case_summary["status"] == "data_loaded"


# =========================================================================
# GET /api/cases/{id}/overview
# =========================================================================

def test_overview_runs_pipeline():
    """Overview endpoint runs comparability pipeline and returns 5 blocks."""
    case_id = _create_case()

    r = client.get(f"/api/cases/{case_id}/overview")
    assert r.status_code == 200
    body = r.json()

    assert body["case_id"] == case_id

    # Block 1: Judgment Summary
    js = body["judgment_summary"]
    assert "verdict" in js
    assert js["verdict"] in ("Comparable", "Not Comparable", "Insufficient Evidence",
                              "Comparable With Caveats")
    assert 0 <= js["confidence"] <= 1
    assert js["overall_action"]
    assert js["key_finding"]

    # Block 2: Top Gaps
    assert "top_gaps" in body
    assert isinstance(body["top_gaps"], list)

    # Block 3: Critical Attributes
    assert "critical_attributes" in body
    assert isinstance(body["critical_attributes"], list)
    assert len(body["critical_attributes"]) == 2  # We sent 2 attributes

    for attr in body["critical_attributes"]:
        assert "name" in attr
        assert "score" in attr
        assert "is_cqa" in attr
        assert "action" in attr

    # Block 4: Reviewer Risk
    assert "reviewer_risk" in body
    assert "predicted_questions" in body["reviewer_risk"]

    # Block 5: Provenance Snapshot
    assert "provenance_snapshot" in body
    ps = body["provenance_snapshot"]
    assert "sources_count" in ps


def test_overview_not_found():
    """Overview for nonexistent case returns 404."""
    r = client.get("/api/cases/nonexistent/overview")
    assert r.status_code == 404


def test_overview_caches_report():
    """Second overview call reuses cached comparability report."""
    case_id = _create_case()

    r1 = client.get(f"/api/cases/{case_id}/overview")
    assert r1.status_code == 200

    r2 = client.get(f"/api/cases/{case_id}/overview")
    assert r2.status_code == 200

    # Should return same verdict
    assert r1.json()["judgment_summary"]["verdict"] == r2.json()["judgment_summary"]["verdict"]


# =========================================================================
# GET /api/cases/{id}/attributes/{name}
# =========================================================================

def test_attribute_deep_dive():
    """Attribute deep dive returns full reasoning card."""
    case_id = _create_case()

    # First trigger overview to run the pipeline
    client.get(f"/api/cases/{case_id}/overview")

    r = client.get(f"/api/cases/{case_id}/attributes/SEC Monomer")
    assert r.status_code == 200
    body = r.json()

    assert body["case_id"] == case_id
    assert body["attribute_name"] == "SEC Monomer"
    assert body["category"] == "purity"
    assert "score" in body
    assert "uncertainty" in body
    assert "is_cqa" in body
    assert "reasoning" in body
    assert "action" in body


def test_attribute_not_found():
    """Nonexistent attribute returns 404."""
    case_id = _create_case()
    client.get(f"/api/cases/{case_id}/overview")

    r = client.get(f"/api/cases/{case_id}/attributes/NonExistent")
    assert r.status_code == 404


# =========================================================================
# GET /api/cases/{id}/gaps
# =========================================================================

def test_gaps_endpoint():
    """Gaps endpoint returns structured gap inventory."""
    case_id = _create_case()
    client.get(f"/api/cases/{case_id}/overview")

    r = client.get(f"/api/cases/{case_id}/gaps")
    assert r.status_code == 200
    body = r.json()

    assert body["case_id"] == case_id
    assert "total_gaps" in body
    assert "critical_count" in body
    assert "high_count" in body
    assert isinstance(body["gaps"], list)

    # Gaps should be sorted by severity
    if len(body["gaps"]) > 1:
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        for i in range(len(body["gaps"]) - 1):
            s1 = severity_order.get(body["gaps"][i]["severity"], 4)
            s2 = severity_order.get(body["gaps"][i + 1]["severity"], 4)
            assert s1 <= s2, "Gaps not sorted by severity"


def test_gaps_no_assessment():
    """Gaps before assessment returns 404."""
    case_id = _create_case()
    r = client.get(f"/api/cases/{case_id}/gaps")
    assert r.status_code == 404


# =========================================================================
# GET /api/cases/{id}/provenance/{id}
# =========================================================================

def test_provenance_placeholder():
    """Provenance endpoint returns placeholder data."""
    case_id = _create_case()

    r = client.get(f"/api/cases/{case_id}/provenance/rec_001")
    assert r.status_code == 200
    body = r.json()
    assert body["case_id"] == case_id
    assert body["record_id"] == "rec_001"
    assert "source_type" in body
    assert "full_citation" in body


def test_provenance_case_not_found():
    """Provenance for nonexistent case returns 404."""
    r = client.get("/api/cases/nonexistent/provenance/rec_001")
    assert r.status_code == 404


# =========================================================================
# POST /api/cases/{id}/export
# =========================================================================

def test_export_case():
    """Export endpoint returns export metadata."""
    case_id = _create_case()

    r = client.post(f"/api/cases/{case_id}/export", json={"format": "json"})
    assert r.status_code == 200
    body = r.json()
    assert body["case_id"] == case_id
    assert body["format"] == "json"
    assert body["filename"].endswith(".json")
    assert "download_url" in body


def test_export_case_not_found():
    """Export for nonexistent case returns 404."""
    r = client.post("/api/cases/nonexistent/export", json={"format": "json"})
    assert r.status_code == 404


# =========================================================================
# Data Harmonizer Integration
# =========================================================================

def test_harmonize_batch_data_function():
    """harmonize_batch_data normalizes units and fills defaults."""
    from modules.data_harmonizer import harmonize_batch_data

    raw = {
        "attributes": [
            {
                "name": "SEC Monomer",
                "pre_value": 98.5,
                "post_value": 98.3,
                "unit": "%",
            },
        ],
    }
    result = harmonize_batch_data(raw)
    assert "molecule_class" in result
    assert len(result["attributes"]) == 1
    attr = result["attributes"][0]
    assert attr["category"] == "physicochemical"  # default filled
    assert attr["n_lots"] == 3  # default filled
    assert attr["functional_support_level"] == "none"  # default filled


def test_harmonize_batch_data_legacy_field_names():
    """harmonize_batch_data maps legacy field names."""
    from modules.data_harmonizer import harmonize_batch_data

    raw = {
        "attributes": [
            {
                "attribute": "pH",  # legacy "attribute" instead of "name"
                "pre_value": 6.0,
                "post_value": 6.1,
                "unit": "",
            },
        ],
    }
    result = harmonize_batch_data(raw)
    attr = result["attributes"][0]
    assert attr["name"] == "pH"
    assert "attribute" not in attr


def test_harmonize_passthrough_non_dict():
    """harmonize_batch_data passes through non-dict input unchanged."""
    from modules.data_harmonizer import harmonize_batch_data

    assert harmonize_batch_data("not a dict") == "not a dict"
