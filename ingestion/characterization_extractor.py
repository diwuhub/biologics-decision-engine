"""
B1: Characterization Extractor (Phase 2 Track B).

Extracts physicochemical and biological properties per ICH Q6B from
characterization reports. Detects required Q6B sections, extracts key
numeric values (HMW%, potency, afucosylation, main charge peak),
and generates reviewer concern predictions.

Contract:
- extract_attributes() MUST NOT raise unhandled exceptions.
- extract_evidence() MUST NOT raise unhandled exceptions.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from statistics import mean as _mean
from typing import Any, Dict, List, Optional

from ingestion.base_extractor import BaseExtractor
from ingestion.table_interpreter import (
    _CQA_KEYWORDS, _looks_like_attribute, interpret_table,
    derive_inverse_attributes, INVERSE_ATTRIBUTES, classify_lot_header,
)
from specs.cross_document_bridge import ExtractedAttribute

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# B2: Three-State Evidence Value Model
# ---------------------------------------------------------------------------

PRESENT = "present"
CONFIRMED_ABSENT = "confirmed_absent"
UNCERTAIN = "uncertain"


@dataclass
class EvidenceValue:
    """Three-state value model: present / confirmed_absent / uncertain.

    A professional tool never confuses 'data is absent' with 'I could not
    read the data'. This dataclass enforces that distinction.
    """
    state: str = UNCERTAIN  # present | confirmed_absent | uncertain
    value: Optional[float] = None  # populated only when state == present
    source_anchor: Optional[str] = None  # page/table reference
    extraction_confidence: float = 0.0
    uncertainty_reason: Optional[str] = None  # e.g. 'pivoted_table_not_parsed'

    @staticmethod
    def present(value: float, confidence: float = 0.8, anchor: Optional[str] = None) -> "EvidenceValue":
        return EvidenceValue(state=PRESENT, value=value, extraction_confidence=confidence, source_anchor=anchor)

    @staticmethod
    def absent() -> "EvidenceValue":
        return EvidenceValue(state=CONFIRMED_ABSENT, extraction_confidence=1.0)

    @staticmethod
    def uncertain_value(reason: str, confidence: float = 0.3) -> "EvidenceValue":
        return EvidenceValue(state=UNCERTAIN, extraction_confidence=confidence, uncertainty_reason=reason)


# ---------------------------------------------------------------------------
# B2: CharacterizationEvidence dataclass
# ---------------------------------------------------------------------------

@dataclass
class CharacterizationEvidence:
    """Evidence payload from a characterization report."""
    sections_found: List[str] = field(default_factory=list)
    sections_missing: List[str] = field(default_factory=list)
    sections_uncertain: List[str] = field(default_factory=list)
    completeness_score: float = 0.0  # (present + 0.5*uncertain) / required
    reference_standard_identified: bool = False
    reference_standard_lot: str = ""
    # Three-state CQA fields
    hmw: EvidenceValue = field(default_factory=EvidenceValue)
    main_charge_peak: EvidenceValue = field(default_factory=EvidenceValue)
    afucosylation: EvidenceValue = field(default_factory=EvidenceValue)
    relative_potency: EvidenceValue = field(default_factory=EvidenceValue)
    # Legacy convenience accessors (populated from EvidenceValue for backward compat)
    hmw_pct: Optional[float] = None
    main_charge_peak_pct: Optional[float] = None
    afucosylation_pct: Optional[float] = None
    potency_relative_pct: Optional[float] = None
    acidic_variants_pct: Optional[float] = None
    basic_variants_pct: Optional[float] = None
    # Gaps — only from CONFIRMED_ABSENT fields
    critical_gaps: List[str] = field(default_factory=list)
    extraction_uncertainties: List[str] = field(default_factory=list)
    reviewer_concerns: List[str] = field(default_factory=list)
    tables_found: int = 0
    extractor: str = "CharacterizationExtractor"


# ---------------------------------------------------------------------------
# ICH Q6B required sections and detection patterns
# ---------------------------------------------------------------------------

_Q6B_SECTIONS: Dict[str, Dict[str, Any]] = {
    "primary_structure": {
        "label": "Primary Structure",
        "patterns": [
            r"\bprimary\s+structure\b",
            r"\bpeptide\s+mapp?ing\b",
            r"\bLC[-\s]?MS/?MS\b",
            r"\bamino\s+acid\s+sequence\b",
            r"\bEdman\b",
            r"\bN[-\s]?terminal\b",
            r"\bC[-\s]?terminal\b",
            r"\bmass\s+spectrom",
        ],
        "methods": ["peptide mapping", "LC-MS/MS", "Edman degradation"],
    },
    "higher_order_structure": {
        "label": "Higher-Order Structure",
        "patterns": [
            r"\bhigher[-\s]?order\s+structure\b",
            r"\bHOS\b",
            r"\bcircular\s+dichroism\b",
            r"\b(?:far|near)[-\s]?UV\s+CD\b",
            r"\bCD\s+spectroscopy\b",
            r"\bDSC\b",
            r"\bdifferential\s+scanning\s+calorimetry\b",
            r"\bFTIR\b",
            r"\bsecondary\s+structure\b",
            r"\btertiary\s+structure\b",
        ],
        "methods": ["CD", "DSC", "FTIR"],
    },
    "aggregation_size_variants": {
        "label": "Aggregation / Size Variants",
        "patterns": [
            r"\baggregat",
            r"\bsize\s+variant",
            r"\bSEC[-\s]?HPLC\b",
            r"\bSE[-\s]?HPLC\b",
            r"\bsize[-\s]?exclusion\b",
            r"\bAUC\b",
            r"\banalytical\s+ultracentrifugation\b",
            r"\bDLS\b",
            r"\bdynamic\s+light\s+scattering\b",
            r"\bHMW\b",
            r"\bhigh\s+molecular\s+weight\b",
            r"\bmonomer\b",
            r"\bfragment",
        ],
        "methods": ["SEC-HPLC", "AUC", "DLS"],
    },
    "charge_heterogeneity": {
        "label": "Charge Heterogeneity",
        "patterns": [
            r"\bcharge\s+heterogeneity\b",
            r"\bcharge\s+variant",
            r"\bCEX\b",
            r"\bcation\s+exchange\b",
            r"\bicIEF\b",
            r"\bisoelectric\s+focusing\b",
            r"\bacidic\s+(?:peak|variant|species)\b",
            r"\bbasic\s+(?:peak|variant|species)\b",
            r"\bmain\s+(?:peak|charge)\b",
        ],
        "methods": ["CEX", "icIEF"],
    },
    "glycosylation_ptms": {
        "label": "Glycosylation / PTMs",
        "patterns": [
            r"\bglycos(?:yl|an)",
            r"\bN[-\s]?glycan\b",
            r"\bHILIC[-\s]?MS\b",
            r"\bpost[-\s]?translational\s+modification\b",
            r"\bPTM\b",
            r"\bafucos(?:yl|e)\b",
            r"\bgalactos(?:yl|e)\b",
            r"\bsialylat",
            r"\bman(?:nos[ey]|5)\b",
            r"\bfucosylat",
            r"\bGlcNAc\b",
            r"\bG0F?\b",
            r"\bG1F?\b",
            r"\bG2F?\b",
        ],
        "methods": ["N-glycan profiling", "HILIC-MS"],
    },
    "biological_activity_potency": {
        "label": "Biological Activity / Potency",
        "patterns": [
            r"\bbiological\s+activity\b",
            r"\bpotency\b",
            r"\bcell[-\s]?based\s+assay\b",
            r"\brelative\s+potency\b",
            r"\bEC50\b",
            r"\bIC50\b",
            r"\bbioassay\b",
            r"\bADCC\b",
            r"\bCDC\b",
            r"\bproliferation\s+assay\b",
            r"\breporter\s+gene\s+assay\b",
        ],
        "methods": ["cell-based assay", "ADCC", "CDC"],
    },
    "immunochemical_properties": {
        "label": "Immunochemical Properties",
        "patterns": [
            r"\bimmunochemical\b",
            r"\bELISA\b",
            r"\bSPR\b",
            r"\bsurface\s+plasmon\s+resonance\b",
            r"\bbinding\s+affinity\b",
            r"\bKd\b",
            r"\bkon\b",
            r"\bkoff\b",
            r"\bBiacore\b",
            r"\bantigen\s+binding\b",
            r"\bFc\s*(?:gamma|[γg])?\s*R(?:IIIa|eceptor)\b",
            r"\bFcRn\b",
        ],
        "methods": ["ELISA", "SPR", "Biacore"],
    },
    "purity_impurities": {
        "label": "Purity / Impurities",
        "patterns": [
            r"\bpurity\b",
            r"\bimpurit",
            r"\brCE[-\s]?SDS\b",
            r"\bCE[-\s]?SDS\b",
            r"\bRP[-\s]?HPLC\b",
            r"\breversed?[-\s]?phase\b",
            r"\breduced\b.*\bCE[-\s]?SDS\b",
            r"\bnon[-\s]?reduced\b.*\bCE[-\s]?SDS\b",
            r"\bhost\s+cell\s+protein\b",
            r"\bHCP\b",
            r"\bresidual\s+DNA\b",
            r"\bprotein\s+A\b",
        ],
        "methods": ["rCE-SDS", "RP-HPLC", "CE-SDS"],
    },
}

# Numeric value extraction patterns
# Note: patterns use [^:\n]{0,40} to limit match distance (avoid cross-paragraph grabs)
_HMW_PATTERNS = [
    r"\bHMW[^:\n]{0,40}[:=]\s*([\d.]+)\s*%",
    r"\bhigh\s+molecular\s+weight[^:\n]{0,40}[:=]\s*([\d.]+)\s*%",
    r"\baggregat(?:e|es|ion)\s*[:=]\s*([\d.]+)\s*%",
    r"\bHMW\s*\(?\s*(?:aggregat\w*)?\s*\)?\s*([\d.]+)\s*%",
    # Real-world patterns: "HMW RA" (relative area), table-style
    r"\bHMW\s+(?:RA|relative\s+area)\s*[:=]?\s*([\d.]+)",
    r"\bHMW\b[^.\n]{0,60}?([\d.]+)\s*%",
    r"(?:aggregat\w*|HMW)\s*(?:content|level|percentage)?\s*(?:of|was|is)?\s*([\d.]+)\s*%",
]

_POTENCY_PATTERNS = [
    r"\bpotency\s*[:=]\s*([\d.]+)\s*%",
    r"\brelative\s+potency\s*[:=]\s*([\d.]+)",
    r"\bpotency\s*(?:\(?\s*%?\s*RP\s*\)?)?\s*[:=]\s*([\d.]+)",
    r"\brelative\s+potency\s*[:=]?\s*\b([\d]{2,}(?:\.[\d]+)?)\s*%",
]

_AFUCOSYLATION_PATTERNS = [
    r"\bafucos(?:yl(?:ation|ated)?)?\s*[:=]\s*([\d.]+)\s*%",
    r"\bafucos(?:yl(?:ation|ated)?)\s*[:=]?\s*([\d.]+)\s*%",
    r"\bnon[-\s]?fucosylated?\s*[:=]?\s*([\d.]+)\s*%",
    r"\bafucos\w*\s*(?:content|level)?\s*(?:of|was|is)?\s*([\d.]+)\s*%",
]

_MAIN_CHARGE_PATTERNS = [
    r"\bmain\s+(?:peak|charge)\s*[:=]\s*([\d.]+)\s*%",
    r"\bmain\s+(?:peak|charge)\s+(?:pct|percent|percentage)?\s*[:=]?\s*([\d.]+)",
    r"\bmain\s+(?:peak|charge|isoform|band|component)\s*(?:RA|relative\s+area)?\s*[:=]?\s*([\d.]+)\s*%",
    r"\bmain\s+charge\s+group\b[^.\n]{0,60}?([\d.]+)\s*%",
]

_REFERENCE_STANDARD_LOT_PATTERNS = [
    r"\breference\s+standard\s+(?:lot\s*(?:#|number)?|batch)\s*[:=]?\s*([A-Z0-9][\w\-]+)",
    r"\blot\s*(?:#|number)?\s*[:=]?\s*([A-Z0-9][\w\-]+)\s*\(?\s*reference\s+standard",
    r"\breference\s+standard\b.*?\blot\s*[:=]?\s*([A-Z0-9][\w\-]+)",
]

_REFERENCE_STANDARD_PATTERNS = [
    r"\breference\s+standard\b",
    r"\breference\s+material\b",
    r"\bRM\s*\d{3,}\b",  # NIST RM 8671
    r"\bUSP\s+reference\b",
    r"\bWHO\s+(?:international\s+)?standard\b",
    r"\bin[-\s]?house\s+reference\b",
    r"\bcertified\s+(?:value|reference)\b",
]


# ---------------------------------------------------------------------------
# Attribute name quality gate
# ---------------------------------------------------------------------------

_ATTRIBUTE_BLOCKLIST = frozenset([
    "rack", "row", "sample", "replicate", "run", "lot", "condition",
    "n/a", "na", "nd", "nt", "date", "analyst", "instrument",
    "column", "batch", "vial", "position", "injection", "sequence",
    "homogeneity uv",
])

# CQA keywords: imported from table_interpreter._CQA_KEYWORDS, plus extractor-specific extras
_ATTRIBUTE_CQA_KEYWORDS = _CQA_KEYWORDS | frozenset(["monomeric", "thioether", "glycan occupancy"])


def _is_valid_attribute_name(name: str) -> bool:
    """Attribute quality gate — delegates to table_interpreter._looks_like_attribute
    plus extra checks for "n.d." and extractor-specific CQA keywords."""
    if not name:
        return False
    clean = name.strip()
    if clean.lower() in ("n.d.", "nt"):
        return False
    if clean.lower().strip() in _ATTRIBUTE_BLOCKLIST:
        return False
    # Use shared validation, then verify extractor-specific keywords if needed
    if _looks_like_attribute(name):
        return True
    # Check extractor-specific extras not in table_interpreter's keyword set
    name_lower = clean.lower()
    return any(kw in name_lower for kw in ("monomeric", "thioether", "glycan occupancy"))


class CharacterizationExtractor(BaseExtractor):
    """Extract characterization data from parsed documents per ICH Q6B.

    Contract:
    - extract_attributes() NEVER raises.
    - extract_evidence() NEVER raises.
    """

    def extract_attributes(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Extract characterization data from parsed document."""
        try:
            return self._extract_attributes_impl(parsed_doc)
        except Exception as e:
            logger.error("CharacterizationExtractor.extract_attributes failed: %s", e)
            return []

    def extract_evidence(
        self, parsed_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assess ICH Q6B section coverage and extract key values."""
        try:
            evidence = self._extract_evidence_impl(parsed_doc)
            return self._evidence_to_dict(evidence)
        except Exception as e:
            logger.error("CharacterizationExtractor.extract_evidence failed: %s", e)
            return {"error": str(e), "extractor": "CharacterizationExtractor"}

    def supported_categories(self) -> List[str]:
        return [
            "physicochemical",
            "purity",
            "biological_activity",
            "potency",
            "identity",
            "glycosylation",
            "charge_variants",
            "aggregation",
            "higher_order_structure",
            "immunochemical",
        ]

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _extract_attributes_impl(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Scan tables for characterization attributes."""
        doc_path = parsed_doc.get("document_path", "unknown")
        attributes: List[ExtractedAttribute] = []

        for page in parsed_doc.get("pages", []):
            page_num = page.get("page_number", 1)
            for table in page.get("tables", []):
                headers = [h.lower().strip() for h in table.get("headers", [])]
                if not headers:
                    continue

                name_idx = self._find_name_column(headers)
                value_idx = self._find_value_column(headers)
                unit_idx = self._find_unit_column(headers)
                method_idx = self._find_method_column(headers)

                if name_idx is None:
                    continue

                table_id = table.get("id", "unknown_table")

                for row in table.get("rows", []):
                    raw_headers = table.get("headers", [])
                    name = self._get_cell(row, raw_headers, name_idx)
                    if not name or not name.strip():
                        continue

                    # Attribute name quality gate: reject nonsense names
                    if not _is_valid_attribute_name(name):
                        logger.debug("Dropped invalid attribute name: %r (page %d)", name, page_num)
                        continue

                    value = None
                    if value_idx is not None:
                        raw_val = self._get_cell(row, raw_headers, value_idx)
                        value = self._try_parse_numeric(raw_val)

                    unit = ""
                    if unit_idx is not None:
                        unit = self._get_cell(row, raw_headers, unit_idx) or ""

                    method = ""
                    if method_idx is not None:
                        method = self._get_cell(row, raw_headers, method_idx) or ""

                    category = self._categorize_attribute(name, method)

                    attributes.append(
                        ExtractedAttribute(
                            name=name.strip(),
                            value=value if value is not None else 0.0,
                            unit=unit.strip(),
                            source_document=doc_path,
                            source_page=page_num,
                            source_table=table_id,
                            confidence=0.7,
                            context=f"Characterization extraction from {table_id}"
                            + (f" (method: {method})" if method else ""),
                            category=category,
                            anchor_ids=[str(uuid.uuid4())],
                            extraction_confidence=0.7,
                        )
                    )

        return attributes

    def _extract_evidence_impl(
        self, parsed_doc: Dict[str, Any]
    ) -> CharacterizationEvidence:
        """Assess ICH Q6B section coverage and extract key values."""
        all_text = self._gather_all_text(parsed_doc)
        all_table_text = self._gather_table_text(parsed_doc)
        combined = all_text + " " + all_table_text

        # Detect Q6B sections
        sections_found = []
        sections_missing = []
        for section_key, section_def in _Q6B_SECTIONS.items():
            found = False
            for pattern in section_def["patterns"]:
                if re.search(pattern, combined, re.IGNORECASE):
                    found = True
                    break
            if found:
                sections_found.append(section_def["label"])
            else:
                sections_missing.append(section_def["label"])

        total_required = len(_Q6B_SECTIONS)
        completeness_score = len(sections_found) / total_required if total_required > 0 else 0.0

        # Reference standard detection
        ref_std_identified = any(
            re.search(p, combined, re.IGNORECASE)
            for p in _REFERENCE_STANDARD_PATTERNS
        )
        ref_std_lot = ""
        for p in _REFERENCE_STANDARD_LOT_PATTERNS:
            m = re.search(p, combined, re.IGNORECASE)
            if m:
                ref_std_lot = m.group(1)
                break

        # Key numeric extractions
        hmw_val = self._extract_first_numeric(combined, _HMW_PATTERNS)
        potency_val = self._extract_first_numeric(combined, _POTENCY_PATTERNS)
        afucosylation_val = self._extract_first_numeric(combined, _AFUCOSYLATION_PATTERNS)
        main_charge_val = self._extract_first_numeric(combined, _MAIN_CHARGE_PATTERNS)

        # Also try extracting from tables (simple keyword match)
        if hmw_val is None:
            hmw_val = self._extract_value_from_tables(parsed_doc, ["hmw", "high molecular weight", "aggregate"])
        if potency_val is None:
            potency_val = self._extract_value_from_tables(parsed_doc, ["potency", "relative potency"])
        if afucosylation_val is None:
            afucosylation_val = self._extract_value_from_tables(parsed_doc, ["afucosylation", "afucosylated"])
        if main_charge_val is None:
            main_charge_val = self._extract_value_from_tables(parsed_doc, ["main peak", "main charge"])

        # Phase C: Table interpreter — handles pivoted layouts, replicate values,
        # and lot-column-aware extraction (prefer RM lots over PS).
        acidic_val: Optional[float] = None
        basic_val: Optional[float] = None
        per_lot_data: Dict[str, Dict[str, float]] = {}
        try:
            all_normalized = []
            for page in parsed_doc.get("pages", []):
                page_num = page.get("page_number", 1)
                for table in page.get("tables", []):
                    normalized = interpret_table(table, page_number=page_num)
                    all_normalized.extend(normalized)

            # Lot-aware value selection: prefer RM mean over PS
            _CQA_KEYWORDS_MAP = {
                "hmw": ["hmw", "high molecular weight", "aggregate"],
                "potency": ["potency", "relative potency", "biological activity"],
                "afucosylation": ["afucosyl", "afucose", "non-fucosyl", "g0"],
                "main_charge": [
                    "main peak", "main charge", "main component", "charge purity",
                    "main species", "m1",
                ],
                "acidic": ["acidic variant", "acidic peak"],
                "basic": ["basic variant", "basic peak"],
            }
            for cqa_key, kws in _CQA_KEYWORDS_MAP.items():
                val, lots = self._select_preferred_lot_value(all_normalized, kws)
                if lots:
                    per_lot_data[cqa_key] = lots
                if val is not None:
                    if cqa_key == "hmw" and hmw_val is None:
                        hmw_val = val
                    elif cqa_key == "potency" and potency_val is None:
                        potency_val = val
                    elif cqa_key == "afucosylation" and afucosylation_val is None:
                        afucosylation_val = val
                    elif cqa_key == "main_charge" and main_charge_val is None:
                        main_charge_val = val
                    elif cqa_key == "acidic" and acidic_val is None:
                        acidic_val = val
                    elif cqa_key == "basic" and basic_val is None:
                        basic_val = val

            # Inverse attribute derivation (monomeric purity -> HMW%,
            # charge purity -> main charge peak, fucose -> afucosylation)
            # Also lot-aware: prefer RM lots for inverse derivation
            derived, derived_lots = self._derive_inverse_lot_aware(all_normalized)
            if hmw_val is None and "hmw_pct" in derived:
                hmw_val = derived["hmw_pct"]
                if "hmw_pct" in derived_lots:
                    per_lot_data.setdefault("hmw", {}).update(derived_lots["hmw_pct"])
            if main_charge_val is None and "charge_variant_main_pct" in derived:
                main_charge_val = derived["charge_variant_main_pct"]
                if "charge_variant_main_pct" in derived_lots:
                    per_lot_data.setdefault("main_charge", {}).update(
                        derived_lots["charge_variant_main_pct"]
                    )
            if afucosylation_val is None and "afucosylation_pct" in derived:
                afucosylation_val = derived["afucosylation_pct"]
        except Exception as e:
            logger.debug("Table interpreter failed: %s", e)

        # Phase D: LLM-assisted extraction fallback for UNCERTAIN fields
        _uncertain_fields = {
            "hmw_pct": hmw_val,
            "main_charge_peak_pct": main_charge_val,
            "potency_relative_pct": potency_val,
            "afucosylation_pct": afucosylation_val,
        }
        _still_missing = [k for k, v in _uncertain_fields.items() if v is None]
        if _still_missing:
            try:
                from ingestion.llm_extraction import is_available, extract_table_values
                if is_available():
                    _llm_result = extract_table_values(
                        page_text=combined[:4000],
                        table_text=all_table_text[:3000],
                        target_fields=_still_missing,
                    )
                    for _fld, _ext in _llm_result.items():
                        if _ext and _ext.get("value") is not None:
                            _val = float(_ext["value"])
                            if _fld == "hmw_pct" and hmw_val is None:
                                hmw_val = _val
                            elif _fld == "main_charge_peak_pct" and main_charge_val is None:
                                main_charge_val = _val
                            elif _fld == "potency_relative_pct" and potency_val is None:
                                potency_val = _val
                            elif _fld == "afucosylation_pct" and afucosylation_val is None:
                                afucosylation_val = _val
                            logger.info("LLM fallback extracted %s = %s", _fld, _val)
            except Exception as _llm_err:
                logger.debug("LLM extraction unavailable: %s", _llm_err)

        # Count tables
        tables_found = sum(
            len(page.get("tables", []))
            for page in parsed_doc.get("pages", [])
        )

        # ---------------------------------------------------------------
        # Three-State Value Model: determine state for each CQA field
        # ---------------------------------------------------------------
        hmw_ev = self._determine_cqa_state(
            extracted_value=hmw_val,
            candidate_table_keywords=["sec", "monomer", "aggregate", "hmw", "size", "monomeric purity"],
            candidate_text_keywords=[r"\bSEC[-\s]?HPLC\b", r"\baggregat", r"\bsize[-\s]?exclusion\b",
                                     r"\bmonomer\b", r"\bHMW\b", r"\bmonomeric\s+purity\b"],
            field_name="HMW %",
            parsed_doc=parsed_doc,
            combined_text=combined,
        )
        main_charge_ev = self._determine_cqa_state(
            extracted_value=main_charge_val,
            candidate_table_keywords=["charge", "cex", "icief", "main peak", "acidic", "basic"],
            candidate_text_keywords=[r"\bcharge\s+heterogeneity\b", r"\bcharge\s+variant",
                                     r"\bCEX\b", r"\bicIEF\b", r"\bmain\s+(?:peak|charge)\b"],
            field_name="Main charge peak %",
            parsed_doc=parsed_doc,
            combined_text=combined,
        )
        afucosylation_ev = self._determine_cqa_state(
            extracted_value=afucosylation_val,
            candidate_table_keywords=["fucose", "afucosyl", "glycan", "g0f", "g1f"],
            candidate_text_keywords=[r"\bafucos", r"\bfucosylat", r"\bglycan\s+profil",
                                     r"\bN[-\s]?glycan\b", r"\bG0F?\b"],
            field_name="Afucosylation %",
            parsed_doc=parsed_doc,
            combined_text=combined,
        )
        potency_ev = self._determine_cqa_state(
            extracted_value=potency_val,
            candidate_table_keywords=["potency", "bioassay", "adcc", "cdc", "biological activity"],
            candidate_text_keywords=[r"\bpotency\b", r"\bbioassay\b", r"\bbiological\s+activity\b",
                                     r"\bADCC\b", r"\bCDC\b", r"\bcell[-\s]?based\s+assay\b"],
            field_name="Relative potency %",
            parsed_doc=parsed_doc,
            combined_text=combined,
        )

        # ---------------------------------------------------------------
        # Refine potency state: broader evidence check before confirming
        # absence. Per CLI v4.0 Step 1.1: for mAb characterization docs,
        # potency is always expected per ICH Q6B — if not extracted, mark
        # as UNCERTAIN (extraction gap) rather than CONFIRMED_ABSENT.
        # ---------------------------------------------------------------
        if potency_ev.state == CONFIRMED_ABSENT:
            potency_ev = self._refine_potency_state(
                combined, sections_found, potency_ev,
            )
            # Update legacy accessor if state changed
            if potency_ev.state == UNCERTAIN:
                potency_relative_pct = potency_ev.value  # stays None

        # ---------------------------------------------------------------
        # Critical gaps: ONLY from confirmed-absent fields
        # Extraction uncertainties: from uncertain fields
        # ---------------------------------------------------------------
        critical_gaps = self._identify_critical_gaps(
            sections_found, sections_missing,
            potency_ev, ref_std_identified, combined,
        )
        extraction_uncertainties = self._build_extraction_uncertainties(
            hmw_ev, main_charge_ev, afucosylation_ev, potency_ev,
        )
        reviewer_concerns = self._predict_reviewer_concerns(
            sections_found, sections_missing,
            potency_ev, afucosylation_ev,
            ref_std_identified, combined,
        )

        return CharacterizationEvidence(
            sections_found=sections_found,
            sections_missing=sections_missing,
            completeness_score=round(completeness_score, 3),
            reference_standard_identified=ref_std_identified,
            reference_standard_lot=ref_std_lot,
            # Three-state fields
            hmw=hmw_ev,
            main_charge_peak=main_charge_ev,
            afucosylation=afucosylation_ev,
            relative_potency=potency_ev,
            # Legacy backward-compat fields
            hmw_pct=hmw_ev.value,
            main_charge_peak_pct=main_charge_ev.value,
            afucosylation_pct=afucosylation_ev.value,
            potency_relative_pct=potency_ev.value,
            acidic_variants_pct=acidic_val,
            basic_variants_pct=basic_val,
            critical_gaps=critical_gaps,
            extraction_uncertainties=extraction_uncertainties,
            reviewer_concerns=reviewer_concerns,
            tables_found=tables_found,
        )

    # ------------------------------------------------------------------
    # B3: Reviewer concern prediction
    # ------------------------------------------------------------------

    @staticmethod
    def _select_preferred_lot_value(
        attrs: List[Any],
        keywords: List[str],
    ) -> tuple:
        """Select preferred value from normalized attributes, preferring RM lots.

        Returns (primary_value, per_lot_dict).
        """
        matches = []
        for attr in attrs:
            name_lower = attr.name.lower()
            if any(kw in name_lower for kw in keywords):
                matches.append(attr)

        if not matches:
            return None, {}

        rm_vals = [a for a in matches if a.lot_preference == "RM"]
        ps_vals = [a for a in matches if a.lot_preference == "PS"]

        per_lot: Dict[str, float] = {}
        for a in matches:
            if a.lot_id and a.lot_id.strip() and not a.lot_id.startswith("col_"):
                per_lot[a.lot_id] = a.value

        if rm_vals:
            return _mean([a.value for a in rm_vals]), per_lot
        elif ps_vals:
            return _mean([a.value for a in ps_vals]), per_lot
        elif matches:
            return matches[0].value, per_lot

        return None, {}

    @staticmethod
    def _derive_inverse_lot_aware(
        attrs: List[Any],
    ) -> tuple:
        """Lot-aware inverse attribute derivation.

        Groups attributes by name pattern, prefers RM lots for the inverse.
        Returns (derived_values_dict, derived_lots_dict).
        """
        derived: Dict[str, float] = {}
        derived_lots: Dict[str, Dict[str, float]] = {}

        # Group matching attributes by derived name
        groups: Dict[str, List[Any]] = {}
        for attr in attrs:
            name_lower = attr.name.lower().strip()
            for pattern, (derived_name, transform) in INVERSE_ATTRIBUTES.items():
                if pattern in name_lower:
                    groups.setdefault(derived_name, []).append((attr, transform))
                    break

        for derived_name, entries in groups.items():
            rm_entries = [(a, t) for a, t in entries if a.lot_preference == "RM"]
            ps_entries = [(a, t) for a, t in entries if a.lot_preference == "PS"]

            use = rm_entries if rm_entries else (ps_entries if ps_entries else entries)

            per_lot: Dict[str, float] = {}
            vals = []
            for attr, transform in use:
                try:
                    v = transform(attr.value)
                    vals.append(v)
                    if attr.lot_id and attr.lot_id.strip() and not attr.lot_id.startswith("col_"):
                        per_lot[attr.lot_id] = v
                except (ValueError, TypeError):
                    pass

            if vals:
                derived[derived_name] = _mean(vals)
                derived_lots[derived_name] = per_lot

        return derived, derived_lots

    def _determine_cqa_state(
        self,
        extracted_value: Optional[float],
        candidate_table_keywords: List[str],
        candidate_text_keywords: List[str],
        field_name: str,
        parsed_doc: Dict[str, Any],
        combined_text: str,
    ) -> EvidenceValue:
        """Determine three-state value: present / confirmed_absent / uncertain.

        Rules:
        - If value was extracted → PRESENT
        - If candidate tables/sections found but no value → UNCERTAIN
        - If NO candidate tables AND no text mentions → CONFIRMED_ABSENT
        """
        if extracted_value is not None:
            return EvidenceValue.present(extracted_value)

        # Check for candidate tables
        has_candidate_table = self._has_candidate_tables(
            parsed_doc, candidate_table_keywords
        )
        # Check for text mentions
        has_text_mention = any(
            re.search(p, combined_text, re.IGNORECASE)
            for p in candidate_text_keywords
        )

        if has_candidate_table:
            return EvidenceValue.uncertain_value(
                f"Candidate tables found for {field_name} but value could not be parsed"
            )
        elif has_text_mention:
            return EvidenceValue.uncertain_value(
                f"Text mentions {field_name} but no extractable table/value found"
            )
        else:
            return EvidenceValue.absent()

    def _refine_potency_state(
        self,
        combined_text: str,
        sections_found: List[str],
        current_ev: EvidenceValue,
    ) -> EvidenceValue:
        """Refine potency state before confirming absence.

        Scans for broader evidence that potency/biological activity data
        may be present but unextractable:
          (a) Candidate section names in text (Biological Activity, Potency,
              Bioassay, Functional Activity)
          (b) Narrative keywords (potency, bioassay, ADCC, CDC, relative potency)
          (c) mAb-specific: antibody characterization documents are expected to
              include biological activity per ICH Q6B — if no potency data was
              found, it is more likely an extraction gap than true absence.

        Returns UNCERTAIN if any signal is found, otherwise the original state.
        """
        # (a) Candidate section names in text
        section_patterns = [
            r"\bbiological\s+activity\b",
            r"\bpotency\b",
            r"\bbioassay\b",
            r"\bfunctional\s+activity\b",
        ]
        has_candidate_section = any(
            re.search(p, combined_text, re.IGNORECASE) for p in section_patterns
        )
        if has_candidate_section:
            return EvidenceValue.uncertain_value(
                reason="potency_section_found_but_no_numeric"
            )

        # (b) Narrative keyword count
        narrative_keywords = [
            r"\bpotency\b", r"\bbioassay\b", r"\bADCC\b", r"\bCDC\b",
            r"\brelative\s+potency\b",
        ]
        keyword_count = sum(
            1 for p in narrative_keywords
            if re.search(p, combined_text, re.IGNORECASE)
        )
        if keyword_count >= 2:
            return EvidenceValue.uncertain_value(
                reason="potency_section_found_but_no_numeric"
            )

        # (c) mAb document checks
        is_mab_doc = bool(re.search(
            r"\b(?:mAb|IgG[1-4]?[kλ]?|monoclonal\s+antibody|"
            r"therapeutic\s+(?:antibody|protein))\b",
            combined_text, re.IGNORECASE,
        ))

        # (c1) Reference material/standard certificate: these focus on
        # physicochemical characterization and typically omit potency assays.
        if is_mab_doc:
            is_ref_material = bool(re.search(
                r"\breference\s+(?:material|standard)\b",
                combined_text, re.IGNORECASE,
            ))
            if is_ref_material:
                return EvidenceValue.uncertain_value(
                    reason="potency_not_expected_in_reference_material_certificate"
                )

        # (c2) Comprehensive mAb characterization: if nearly all Q6B sections
        # are found (all but potency) but potency is missing, the data likely
        # exists in forms our extractor can't reach (figures, non-standard
        # tables). Only applies when document is near-complete (>= 7/8 sections).
        high_coverage = len(sections_found) >= len(_Q6B_SECTIONS) - 1
        if is_mab_doc and high_coverage:
            return EvidenceValue.uncertain_value(
                reason="potency_expected_for_mab_characterization_but_not_extracted"
            )

        return current_ev

    def _has_candidate_tables(
        self, parsed_doc: Dict[str, Any], keywords: List[str]
    ) -> bool:
        """Check if any table has headers or first-column cells matching keywords."""
        for page in parsed_doc.get("pages", []):
            for table in page.get("tables", []):
                headers = table.get("headers", [])
                header_text = " ".join(h.lower() for h in headers)
                if any(kw in header_text for kw in keywords):
                    return True
                # Check first column of rows (pivoted layout)
                for row in table.get("rows", []):
                    if isinstance(row, dict):
                        first_val = str(list(row.values())[0]).lower() if row else ""
                    elif isinstance(row, list) and row:
                        first_val = str(row[0]).lower()
                    else:
                        continue
                    if any(kw in first_val for kw in keywords):
                        return True
        return False

    def _build_extraction_uncertainties(
        self,
        hmw: EvidenceValue,
        main_charge: EvidenceValue,
        afucosylation: EvidenceValue,
        potency: EvidenceValue,
    ) -> List[str]:
        """Build list of extraction uncertainties from UNCERTAIN fields."""
        uncertainties = []
        for name, ev in [
            ("HMW %", hmw),
            ("Main charge peak %", main_charge),
            ("Afucosylation %", afucosylation),
            ("Relative potency %", potency),
        ]:
            if ev.state == UNCERTAIN and ev.uncertainty_reason:
                uncertainties.append(f"{name}: {ev.uncertainty_reason}")
        return uncertainties

    def _identify_critical_gaps(
        self,
        sections_found: List[str],
        sections_missing: List[str],
        potency_ev: EvidenceValue,
        ref_std_identified: bool,
        combined_text: str,
    ) -> List[str]:
        """Identify critical gaps — ONLY from confirmed-absent data.

        INV-004: Never show 'Missing X' unless state == CONFIRMED_ABSENT.
        """
        gaps = []

        # Missing potency: only flag if section is missing AND potency is confirmed absent
        potency_label = _Q6B_SECTIONS["biological_activity_potency"]["label"]
        if potency_label in sections_missing and potency_ev.state == CONFIRMED_ABSENT:
            gaps.append("Missing potency/biological activity data -- critical for regulatory submission")

        # No HOS data — section-level only (no numeric CQA to check)
        hos_label = _Q6B_SECTIONS["higher_order_structure"]["label"]
        if hos_label in sections_missing:
            gaps.append("No higher-order structure data -- structural integrity unconfirmed")

        # No reference standard traceability
        if not ref_std_identified:
            gaps.append("No reference standard traceability -- results cannot be verified")

        # Low completeness
        if len(sections_found) < 4:
            gaps.append(
                f"Low ICH Q6B coverage ({len(sections_found)}/{len(_Q6B_SECTIONS)} sections) "
                f"-- incomplete characterization"
            )

        # No glycosylation data for mAb
        glyco_label = _Q6B_SECTIONS["glycosylation_ptms"]["label"]
        if glyco_label in sections_missing:
            if re.search(r"\b(?:mAb|IgG|antibody|monoclonal)\b", combined_text, re.IGNORECASE):
                gaps.append("No glycosylation data for antibody -- glycan profiling required per ICH Q6B")

        return gaps

    def _predict_reviewer_concerns(
        self,
        sections_found: List[str],
        sections_missing: List[str],
        potency_ev: EvidenceValue,
        afucosylation_ev: EvidenceValue,
        ref_std_identified: bool,
        combined_text: str,
    ) -> List[str]:
        """B3: Predict reviewer concerns specific to characterization."""
        concerns = []

        # Missing potency — only flag as CRITICAL if confirmed absent
        potency_label = _Q6B_SECTIONS["biological_activity_potency"]["label"]
        if potency_label in sections_missing:
            if potency_ev.state == CONFIRMED_ABSENT:
                concerns.append(
                    "CRITICAL: No potency data provided. Reviewer will require biological "
                    "activity assessment per ICH Q6B Section 2."
                )
            elif potency_ev.state == UNCERTAIN:
                concerns.append(
                    "Potency data may be present but could not be fully extracted. "
                    "Manual verification recommended before submission."
                )

        # No HOS data -- structural integrity concern
        hos_label = _Q6B_SECTIONS["higher_order_structure"]["label"]
        if hos_label in sections_missing:
            concerns.append(
                "No higher-order structure (HOS) data. Reviewer will flag structural "
                "integrity concern -- CD, DSC, or FTIR required."
            )

        # Single method per CQA -- check for orthogonal coverage
        for section_key, section_def in _Q6B_SECTIONS.items():
            label = section_def["label"]
            if label not in sections_found:
                continue
            methods = section_def.get("methods", [])
            methods_found = sum(
                1 for m in methods
                if re.search(re.escape(m), combined_text, re.IGNORECASE)
            )
            if methods_found == 1:
                concerns.append(
                    f"Only one analytical method detected for {label}. "
                    f"Reviewer may request orthogonal method for confirmation."
                )

        # Afucosylation > 30% for ADCC mAb — only when present
        afuc_val = afucosylation_ev.value
        if afuc_val is not None and afuc_val > 30.0:
            is_adcc = re.search(r"\bADCC\b", combined_text, re.IGNORECASE) is not None
            if is_adcc:
                concerns.append(
                    f"Afucosylation ({afuc_val:.1f}%) exceeds 30% for ADCC-dependent mAb. "
                    f"Reviewer will likely question FcgammaRIIIa binding impact."
                )

        # No reference standard traceability
        if not ref_std_identified:
            concerns.append(
                "No reference standard identified. Reviewer will flag traceability gap."
            )

        return concerns

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _gather_all_text(self, parsed_doc: Dict[str, Any]) -> str:
        """Combine all text content from the parsed document."""
        parts = []
        for page in parsed_doc.get("pages", []):
            text = page.get("text", "")
            if text:
                parts.append(text)
        for para in parsed_doc.get("paragraphs", []):
            text = para.get("text", "")
            if text:
                parts.append(text)
        metadata = parsed_doc.get("metadata", {})
        title = metadata.get("title", "")
        if title:
            parts.append(title)
        return "\n".join(parts)

    def _gather_table_text(self, parsed_doc: Dict[str, Any]) -> str:
        """Collect all table cell text for keyword scanning."""
        parts = []
        for page in parsed_doc.get("pages", []):
            for table in page.get("tables", []):
                for h in table.get("headers", []):
                    parts.append(h)
                for row in table.get("rows", []):
                    if isinstance(row, dict):
                        parts.extend(str(v) for v in row.values())
                    elif isinstance(row, list):
                        parts.extend(str(v) for v in row)
        return " ".join(parts)

    def _extract_first_numeric(
        self, text: str, patterns: List[str]
    ) -> Optional[float]:
        """Extract first numeric match from text using patterns."""
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1))
                except (ValueError, IndexError):
                    continue
        return None

    def _extract_value_from_tables(
        self, parsed_doc: Dict[str, Any], name_keywords: List[str]
    ) -> Optional[float]:
        """Try to extract a numeric value from tables by attribute name keywords."""
        for page in parsed_doc.get("pages", []):
            for table in page.get("tables", []):
                headers = table.get("headers", [])
                for row in table.get("rows", []):
                    # Check if any cell contains one of the keywords
                    row_cells = []
                    if isinstance(row, dict):
                        row_cells = list(row.values())
                    elif isinstance(row, list):
                        row_cells = row

                    for i, cell in enumerate(row_cells):
                        cell_str = str(cell).lower()
                        if any(kw in cell_str for kw in name_keywords):
                            # Look for numeric in adjacent cells
                            for j, other_cell in enumerate(row_cells):
                                if j == i:
                                    continue
                                val = self._try_parse_numeric(str(other_cell))
                                if val is not None:
                                    return val
        return None

    def _find_name_column(self, headers: List[str]) -> Optional[int]:
        """Find a column that likely contains attribute names."""
        name_patterns = [
            "attribute", "parameter", "test", "assay", "analyte",
            "quality attribute", "name", "method", "analysis",
            "property", "characteristic", "measure", "specification",
        ]
        for i, h in enumerate(headers):
            for pat in name_patterns:
                if pat in h:
                    return i
        # Only fall back to column 0 if the first header looks like a name column
        # (text-heavy, not just numbers or short codes)
        if headers and len(headers) >= 2:
            first = headers[0].strip()
            # Reject pure-numeric or empty first headers
            if first and not first.replace('.', '').replace('-', '').isdigit() and len(first) > 3:
                return 0
        return None

    def _find_value_column(self, headers: List[str]) -> Optional[int]:
        """Find a column that likely contains values."""
        value_patterns = [
            "value", "result", "measured", "mean", "average",
            "observed", "actual", "data",
        ]
        for i, h in enumerate(headers):
            for pat in value_patterns:
                if pat in h:
                    return i
        return None

    def _find_unit_column(self, headers: List[str]) -> Optional[int]:
        """Find a column that likely contains units."""
        unit_patterns = ["unit", "uom", "units"]
        for i, h in enumerate(headers):
            for pat in unit_patterns:
                if pat in h:
                    return i
        return None

    def _find_method_column(self, headers: List[str]) -> Optional[int]:
        """Find a column that likely contains method names."""
        method_patterns = ["method", "technique", "analytical method"]
        for i, h in enumerate(headers):
            for pat in method_patterns:
                if pat in h:
                    return i
        return None

    def _get_cell(
        self, row: Any, headers: List[str], idx: int
    ) -> Optional[str]:
        """Get cell value from a row dict or list by column index."""
        if isinstance(row, dict):
            if idx < len(headers):
                return row.get(headers[idx])
            return None
        elif isinstance(row, list):
            if idx < len(row):
                return str(row[idx])
            return None
        return None

    def _try_parse_numeric(self, text: Optional[str]) -> Optional[float]:
        """Try to parse a numeric value from cell text."""
        if text is None:
            return None
        text = text.strip()
        if not text:
            return None
        cleaned = re.sub(r'[%<>~]', '', text).strip()
        if cleaned.upper() in ("N/A", "NA", "ND", "NT", "-", ""):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _categorize_attribute(self, name: str, method: str = "") -> str:
        """Categorize an attribute based on name and method."""
        combined = (name + " " + method).lower()

        if any(kw in combined for kw in ["potency", "bioassay", "biological activity", "adcc", "cdc"]):
            return "biological_activity"
        if any(kw in combined for kw in ["purity", "impurit", "rce-sds", "ce-sds", "rp-hplc", "hcp", "dna"]):
            return "purity"
        if any(kw in combined for kw in ["glyc", "fucosyl", "sialyl", "galactosyl", "ptm", "n-glycan"]):
            return "glycosylation"
        if any(kw in combined for kw in ["charge", "acidic", "basic", "main peak", "cex", "icief"]):
            return "charge_variants"
        if any(kw in combined for kw in ["hmw", "aggregat", "monomer", "fragment", "sec", "size"]):
            return "aggregation"
        if any(kw in combined for kw in ["cd ", "dsc", "ftir", "higher-order", "secondary structure"]):
            return "higher_order_structure"
        if any(kw in combined for kw in ["elisa", "spr", "biacore", "binding", "kd", "immunochem"]):
            return "immunochemical"
        if any(kw in combined for kw in ["primary structure", "peptide map", "lc-ms", "amino acid"]):
            return "identity"

        return "physicochemical"

    @staticmethod
    def _ev_to_dict(ev: EvidenceValue) -> Dict[str, Any]:
        """Convert EvidenceValue to serializable dict."""
        return {
            "state": ev.state,
            "value": ev.value,
            "source_anchor": ev.source_anchor,
            "extraction_confidence": ev.extraction_confidence,
            "uncertainty_reason": ev.uncertainty_reason,
        }

    def _evidence_to_dict(self, evidence: CharacterizationEvidence) -> Dict[str, Any]:
        """Convert CharacterizationEvidence to dict."""
        return {
            "sections_found": evidence.sections_found,
            "sections_missing": evidence.sections_missing,
            "sections_uncertain": evidence.sections_uncertain,
            "completeness_score": evidence.completeness_score,
            "reference_standard_identified": evidence.reference_standard_identified,
            "reference_standard_lot": evidence.reference_standard_lot,
            # Three-state CQA fields
            "hmw": self._ev_to_dict(evidence.hmw),
            "main_charge_peak": self._ev_to_dict(evidence.main_charge_peak),
            "afucosylation": self._ev_to_dict(evidence.afucosylation),
            "relative_potency": self._ev_to_dict(evidence.relative_potency),
            # Legacy backward-compat keys (still populated for consumers)
            "hmw_pct": evidence.hmw_pct,
            "main_charge_peak_pct": evidence.main_charge_peak_pct,
            "afucosylation_pct": evidence.afucosylation_pct,
            "potency_relative_pct": evidence.potency_relative_pct,
            "acidic_variants_pct": evidence.acidic_variants_pct,
            "basic_variants_pct": evidence.basic_variants_pct,
            # Gaps and concerns
            "critical_gaps": evidence.critical_gaps,
            "extraction_uncertainties": evidence.extraction_uncertainties,
            "reviewer_concerns": evidence.reviewer_concerns,
            "tables_found": evidence.tables_found,
            "extractor": evidence.extractor,
        }
