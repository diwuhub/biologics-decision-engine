"""Tests for ctd_reviewer module — section classification, consistency checking, and checklist review."""

import sys
import os

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.ctd_reviewer import classify_sections, check_consistency, review_checklist


# ---------------------------------------------------------------------------
# Section classifier tests
# ---------------------------------------------------------------------------

class TestSectionClassifier:

    def test_section_classifier_recognizes_drug_substance(self):
        """S.2.2-style text about manufacturing process should classify correctly."""
        text = (
            "S.2.2 Description of Manufacturing Process\n"
            "The manufacturing process for the drug substance involves cell culture "
            "in a 2000 L bioreactor using a fed-batch process. Harvest is performed "
            "by depth filtration followed by Protein A chromatography. Viral "
            "inactivation is achieved by low pH hold. Additional purification steps "
            "include cation exchange chromatography and nanofiltration. "
            "In-process controls are applied at each unit operation."
        )
        results = classify_sections(text)
        assert len(results) >= 1
        best = results[0]
        assert best["section_id"] == "S.2.2"
        assert best["confidence"]["score"] is not None
        assert best["confidence"]["score"] >= 0.50
        assert best["section_heading"] == "Description of Manufacturing Process and Process Controls"

    def test_section_classifier_recognizes_stability(self):
        """Stability data text should map to S.7.x."""
        text = (
            "S.7.3 Stability Data\n"
            "Long-term stability data at 5 +/- 3 degrees C and accelerated "
            "stability data at 25 degrees C / 60% RH are presented. Time points "
            "of 0, 3, 6, 12, and 24 months were tested. The stability table "
            "shows all results within acceptance criteria."
        )
        results = classify_sections(text)
        assert len(results) >= 1
        assert results[0]["section_id"].startswith("S.7")

    def test_section_classifier_recognizes_drug_product(self):
        """P.1-style composition text should classify to P.1."""
        text = (
            "P.1 Description and Composition\n"
            "The drug product is a solution for injection containing 50 mg/mL "
            "of the active substance formulated with histidine buffer, sucrose, "
            "polysorbate 80, and water for injection. The dosage form is supplied "
            "in a pre-filled syringe."
        )
        results = classify_sections(text)
        assert len(results) >= 1
        assert results[0]["section_id"] == "P.1"

    def test_section_classifier_empty_input(self):
        """Empty input should return empty list."""
        assert classify_sections("") == []
        assert classify_sections("   ") == []

    def test_section_classifier_returns_content_preview(self):
        """Each classification should include a content preview."""
        text = (
            "The specification table lists acceptance criteria for identity, "
            "purity, potency, and safety tests. Release specification requires "
            "NMT 5% aggregates. The shelf-life specification allows NMT 8%."
        )
        results = classify_sections(text)
        assert len(results) >= 1
        assert "content_preview" in results[0]
        assert len(results[0]["content_preview"]) <= 300


# ---------------------------------------------------------------------------
# Consistency checker tests
# ---------------------------------------------------------------------------

