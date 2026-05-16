"""
A1: Document Classifier.

Classifies parsed documents into one of the recognized CMC document types
using keyword heuristics. No LLM -- pure pattern matching.

Detection signals:
- COMPARABILITY: "comparability", "pre-change", "post-change" + pre/post column pair
- CHARACTERIZATION: "characterization", "structural analysis" + attribute/value/unit tables
- STABILITY: "stability study", timepoint columns (T=0, 3M, 6M...)
- ANALYTICAL_METHOD: "method validation", "validation report" + recovery%/RSD/R^2
- PROCESS_VALIDATION: "process validation" + batch/parameter/target/actual/spec
- CTD_MODULE_3: "Module 3", "3.2.S", "3.2.P"
- UNKNOWN: none match with confidence >= 0.5
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DocTypeSpec:
    """Result of document type classification."""
    document_type: str  # COMPARABILITY | CHARACTERIZATION | STABILITY | ANALYTICAL_METHOD | PROCESS_VALIDATION | CTD_MODULE_3 | UNKNOWN
    confidence: float   # 0.0 - 1.0
    classification_notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Detection signal definitions
# ---------------------------------------------------------------------------

_COMPARABILITY_TEXT_SIGNALS = [
    r"\bcomparability\b",
    r"\bpre[-\s]?change\b",
    r"\bpost[-\s]?change\b",
]

_COMPARABILITY_TABLE_COLS = {
    "pre": ["pre-change", "pre change", "pre_change", "pre", "before", "reference", "original"],
    "post": ["post-change", "post change", "post_change", "post", "after", "proposed", "new"],
}

_CHARACTERIZATION_TEXT_SIGNALS = [
    r"\bcharacterization\b",
    r"\bstructural\s+analysis\b",
    r"\bhigher[-\s]order\s+structure\b",
    r"\bpost[-\s]?translational\s+modification\b",
    r"\bglycan\s+profil\b",
    r"\bprimary\s+structure\b",
    r"\bmass\s+spectrom\b",
    r"\breference\s+material\b",
    r"\bcertified\s+value\b",
    r"\bcharacterisation\b",  # British spelling (EMA docs)
]

_STABILITY_TEXT_SIGNALS = [
    r"\bstability\s+stud(?:y|ies)\b",
    r"\bICH\s+Q[15]C?\b",
    r"\bshelf[\s-]life\b",
    r"\baccelerated\s+stability\b",
    r"\blong[-\s]?term\s+stability\b",
]

_STABILITY_TIMEPOINT_PATTERNS = [
    r"\bT\s*=\s*0\b",
    r"\b[0-9]+\s*M\b",          # 3M, 6M, 12M
    r"\b[0-9]+\s*month\b",
    r"\btimepoint\b",
]

_ANALYTICAL_METHOD_TEXT_SIGNALS = [
    r"\bmethod\s+validation\b",
    r"\bvalidation\s+report\b",
    r"\bsystem\s+suitability\b",
    r"\blinearity\b",
    r"\bICH\s+Q2\b",
    r"\bICH\s+Q14\b",
]

_ANALYTICAL_METHOD_TABLE_SIGNALS = [
    r"\brecovery\s*%?\b",
    r"\bRSD\b",
    r"\bR[²2]\b",
    r"\b%\s*RSD\b",
    r"\bLOD\b",
    r"\bLOQ\b",
]

_PROCESS_VALIDATION_TEXT_SIGNALS = [
    r"\bprocess\s+validation\b",
    r"\bPPQ\b",
    r"\bperformance\s+qualification\b",
    r"\bcontinued\s+process\s+verification\b",
]

_PROCESS_VALIDATION_TABLE_SIGNALS = [
    r"\bbatch\b",
    r"\bparameter\b",
    r"\btarget\b",
    r"\bactual\b",
    r"\bspec(?:ification)?\b",
]

_CTD_MODULE_3_TEXT_SIGNALS = [
    r"\bModule\s+3\b",
    r"\b3\.2\.S\b",
    r"\b3\.2\.P\b",
    r"\bCTD\b",
    r"\bQuality\s+Overall\s+Summary\b",
]


class DocumentClassifier:
    """Classify document type from parsed content using keyword heuristics.

    Never blocks analysis -- when unsure, returns UNKNOWN with low confidence
    so the dispatcher can route to the generic extractor.
    """

    def classify(self, parsed_doc: Dict[str, Any]) -> DocTypeSpec:
        """Classify document type from parsed content.

        Parameters
        ----------
        parsed_doc : dict
            Parsed document following the common schema
            (pages, paragraphs, tables, metadata).

        Returns
        -------
        DocTypeSpec
            Classification result with type, confidence, and notes.
        """
        try:
            return self._classify_impl(parsed_doc)
        except Exception as e:
            # Never crash -- return UNKNOWN on any error
            return DocTypeSpec(
                document_type="UNKNOWN",
                confidence=0.0,
                classification_notes=[f"Classification error: {e}"],
            )

    def _classify_impl(self, parsed_doc: Dict[str, Any]) -> DocTypeSpec:
        """Internal classification logic."""
        all_text = self._gather_text(parsed_doc)
        all_headers = self._gather_table_headers(parsed_doc)
        all_table_text = self._gather_table_text(parsed_doc)

        scores: Dict[str, float] = {}
        notes: Dict[str, List[str]] = {
            "COMPARABILITY": [],
            "CHARACTERIZATION": [],
            "STABILITY": [],
            "ANALYTICAL_METHOD": [],
            "PROCESS_VALIDATION": [],
            "CTD_MODULE_3": [],
        }

        # --- COMPARABILITY ---
        comp_score = 0.0
        for pattern in _COMPARABILITY_TEXT_SIGNALS:
            if re.search(pattern, all_text, re.IGNORECASE):
                comp_score += 0.25
                notes["COMPARABILITY"].append(f"Text match: {pattern}")
        if self._has_pre_post_columns(all_headers):
            comp_score += 0.35
            notes["COMPARABILITY"].append("Pre/post column pair detected")
        scores["COMPARABILITY"] = min(comp_score, 1.0)

        # --- CHARACTERIZATION ---
        char_score = 0.0
        char_hits = 0
        for pattern in _CHARACTERIZATION_TEXT_SIGNALS:
            if re.search(pattern, all_text, re.IGNORECASE):
                char_hits += 1
                notes["CHARACTERIZATION"].append(f"Text match: {pattern}")
        char_score = min(char_hits * 0.2, 0.8)
        # Check for attribute/value/unit table structure
        if self._has_attribute_value_unit_tables(all_headers):
            char_score += 0.2
            notes["CHARACTERIZATION"].append("Attribute/value/unit table detected")
        scores["CHARACTERIZATION"] = min(char_score, 1.0)

        # --- STABILITY ---
        stab_score = 0.0
        for pattern in _STABILITY_TEXT_SIGNALS:
            if re.search(pattern, all_text, re.IGNORECASE):
                stab_score += 0.25
                notes["STABILITY"].append(f"Text match: {pattern}")
        tp_hits = 0
        combined_table_text = all_text + " " + all_table_text
        for pattern in _STABILITY_TIMEPOINT_PATTERNS:
            if re.search(pattern, combined_table_text, re.IGNORECASE):
                tp_hits += 1
        if tp_hits >= 2:
            stab_score += 0.3
            notes["STABILITY"].append(f"Timepoint signals: {tp_hits}")
        scores["STABILITY"] = min(stab_score, 1.0)

        # --- ANALYTICAL_METHOD ---
        am_score = 0.0
        for pattern in _ANALYTICAL_METHOD_TEXT_SIGNALS:
            if re.search(pattern, all_text, re.IGNORECASE):
                am_score += 0.2
                notes["ANALYTICAL_METHOD"].append(f"Text match: {pattern}")
        am_table_hits = 0
        for pattern in _ANALYTICAL_METHOD_TABLE_SIGNALS:
            if re.search(pattern, all_table_text, re.IGNORECASE):
                am_table_hits += 1
        if am_table_hits >= 2:
            am_score += 0.3
            notes["ANALYTICAL_METHOD"].append(f"Table signals: {am_table_hits}")
        scores["ANALYTICAL_METHOD"] = min(am_score, 1.0)

        # --- PROCESS_VALIDATION ---
        pv_score = 0.0
        for pattern in _PROCESS_VALIDATION_TEXT_SIGNALS:
            if re.search(pattern, all_text, re.IGNORECASE):
                pv_score += 0.25
                notes["PROCESS_VALIDATION"].append(f"Text match: {pattern}")
        pv_table_hits = 0
        for pattern in _PROCESS_VALIDATION_TABLE_SIGNALS:
            if re.search(pattern, all_table_text, re.IGNORECASE):
                pv_table_hits += 1
        if pv_table_hits >= 3:
            pv_score += 0.3
            notes["PROCESS_VALIDATION"].append(f"Table signals: {pv_table_hits}")
        scores["PROCESS_VALIDATION"] = min(pv_score, 1.0)

        # --- CTD_MODULE_3 ---
        ctd_score = 0.0
        for pattern in _CTD_MODULE_3_TEXT_SIGNALS:
            if re.search(pattern, all_text, re.IGNORECASE):
                ctd_score += 0.2
                notes["CTD_MODULE_3"].append(f"Text match: {pattern}")
        scores["CTD_MODULE_3"] = min(ctd_score, 1.0)

        # Tie-breaking for EPARs: these cover both characterization and stability.
        # Use characterization-specific deep content signals to decide.
        is_epar = bool(re.search(
            r"\b(?:EPAR|public\s+assessment\s+report|CHMP\b.*\bassessment\s+report|assessment\s+report\b.*\bCHMP)\b",
            all_text, re.IGNORECASE | re.DOTALL
        ))
        char_score_val = scores.get("CHARACTERIZATION", 0)
        stab_score_val = scores.get("STABILITY", 0)
        if is_epar and char_score_val >= 0.3 and stab_score_val > char_score_val:
            # Count deep characterization signals (HOS, glycan, primary structure, PTM)
            deep_char_signals = [
                r"\bhigher[-\s]?order\s+structure\b", r"\bHOS\b",
                r"\bglycan\s+profil", r"\bglycosylation\b",
                r"\bprimary\s+structure\b", r"\bpeptide\s+mapp?ing\b",
                r"\bcharge\s+heterogeneity\b", r"\bcharge\s+variant",
                r"\bbiological\s+activity\b", r"\bpotency\b",
                r"\bimmunochemical\b", r"\bbinding\s+affinity\b",
            ]
            deep_stab_signals = [
                r"\baccelerated\s+stability\b", r"\blong[-\s]?term\s+stability\b",
                r"\bphotostability\b", r"\bshelf[-\s]life\b",
                r"\b(?:25|30|40)\s*°?\s*C", r"\bICH\s+Q[15]C?\b",
                r"\bforced\s+degradation\b", r"\bstress\s+(?:test|stud)",
                r"\b\d+\s*month", r"\bexpiry\b",
            ]
            deep_char_hits = sum(1 for p in deep_char_signals if re.search(p, all_text, re.IGNORECASE))
            deep_stab_hits = sum(1 for p in deep_stab_signals if re.search(p, all_text, re.IGNORECASE))
            # Only boost characterization if char depth significantly exceeds stability depth
            if deep_char_hits >= 4 and deep_stab_hits <= 4:
                scores["CHARACTERIZATION"] = max(char_score_val, stab_score_val + 0.05)
                notes["CHARACTERIZATION"].append(
                    f"EPAR boost: {deep_char_hits} char vs {deep_stab_hits} stab deep signals"
                )

        # Pick the highest-scoring type
        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]

        if best_score < 0.5:
            return DocTypeSpec(
                document_type="UNKNOWN",
                confidence=best_score,
                classification_notes=[
                    f"No type reached confidence >= 0.5. "
                    f"Best: {best_type} ({best_score:.2f})"
                ] + notes.get(best_type, []),
            )

        return DocTypeSpec(
            document_type=best_type,
            confidence=best_score,
            classification_notes=notes[best_type],
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _gather_text(self, parsed_doc: Dict[str, Any]) -> str:
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
        # Metadata
        metadata = parsed_doc.get("metadata", {})
        title = metadata.get("title", "")
        if title:
            parts.append(title)
        return "\n".join(parts)

    def _gather_table_headers(self, parsed_doc: Dict[str, Any]) -> List[List[str]]:
        """Collect all table header rows."""
        all_headers = []
        for page in parsed_doc.get("pages", []):
            for table in page.get("tables", []):
                headers = table.get("headers", [])
                if headers:
                    all_headers.append([h.lower().strip() for h in headers])
        return all_headers

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

    def _has_pre_post_columns(self, all_headers: List[List[str]]) -> bool:
        """Check if any table has both a pre-change and post-change column."""
        for headers in all_headers:
            has_pre = any(
                any(pat in h for pat in _COMPARABILITY_TABLE_COLS["pre"])
                for h in headers
            )
            has_post = any(
                any(pat in h for pat in _COMPARABILITY_TABLE_COLS["post"])
                for h in headers
            )
            if has_pre and has_post:
                return True
        return False

    def _has_attribute_value_unit_tables(self, all_headers: List[List[str]]) -> bool:
        """Check if any table has attribute + value + unit columns."""
        attr_patterns = ["attribute", "parameter", "test", "assay", "analyte"]
        value_patterns = ["value", "result", "measured", "mean", "average"]
        unit_patterns = ["unit", "uom"]
        for headers in all_headers:
            has_attr = any(any(p in h for p in attr_patterns) for h in headers)
            has_val = any(any(p in h for p in value_patterns) for h in headers)
            has_unit = any(any(p in h for p in unit_patterns) for h in headers)
            if has_attr and (has_val or has_unit):
                return True
        return False
