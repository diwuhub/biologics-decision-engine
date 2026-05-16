"""
Tests for the Biologics Decision Engine REST API (v1).
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


# =========================================================================
# /health
# =========================================================================

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


# =========================================================================
# /api/v1/comparability
# =========================================================================

def test_comparability_minimal():
    """A minimal two-attribute comparability request returns a valid report."""
    payload = {
        "product_name": "mAb-Test",
        "change_description": "Cell culture media change",
        "attributes": [
            {
                "name": "SEC Monomer",
                "category": "purity",
                "pre_value": 98.5,
                "post_value": 98.3,
                "unit": "%",
                "n_lots": 5,
                "cv_pct": 1.0,
            },
            {
                "name": "Potency",
                "category": "potency",
                "pre_value": 100.0,
                "post_value": 99.0,
                "unit": "%",
                "n_lots": 5,
                "cv_pct": 5.0,
            },
        ],
    }
    r = client.post("/api/v1/comparability", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["product_name"] == "mAb-Test"
    assert body["overall_verdict"] in ("Comparable", "Comparable With Caveats", "Not Comparable", "Insufficient Evidence")
    assert body["n_attributes"] == 2
    assert len(body["attribute_results"]) == 2
    assert 0 <= body["evidence_strength_index"] <= 1
    assert body["timestamp"]  # non-empty


def test_comparability_empty_attributes():
    """Empty attribute list returns Insufficient Evidence."""
    payload = {
        "product_name": "Empty",
        "attributes": [],
    }
    r = client.post("/api/v1/comparability", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["overall_verdict"] == "Insufficient Evidence"
    assert body["n_attributes"] == 0


def test_comparability_single_attribute():
    """A single attribute request works correctly."""
    payload = {
        "attributes": [
            {
                "name": "pH",
                "category": "physicochemical",
                "pre_value": 6.0,
                "post_value": 6.0,
                "unit": "",
            },
        ],
    }
    r = client.post("/api/v1/comparability", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["n_attributes"] == 1
    assert body["attribute_results"][0]["name"] == "pH"


def test_comparability_validation_error():
    """Missing required fields returns 422."""
    # 'attributes' is required
    r = client.post("/api/v1/comparability", json={"product_name": "Bad"})
    assert r.status_code == 422


# =========================================================================
# /api/v1/gap-memo
# =========================================================================

def test_grade_evidence():
    """Evidence grading returns a valid grade and probabilities."""
    payload = {
        "claim": "Phase 3 randomized controlled trial with 500 patients demonstrated statistically significant improvement",
    }
    r = client.post("/api/v1/grade-evidence", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["grade"] in ("strong", "moderate", "weak", "anecdotal")
    assert "probabilities" in body
    assert len(body["probabilities"]) == 4  # four grade categories


def test_grade_evidence_anecdotal():
    """An obviously anecdotal claim is graded appropriately."""
    payload = {
        "claim": "CEO interview on CNBC discussing expected results without sharing any clinical data",
    }
    r = client.post("/api/v1/grade-evidence", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["grade"] in ("anecdotal", "weak")  # should be anecdotal, weak is acceptable


# =========================================================================
# /api/v1/benchmarks
# =========================================================================

def test_list_benchmarks():
    """Benchmark listing returns available cases."""
    r = client.get("/api/v1/benchmarks")
    assert r.status_code == 200
    body = r.json()
    assert "benchmarks" in body
    assert "count" in body
    assert body["count"] >= 1
    # Each benchmark should have case_id and file
    for bm in body["benchmarks"]:
        assert "case_id" in bm
        assert "file" in bm
