"""
Tests for modules.data_harmonizer — unit normalization, field mapping,
and template detection.
"""

import sys
import os
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.data_harmonizer.unit_normalizer import normalize, normalize_value, NormalizedUnit
from modules.data_harmonizer.field_mapper import map_field, map_fields, FieldMapping
from modules.data_harmonizer.template_detector import detect_template, TemplateMatch


# ========================================================================
# Unit Normalizer
# ========================================================================

class TestUnitConversionMgToG:
    """test_unit_conversion_mg_to_g"""

    def test_mg_ml_identity(self):
        r = normalize("5 mg/mL")
        assert r.value == 5.0
        assert r.normalized_value == 5.0
        assert r.normalized_unit == "mg/mL"
        assert r.family == "concentration"

    def test_g_l_to_mg_ml(self):
        """g/L and mg/mL are equivalent (1:1)."""
        r = normalize("10 g/L")
        assert r.normalized_value == 10.0
        assert r.normalized_unit == "mg/mL"

    def test_ug_ml_to_mg_ml(self):
        r = normalize("500 µg/mL")
        assert r.normalized_value == pytest.approx(0.5)
        assert r.normalized_unit == "mg/mL"

    def test_ng_ml_to_mg_ml(self):
        r = normalize("1000000 ng/mL")
        assert r.normalized_value == pytest.approx(1.0)
        assert r.normalized_unit == "mg/mL"

    def test_mg_l_to_mg_ml(self):
        r = normalize("100 mg/L")
        assert r.normalized_value == pytest.approx(0.1)
        assert r.normalized_unit == "mg/mL"

    def test_volume_l_to_ml(self):
        r = normalize("2 L")
        assert r.normalized_value == 2000.0
        assert r.normalized_unit == "mL"

    def test_kda_identity(self):
        r = normalize("150 kDa")
        assert r.normalized_value == 150.0
        assert r.normalized_unit == "kDa"

    def test_da_to_kda(self):
        r = normalize("150000 Da")
        assert r.normalized_value == pytest.approx(150.0)
        assert r.normalized_unit == "kDa"


class TestUnitConversionCelsiusToKelvin:
    """test_unit_conversion_celsius_to_kelvin"""

    def test_celsius_identity(self):
        r = normalize("25 °C")
        assert r.value == 25.0
        assert r.normalized_value == 25.0
        assert r.normalized_unit == "°C"
        assert r.family == "temperature"

    def test_kelvin_to_celsius(self):
        r = normalize("310 K")
        assert r.normalized_value == pytest.approx(36.85, abs=0.01)
        assert r.normalized_unit == "°C"

    def test_zero_kelvin(self):
        r = normalize("273.15 K")
        assert r.normalized_value == pytest.approx(0.0, abs=0.01)
        assert r.normalized_unit == "°C"

    def test_fahrenheit_to_celsius(self):
        r = normalize("212 °F")
        assert r.normalized_value == pytest.approx(100.0, abs=0.01)
        assert r.normalized_unit == "°C"

    def test_body_temp_fahrenheit(self):
        r = normalize("98.6 F")
        assert r.normalized_value == pytest.approx(37.0, abs=0.01)
        assert r.normalized_unit == "°C"


class TestUnitConversionTime:
    """Time conversions: min -> hours, etc."""

    def test_min_to_hours(self):
        r = normalize("90 min")
        assert r.normalized_value == pytest.approx(1.5, abs=0.01)
        assert r.normalized_unit == "hours"

    def test_hours_identity(self):
        r = normalize("2 hours")
        assert r.normalized_value == 2.0
        assert r.normalized_unit == "hours"

    def test_seconds_to_hours(self):
        r = normalize("3600 s")
        assert r.normalized_value == pytest.approx(1.0, abs=0.01)
        assert r.normalized_unit == "hours"


class TestUnitNormalizerEdgeCases:
    """Edge cases and preserved units."""

    def test_preserved_unit_eu_ml(self):
        r = normalize("0.5 EU/mL")
        assert r.normalized_value == 0.5
        assert r.normalized_unit == "EU/mL"
        assert r.family == "preserved"

    def test_preserved_unit_ph(self):
        r = normalize("7.4 pH")
        assert r.normalized_value == 7.4
        assert r.normalized_unit == "pH"

    def test_unrecognized_unit_passthrough(self):
        r = normalize("42 widgets")
        assert r.value == 42.0
        assert r.normalized_value == 42.0
        assert r.normalized_unit == "widgets"
        assert r.family is None

    def test_invalid_input_raises(self):
        with pytest.raises(ValueError):
            normalize("not a number at all")

    def test_normalize_value_convenience(self):
        r = normalize_value(500.0, "µg/mL")
        assert r.normalized_value == pytest.approx(0.5)
        assert r.normalized_unit == "mg/mL"

    def test_to_dict(self):
        r = normalize("5 mg/mL")
        d = r.to_dict()
        assert d["value"] == 5.0
        assert d["normalized_unit"] == "mg/mL"
        assert isinstance(d, dict)