class TestConsistencyChecker:

    def test_consistency_checker_finds_conflict(self):
        """Contradictory pH values across sections should be flagged."""
        sections = [
            ("S.2.2", (
                "The Protein A elution step uses a buffer at pH 3.5. "
                "The elution pool is then adjusted to neutral pH before "
                "the next chromatography step in the purification process."
            )),
            ("S.2.4", (
                "The critical process parameter for Protein A elution is "
                "pH 3.0. The elution pool must be neutralized before "
                "proceeding to the next purification step."
            )),
        ]
        result = check_consistency(sections)
        assert result["consistency_status"] == "findings_present"
        assert len(result["consistency_flags"]) >= 1
        # Check that the conflict has the expected schema fields
        flag = result["consistency_flags"][0]
        assert "severity" in flag
        assert "section_a" in flag
        assert "section_b" in flag
        assert "value_a" in flag
        assert "value_b" in flag

    def test_consistency_checker_no_conflict(self):
        """Consistent values across sections should produce a pass."""
        sections = [
            ("S.2.2", (
                "The drug substance is stored at 2-8 degrees C in "
                "stainless steel containers. The protein concentration "
                "is maintained at 25 mg/mL throughout the process."
            )),
            ("S.6", (
                "The container closure system consists of stainless steel "
                "vessels stored at 2-8 degrees C. The drug substance "
                "protein concentration target is 25 mg/mL."
            )),
        ]
        result = check_consistency(sections)
        assert result["consistency_status"] == "pass"
        assert len(result["consistency_flags"]) == 0
        assert result["confidence"]["score"] is not None

    def test_consistency_checker_insufficient_data(self):
        """Fewer than 2 sections should return insufficient_data."""
        result = check_consistency([("S.2.2", "Some text about manufacturing.")])
        assert result["consistency_status"] == "insufficient_data"

    def test_consistency_checker_accepts_dicts(self):
        """Should accept classification-style dicts too."""
        sections = [
            {
                "classification_id": "cls-001",
                "section_id": "S.2.2",
                "content_full": "The bioreactor temperature is 37 °C for cell culture.",
            },
            {
                "classification_id": "cls-002",
                "section_id": "S.2.4",
                "content_full": "The bioreactor temperature is set to 36 °C for cell culture.",
            },
        ]
        result = check_consistency(sections)
        assert result["consistency_status"] == "findings_present"

    def test_consistency_checker_numerical_extraction(self):
        """Values with units should be extracted and compared."""
        sections = [
            ("S.2.2", "The protein concentration at harvest is 5.0 g/L in the bioreactor."),
            ("S.4.4", "Batch analysis showed the protein concentration at harvest was 8.0 g/L in the bioreactor."),
        ]
        result = check_consistency(sections)
        assert result["consistency_status"] == "findings_present"
        flags = result["consistency_flags"]
        assert any("numerical" in f.get("category", "") or "value" in f.get("category", "") for f in flags)


# ---------------------------------------------------------------------------
# Checklist reviewer tests
# ---------------------------------------------------------------------------

class TestChecklistReviewer:

    def test_checklist_basic_items(self):
        """Checklist should detect present and missing items for S.2.2 content."""
        # Classify some text first, then check the checklist
        text = (
            "S.2.2 Description of Manufacturing Process\n"
            "The manufacturing process uses a fed-batch cell culture in a "
            "2000 L bioreactor. Harvest is by depth filtration. Purification "
            "includes Protein A chromatography and cation exchange chromatography. "
            "Viral inactivation is by low pH hold followed by nanofiltration for "
            "viral clearance. In-process controls include pH, temperature, and "
            "conductivity monitoring at each unit operation."
        )
        classifications = classify_sections(text)
        assert len(classifications) >= 1

        report = review_checklist(classifications)
        assert report["sections_analyzed"] >= 1
        assert "section_results" in report
        assert "compliance_score" in report

        # Find the S.2.2 section result
        s22_results = [
            sr for sr in report["section_results"]
            if sr["section_id"] == "S.2.2"
        ]
        assert len(s22_results) == 1
        s22 = s22_results[0]
        assert s22["items_present"] >= 3  # should find several items
        assert s22["items_checked"] == 7

    def test_checklist_empty_input(self):
        """Empty classifications should return zero-state report."""
        report = review_checklist([])
        assert report["sections_analyzed"] == 0
        assert report["compliance_score"] == 0

    def test_checklist_detects_critical_missing(self):
        """Missing critical items should be counted."""
        # Classify text that covers S.4.1 but is missing acceptance criteria
        classifications = [
            {
                "classification_id": "cls-001",
                "section_id": "S.4.1",
                "content_full": "The specification table is provided.",
                "confidence": {"score": 0.8, "qualifier": "high", "basis": "test"},
            }
        ]
        report = review_checklist(classifications)
        # Should detect at least one missing item
        s41 = [sr for sr in report["section_results"] if sr["section_id"] == "S.4.1"]
        assert len(s41) == 1
        assert s41[0]["items_missing"] >= 1

    def test_checklist_compliance_score_range(self):
        """Compliance score should be between 0 and 100."""
        classifications = classify_sections(
            "S.1.1 Nomenclature\n"
            "The INN is examplezumab. The USAN is the same. "
            "CAS registry number 12345-67-8. The compendial name is listed."
        )
        report = review_checklist(classifications)
        assert 0 <= report["compliance_score"] <= 100
