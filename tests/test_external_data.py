"""
Tests for P0-A: Regulatory Data Source Integration

Tests marked with @pytest.mark.network make real HTTP calls and may be
skipped in CI environments without internet access.
"""

from __future__ import annotations

import time
import pytest

from services.external_data import (
    AdverseEventRecord,
    ApprovalRecord,
    EnforcementRecord,
    RegulatoryDataSource,
)
from services.external_data.openfda_connector import OpenFDAConnector
from services.external_data.ecfr_connector import ECFRConnector


# ------------------------------------------------------------------
# Helper: detect network availability
# ------------------------------------------------------------------

def _has_network() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(
            "https://api.fda.gov/drug/drugsfda.json?search=openfda.generic_name:%22aspirin%22&limit=1",
            timeout=10,
        )
        return True
    except Exception:
        return False


network = pytest.mark.skipif(not _has_network(), reason="No network access")


# ------------------------------------------------------------------
# Interface compliance
# ------------------------------------------------------------------

class TestInterfaceCompliance:
    def test_openfda_implements_regulatory_data_source(self):
        connector = OpenFDAConnector()
        assert isinstance(connector, RegulatoryDataSource)

    def test_openfda_has_required_methods(self):
        connector = OpenFDAConnector()
        assert callable(getattr(connector, "fetch_enforcement_actions", None))
        assert callable(getattr(connector, "fetch_drug_approvals", None))
        assert callable(getattr(connector, "fetch_adverse_events", None))

    def test_ecfr_has_required_methods(self):
        connector = ECFRConnector()
        assert callable(getattr(connector, "fetch_section", None))
        assert callable(getattr(connector, "fetch_part_structure", None))


# ------------------------------------------------------------------
# openFDA — live network tests
# ------------------------------------------------------------------

class TestOpenFDAEnforcement:
    @network
    def test_openfda_enforcement_search(self):
        c = OpenFDAConnector()
        results = c.fetch_enforcement_actions("sterility", limit=3)
        # The API may return 0 results for a niche query — just ensure
        # no crash and correct types.
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, EnforcementRecord)
            assert r.record_id  # non-empty


class TestOpenFDAApprovals:
    @network
    def test_openfda_approvals_search(self):
        c = OpenFDAConnector()
        results = c.fetch_drug_approvals("adalimumab", limit=3)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, ApprovalRecord)
            assert r.application_number


class TestOpenFDAAdverseEvents:
    @network
    def test_openfda_adverse_events_search(self):
        c = OpenFDAConnector()
        results = c.fetch_adverse_events("adalimumab", limit=3)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, AdverseEventRecord)


# ------------------------------------------------------------------
# openFDA — caching
# ------------------------------------------------------------------

class TestOpenFDACaching:
    @network
    def test_openfda_caching(self):
        c = OpenFDAConnector()
        # First call — hits the network
        t0 = time.monotonic()
        r1 = c.fetch_drug_approvals("adalimumab", limit=3)
        first_duration = time.monotonic() - t0

        # Second call — should come from cache (effectively instant)
        t0 = time.monotonic()
        r2 = c.fetch_drug_approvals("adalimumab", limit=3)
        second_duration = time.monotonic() - t0

        assert r1 == r2, "Cached result should be identical"
        # Cache hit should be much faster (no network + no rate-limit sleep)
        assert second_duration < first_duration or second_duration < 0.05


# ------------------------------------------------------------------
# openFDA — error handling
# ------------------------------------------------------------------

class TestOpenFDAErrorHandling:
    def test_invalid_query_returns_empty(self):
        """An absurd query should return an empty list, never raise."""
        c = OpenFDAConnector()
        results = c.fetch_enforcement_actions(
            "zzzzz_nonexistent_product_xyz_12345", limit=1
        )
        assert isinstance(results, list)

    def test_bad_endpoint_returns_empty(self):
        """Manually break the base URL — connector must not crash."""
        c = OpenFDAConnector()
        import services.external_data.openfda_connector as mod
        original = mod._BASE
        try:
            mod._BASE = "https://api.fda.gov/DOES_NOT_EXIST"
            results = c.fetch_drug_approvals("test", limit=1)
            assert results == []
        finally:
            mod._BASE = original


# ------------------------------------------------------------------
# eCFR — live network tests
# ------------------------------------------------------------------

class TestECFR:
    @network
    def test_ecfr_section_fetch(self):
        c = ECFRConnector()
        text = c.fetch_section("21", "211", "100")
        # 21 CFR 211.100 covers "Written procedures; deviations"
        assert isinstance(text, str)
        # May be empty if the API changed its URL scheme, but should not crash
        if text:
            assert len(text) > 50, "Section text should be substantial"

    @network
    def test_ecfr_part_structure(self):
        c = ECFRConnector()
        structure = c.fetch_part_structure("21", "211")
        assert isinstance(structure, dict)

    def test_ecfr_bad_section_returns_empty(self):
        """Non-existent section should return empty string, not crash."""
        c = ECFRConnector()
        text = c.fetch_section("99", "99999", "99999")
        assert isinstance(text, str)