# ========================================================================
# Field Mapper
# ========================================================================

class TestFieldMappingKnownSynonyms:
    """test_field_mapping_known_synonyms"""

    def test_direct_match_batch_number(self):
        r = map_field("Batch Number")
        assert r.canonical_name == "batch_id"
        assert r.confidence == 0.95
        assert r.basis == "direct"

    def test_direct_match_test_name(self):
        r = map_field("Test Name")
        assert r.canonical_name == "test_name"
        assert r.confidence == 0.95

    def test_direct_match_lot_no(self):
        r = map_field("Lot No")
        assert r.canonical_name == "batch_id"

    def test_synonym_match_assay(self):
        r = map_field("Assay")
        assert r.canonical_name == "test_name"
        assert r.confidence == 0.92
        assert r.basis == "synonym"

    def test_synonym_match_sop(self):
        r = map_field("SOP")
        assert r.canonical_name == "method_reference"
        assert r.confidence == 0.90

    def test_hic_retention_time_with_unit(self):
        r = map_field("HIC Retention Time (Min)a")
        assert r.canonical_name == "hic_rt"
        assert r.confidence >= 0.80
        assert r.embedded_unit == "Min"

    def test_sec_main_peak(self):
        r = map_field("SE-HPLC Main Peak")
        assert r.canonical_name == "sec_main_peak"

    def test_embedded_unit_extraction(self):
        r = map_field("Concentration (mg/mL)")
        assert r.embedded_unit == "mg/mL"

    def test_batch_fields_return_list(self):
        results = map_fields(["Batch", "Test", "Result", "Unit"])
        assert len(results) == 4
        names = [r.canonical_name for r in results]
        assert "batch_id" in names
        assert "test_name" in names
        assert "result_value" in names
        assert "result_unit" in names

    def test_to_dict(self):
        r = map_field("Batch Number")
        d = r.to_dict()
        assert d["canonical_name"] == "batch_id"
        assert isinstance(d, dict)


class TestFieldMappingUnknownReturnsLowConfidence:
    """test_field_mapping_unknown_returns_low_confidence"""

    def test_completely_unknown_field(self):
        r = map_field("xyzzy_gobbledygook")
        assert r.canonical_name is None
        assert r.confidence == 0.0
        assert r.qualifier == "unknown"
        assert r.basis == "unmapped"

    def test_structural_artifact_ignored(self):
        r = map_field("#")
        assert r.canonical_name is None
        assert r.basis == "structural"

    def test_row_number_ignored(self):
        r = map_field("Row Number")
        assert r.canonical_name is None

    def test_low_confidence_fuzzy(self):
        """A partial match should produce a low-confidence result."""
        r = map_field("analytical something weird")
        # Either unmapped or low-confidence fuzzy
        if r.canonical_name is not None:
            assert r.confidence < 0.80
            assert r.qualifier in ("low", "medium")
        else:
            assert r.confidence == 0.0


# ========================================================================
# Template Detection
# ========================================================================

class TestTemplateDetection:
    """test_template_detection"""

    def test_characterization_table(self):
        csv = (
            "Test,Method,Acceptance Criteria,Result,Unit,Batch,Comments,Pass/Fail\n"
            "SEC Purity,SEC-HPLC,>= 95%,97.2,%,LOT-001,Within spec,PASS\n"
            "Potency,Cell-Based,80-120%,105,% Relative,LOT-001,,PASS\n"
        )
        r = detect_template(csv)
        assert r.template_id == "char-summary-v1"
        assert r.confidence_score >= 0.50
        assert r.confidence_qualifier in ("high", "medium")

    def test_stability_table(self):
        csv = (
            "Test,Method,Spec,Initial,3M,6M,12M,24M\n"
            "SEC Purity,SEC-HPLC,>= 95%,98.1,97.8,97.2,96.5,95.1\n"
        )
        r = detect_template(csv)
        assert r.template_id == "stability-summary-v1"
        assert r.confidence_score >= 0.50

    def test_release_testing_table(self):
        csv = (
            "Test,Method,Release Specification,Batch A Result,Batch B Result\n"
            "Appearance,Visual,Clear,Clear,Clear\n"
        )
        r = detect_template(csv)
        assert r.template_id == "release-testing-v1"

    def test_empty_content(self):
        r = detect_template("")
        assert r.template_id is None
        assert r.confidence_score == 0.0

    def test_manual_override(self):
        r = detect_template("anything", template_override="stability-summary-v1")
        assert r.template_id == "stability-summary-v1"
        assert r.confidence_score == 1.0

    def test_unrecognizable_content(self):
        r = detect_template("foo,bar,baz\n1,2,3\n")
        assert r.confidence_score < 0.50

    def test_to_dict(self):
        r = detect_template("")
        d = r.to_dict()
        assert isinstance(d, dict)
        assert "template_id" in d
