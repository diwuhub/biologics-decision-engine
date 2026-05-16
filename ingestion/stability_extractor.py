"""
C1: Stability Extractor (Phase 3 Track C).

Extracts stability data from timepoint tables per ICH Q1A/Q5C.
Detects conditions, timepoints, OOS/OOT events, and assesses
shelf-life claim sufficiency.

Contract:
- extract_attributes() MUST NOT raise unhandled exceptions.
- extract_evidence() MUST NOT raise unhandled exceptions.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ingestion.base_extractor import BaseExtractor
from specs.cross_document_bridge import ExtractedAttribute

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# C2: StabilityEvidence dataclass
# ---------------------------------------------------------------------------

@dataclass
class StabilityEvidence:
    """Evidence payload from a stability study report."""
    conditions_tested: List[str] = field(default_factory=list)  # e.g. ["5C", "25C/60RH", "40C/75RH"]
    max_timepoint_months: int = 0
    attributes_monitored: List[str] = field(default_factory=list)
    oos_events: List[Dict[str, Any]] = field(default_factory=list)  # attribute, timepoint, condition, value
    trend_concerns: List[Dict[str, Any]] = field(default_factory=list)  # projected OOS before shelf-life
    proposed_shelf_life: Optional[int] = None  # months
    sufficiency_for_claim: str = "insufficient"  # "sufficient" / "extrapolated" / "insufficient"
    critical_gaps: List[str] = field(default_factory=list)
    reviewer_concerns: List[str] = field(default_factory=list)
    tables_found: int = 0
    extractor: str = "StabilityExtractor"


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Timepoint column patterns: T=0, 3M, 6M, 9M, 12M, 18M, 24M, 36M
_TIMEPOINT_PATTERNS = [
    r"\bT\s*=?\s*0\b",
    r"\b(\d+)\s*M(?:onths?)?\b",
    r"\binitial\b",
]

_TIMEPOINT_HEADER_RE = re.compile(
    r"(?:T\s*=?\s*0|\b(\d+)\s*M(?:onths?)?\b|initial)",
    re.IGNORECASE,
)

# Condition patterns: 5C, 25C/60%RH, 40C/75%RH
_CONDITION_PATTERNS = {
    "5C": [
        r"\b(?:2[-\s]?8|5)\s*(?:deg(?:rees?)?)?[°]?\s*C\b",
        r"\brefrigerat",
        r"\b2\s*-\s*8\s*°?\s*C\b",
    ],
    "25C/60RH": [
        r"\b25\s*(?:deg(?:rees?)?)?[°]?\s*C\s*/?\s*60\s*%?\s*RH\b",
        r"\blong[-\s]?term\b",
    ],
    "30C/65RH": [
        r"\b30\s*(?:deg(?:rees?)?)?[°]?\s*C\s*/?\s*65\s*%?\s*RH\b",
        r"\bintermediate\b",
    ],
    "40C/75RH": [
        r"\b40\s*(?:deg(?:rees?)?)?[°]?\s*C\s*/?\s*75\s*%?\s*RH\b",
        r"\baccelerated\b",
    ],
}

# OOS/OOT flag patterns
_OOS_PATTERNS = [
    r"\bOOS\b",
    r"\bOOT\b",
    r"\bFAIL\b",
    r"\bout[\s-]?of[\s-]?specification\b",
    r"\bout[\s-]?of[\s-]?trend\b",
    r"\bexceeds?\s+specification\b",
]

# Shelf-life patterns
_SHELF_LIFE_PATTERNS = [
    r"\bshelf[\s-]?life\s*(?:of|:)?\s*(\d+)\s*months?\b",
    r"\b(\d+)\s*months?\s+shelf[\s-]?life\b",
    r"\bproposed\s+(?:shelf[\s-]?life|expiry|expiration)\s*(?:of|:)?\s*(\d+)\s*months?\b",
    r"\bexpiry\s*(?:of|:)?\s*(\d+)\s*months?\b",
]

# Attributes commonly monitored in stability studies
_STABILITY_ATTRIBUTES = [
    "purity", "monomer", "hmw", "aggregat", "potency", "ph",
    "appearance", "color", "subvisible", "particulate",
    "charge variant", "acidic", "basic", "main peak",
    "moisture", "osmolality", "endotoxin", "bioburden",
    "container closure", "protein content", "concentration",
]


class StabilityExtractor(BaseExtractor):
    """Extract stability data from parsed documents per ICH Q1A/Q5C.

    Contract:
    - extract_attributes() NEVER raises.
    - extract_evidence() NEVER raises.
    """

    def extract_attributes(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Extract stability data from timepoint tables."""
        try:
            return self._extract_attributes_impl(parsed_doc)
        except Exception as e:
            logger.error("StabilityExtractor.extract_attributes failed: %s", e)
            return []

    def extract_evidence(
        self, parsed_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assess stability study coverage per ICH Q1A/Q5C."""
        try:
            evidence = self._extract_evidence_impl(parsed_doc)
            return self._evidence_to_dict(evidence)
        except Exception as e:
            logger.error("StabilityExtractor.extract_evidence failed: %s", e)
            return {"error": str(e), "extractor": "StabilityExtractor"}

    def supported_categories(self) -> List[str]:
        return [
            "stability",
            "purity",
            "potency",
            "physicochemical",
            "aggregation",
        ]

    # ------------------------------------------------------------------
    # Internal: attribute extraction
    # ------------------------------------------------------------------

    def _extract_attributes_impl(
        self, parsed_doc: Dict[str, Any]
    ) -> List[ExtractedAttribute]:
        """Scan tables for stability timepoint data."""
        doc_path = parsed_doc.get("document_path", "unknown")
        attributes: List[ExtractedAttribute] = []

        for page in parsed_doc.get("pages", []):
            page_num = page.get("page_number", 1)
            for table in page.get("tables", []):
                headers = [h.lower().strip() for h in table.get("headers", [])]
                if not headers:
                    continue

                # Identify timepoint columns
                timepoint_cols = self._find_timepoint_columns(headers)
                name_idx = self._find_name_column(headers)
                condition_idx = self._find_condition_column(headers)

                if name_idx is None:
                    continue

                table_id = table.get("id", "unknown_table")

                for row in table.get("rows", []):
                    raw_headers = table.get("headers", [])
                    name = self._get_cell(row, raw_headers, name_idx)
                    if not name or not name.strip():
                        continue

                    condition = ""
                    if condition_idx is not None:
                        condition = self._get_cell(row, raw_headers, condition_idx) or ""

                    # Extract value from each timepoint column
                    for tp_idx, tp_label in timepoint_cols:
                        raw_val = self._get_cell(row, raw_headers, tp_idx)
                        value = self._try_parse_numeric(raw_val)
                        if value is None:
                            continue

                        timepoint_str = tp_label
                        if condition:
                            timepoint_str = f"{tp_label} ({condition.strip()})"

                        attributes.append(
                            ExtractedAttribute(
                                name=name.strip(),
                                value=value,
                                unit="%",  # most stability attributes are %
                                source_document=doc_path,
                                source_page=page_num,
                                source_table=table_id,
                                confidence=0.7,
                                context=f"Stability data at {timepoint_str}",
                                category="stability",
                                timepoint=tp_label,
                                anchor_ids=[str(uuid.uuid4())],
                                extraction_confidence=0.7,
                            )
                        )

        return attributes

    # ------------------------------------------------------------------
    # Internal: evidence extraction
    # ------------------------------------------------------------------

    def _extract_evidence_impl(
        self, parsed_doc: Dict[str, Any]
    ) -> StabilityEvidence:
        """Assess ICH Q1A/Q5C coverage."""
        all_text = self._gather_all_text(parsed_doc)
        all_table_text = self._gather_table_text(parsed_doc)
        combined = all_text + " " + all_table_text

        # Detect conditions
        conditions_tested = self._detect_conditions(combined)

        # Detect timepoints and find max
        max_timepoint = self._detect_max_timepoint(combined)

        # Detect monitored attributes
        attributes_monitored = self._detect_monitored_attributes(combined)

        # Detect OOS/OOT events
        oos_events = self._detect_oos_events(parsed_doc)

        # Detect proposed shelf-life
        proposed_shelf_life = self._detect_shelf_life(combined)

        # Assess trend concerns
        trend_concerns = self._detect_trend_concerns(parsed_doc, proposed_shelf_life)

        # Count tables
        tables_found = sum(
            len(page.get("tables", []))
            for page in parsed_doc.get("pages", [])
        )

        # Assess sufficiency
        sufficiency = self._assess_sufficiency(
            max_timepoint, proposed_shelf_life, oos_events, conditions_tested
        )

        # Identify critical gaps
        critical_gaps = self._identify_critical_gaps(
            conditions_tested, max_timepoint, proposed_shelf_life,
            attributes_monitored, oos_events,
        )

        # Phase 4C: ICH Q1A-specific reviewer concerns
        reviewer_concerns = self._predict_reviewer_concerns(
            conditions_tested, max_timepoint, proposed_shelf_life,
            attributes_monitored, oos_events, trend_concerns, combined,
        )

        return StabilityEvidence(
            conditions_tested=conditions_tested,
            max_timepoint_months=max_timepoint,
            attributes_monitored=attributes_monitored,
            oos_events=oos_events,
            trend_concerns=trend_concerns,
            proposed_shelf_life=proposed_shelf_life,
            sufficiency_for_claim=sufficiency,
            critical_gaps=critical_gaps,
            reviewer_concerns=reviewer_concerns,
            tables_found=tables_found,
        )

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def _detect_conditions(self, text: str) -> List[str]:
        """Detect storage conditions mentioned in text."""
        conditions = []
        for cond_name, patterns in _CONDITION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    conditions.append(cond_name)
                    break
        return conditions

    def _detect_max_timepoint(self, text: str) -> int:
        """Find the maximum timepoint in months from stability data.

        Prefers timepoints found in table headers. Falls back to text patterns
        that look like timepoint references (e.g., "T=0, 3M, 6M") rather than
        narrative shelf-life claims (e.g., "shelf-life of 24 months").
        """
        max_tp = 0

        # First, try to detect from table-like context: "T=0, 3M, 6M, 12M" patterns
        # These are typically comma/space-separated timepoint lists
        tp_list_pattern = r"(?:T\s*=?\s*0[,\s]+)(?:(\d+)\s*M(?:onths?)?[,\s]*){1,}"
        for match in re.finditer(tp_list_pattern, text, re.IGNORECASE):
            # Found a list starting with T=0, extract all month values from that span
            span_text = text[match.start():match.end() + 50]
            for m in re.finditer(r"\b(\d+)\s*M(?:onths?)?\b", span_text, re.IGNORECASE):
                try:
                    months = int(m.group(1))
                    if 0 < months <= 60:
                        max_tp = max(max_tp, months)
                except (ValueError, IndexError):
                    continue

        # Also scan for standalone timepoint patterns NOT in shelf-life context
        for match in re.finditer(r"\b(\d+)\s*M(?:onths?)?\b", text, re.IGNORECASE):
            try:
                months = int(match.group(1))
                if 0 < months <= 60:
                    # Check that this isn't in a shelf-life / expiry context
                    start = max(0, match.start() - 60)
                    prefix = text[start:match.start()].lower()
                    if any(kw in prefix for kw in ["shelf-life", "shelf life", "expiry",
                                                    "expiration", "proposed", "claim"]):
                        continue
                    max_tp = max(max_tp, months)
            except (ValueError, IndexError):
                continue

        return max_tp

    def _detect_monitored_attributes(self, text: str) -> List[str]:
        """Detect which attributes are monitored in the stability study."""
        found = []
        for attr in _STABILITY_ATTRIBUTES:
            if re.search(re.escape(attr), text, re.IGNORECASE):
                found.append(attr)
        return found

    @staticmethod
    def _is_glossary_oos(context: str) -> bool:
        """Filter out OOS mentions that are glossary/abbreviation definitions
        or procedural references (not actual OOS events)."""
        ctx_lower = context.lower()
        # Glossary/abbreviation list signals
        glossary_signals = [
            " = ", "abbreviation", "glossary", "defined as", "stands for",
            "acronym", "list of abbrevi", "table of abbrevi",
            "out of specifications",  # "OOS Out of Specifications" definition
            "out of specification",
        ]
        if any(sig in ctx_lower for sig in glossary_signals):
            return True
        # Abbreviation list pattern: multiple ALL-CAPS words on adjacent lines
        import re
        abbrev_count = len(re.findall(r'\b[A-Z]{2,5}\s+[A-Z][a-z]', context))
        if abbrev_count >= 3:
            return True
        # Procedural references (not actual events)
        procedural = [
            "oos investigation", "oos procedure", "deviation and/or oos",
            "result in deviation", "will result in",
        ]
        if any(sig in ctx_lower for sig in procedural):
            return True
        return False

    def _detect_oos_events(self, parsed_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Detect OOS/OOT events from tables and text."""
        events = []
        all_text = self._gather_all_text(parsed_doc)
        seen_contexts = set()

        for pattern in _OOS_PATTERNS:
            for match in re.finditer(pattern, all_text, re.IGNORECASE):
                start = max(0, match.start() - 100)
                end = min(len(all_text), match.end() + 100)
                context = all_text[start:end].strip()

                # Skip glossary/abbreviation definitions
                if self._is_glossary_oos(context):
                    continue

                # Deduplicate by context similarity
                ctx_key = context[:80]
                if ctx_key in seen_contexts:
                    continue
                seen_contexts.add(ctx_key)

                events.append({
                    "flag": match.group(0),
                    "context": context,
                    "source": "text",
                })

        # Also check table cells
        for page in parsed_doc.get("pages", []):
            for table in page.get("tables", []):
                for row in table.get("rows", []):
                    row_cells = []
                    if isinstance(row, dict):
                        row_cells = list(row.values())
                    elif isinstance(row, list):
                        row_cells = row
                    for cell in row_cells:
                        cell_str = str(cell).strip()
                        for pattern in _OOS_PATTERNS:
                            if re.search(pattern, cell_str, re.IGNORECASE):
                                events.append({
                                    "flag": cell_str,
                                    "context": " | ".join(str(c) for c in row_cells),
                                    "source": "table",
                                })
                                break

        return events

    def _detect_shelf_life(self, text: str) -> Optional[int]:
        """Extract proposed shelf-life in months from text."""
        for pattern in _SHELF_LIFE_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                for group in m.groups():
                    if group is not None:
                        try:
                            return int(group)
                        except ValueError:
                            continue
        return None

    def _detect_trend_concerns(
        self, parsed_doc: Dict[str, Any], proposed_shelf_life: Optional[int]
    ) -> List[Dict[str, Any]]:
        """Detect trend concerns -- values approaching specs over time."""
        concerns: List[Dict[str, Any]] = []
        if proposed_shelf_life is None:
            return concerns

        # Look for timepoint tables with decreasing purity or increasing aggregation
        for page in parsed_doc.get("pages", []):
            for table in page.get("tables", []):
                headers = [h.lower().strip() for h in table.get("headers", [])]
                if not headers:
                    continue

                timepoint_cols = self._find_timepoint_columns(headers)
                name_idx = self._find_name_column(headers)
                if name_idx is None or len(timepoint_cols) < 2:
                    continue

                for row in table.get("rows", []):
                    raw_headers = table.get("headers", [])
                    name = self._get_cell(row, raw_headers, name_idx)
                    if not name:
                        continue

                    # Collect values across timepoints
                    values = []
                    for tp_idx, tp_label in timepoint_cols:
                        raw_val = self._get_cell(row, raw_headers, tp_idx)
                        val = self._try_parse_numeric(raw_val)
                        if val is not None:
                            tp_months = self._parse_timepoint_months(tp_label)
                            values.append((tp_months, val))

                    if len(values) >= 2:
                        values.sort(key=lambda x: x[0])
                        # Check for monotonic decline (purity) or increase (aggregation)
                        is_declining = all(
                            values[i][1] >= values[i + 1][1]
                            for i in range(len(values) - 1)
                        )
                        name_lower = name.lower()
                        if is_declining and any(kw in name_lower for kw in ["purity", "monomer", "main peak", "potency"]):
                            rate_per_month = (values[0][1] - values[-1][1]) / max(1, values[-1][0] - values[0][0])
                            if rate_per_month > 0 and proposed_shelf_life:
                                projected_end = values[-1][1] - rate_per_month * (proposed_shelf_life - values[-1][0])
                                if projected_end < 90.0:  # typical lower spec for purity
                                    concerns.append({
                                        "attribute": name.strip(),
                                        "trend": "declining",
                                        "rate_per_month": round(rate_per_month, 3),
                                        "projected_value_at_shelf_life": round(projected_end, 1),
                                    })

        return concerns

    def _assess_sufficiency(
        self,
        max_timepoint: int,
        proposed_shelf_life: Optional[int],
        oos_events: List[Dict[str, Any]],
        conditions_tested: List[str],
    ) -> str:
        """Assess whether stability data supports the proposed shelf-life claim.

        ICH Q1A: >= 12 months real-time data required for any shelf-life claim.
        Real-time data < proposed_shelf_life * 0.5 -> major gap.
        """
        if proposed_shelf_life is None:
            if max_timepoint == 0:
                return "insufficient"
            # No claim made, but data exists
            if max_timepoint >= 12:
                return "sufficient"
            return "insufficient"

        # Any OOS at recommended storage is a critical concern
        if oos_events:
            # Check if any OOS is at recommended (5C or 25C) storage
            for event in oos_events:
                ctx = event.get("context", "").lower()
                if any(cond in ctx for cond in ["5", "25", "long-term", "recommended"]):
                    return "insufficient"

        # ICH Q1A: need >= 12 months real-time for any claim
        if max_timepoint < 12:
            return "insufficient"

        # Real-time data < proposed_shelf_life * 0.5 -> extrapolated
        if max_timepoint < proposed_shelf_life * 0.5:
            return "insufficient"

        # Real-time covers less than full proposed shelf-life
        if max_timepoint < proposed_shelf_life:
            return "extrapolated"

        return "sufficient"

    def _identify_critical_gaps(
        self,
        conditions_tested: List[str],
        max_timepoint: int,
        proposed_shelf_life: Optional[int],
        attributes_monitored: List[str],
        oos_events: List[Dict[str, Any]],
    ) -> List[str]:
        """Identify critical gaps in the stability program."""
        gaps = []

        # No long-term condition
        if "5C" not in conditions_tested and "25C/60RH" not in conditions_tested:
            gaps.append("No long-term storage condition data -- required per ICH Q1A")

        # No accelerated condition
        if "40C/75RH" not in conditions_tested:
            gaps.append("No accelerated stability data (40C/75RH) -- required per ICH Q1A")

        # Less than 12 months real-time data
        if max_timepoint < 12:
            gaps.append(
                f"Only {max_timepoint} months real-time data -- ICH Q1A requires >= 12 months for any shelf-life claim"
            )

        # Real-time data < proposed shelf-life * 0.5
        if proposed_shelf_life is not None and max_timepoint < proposed_shelf_life * 0.5:
            gaps.append(
                f"Real-time data ({max_timepoint}M) < 50% of proposed shelf-life ({proposed_shelf_life}M) -- major gap"
            )

        # OOS at recommended storage
        for event in oos_events:
            ctx = event.get("context", "").lower()
            if any(cond in ctx for cond in ["5", "25", "long-term", "recommended"]):
                gaps.append(
                    f"OOS event at recommended storage condition -- critical concern: {event.get('flag', 'unknown')}"
                )
                break

        # Key attributes not monitored
        critical_attrs = ["purity", "potency", "aggregat"]
        for attr in critical_attrs:
            if attr not in attributes_monitored:
                gaps.append(f"Stability-indicating attribute '{attr}' not monitored")

        return gaps

    # ------------------------------------------------------------------
    # Phase 4C: Reviewer concern prediction (ICH Q1A-specific)
    # ------------------------------------------------------------------

    def _predict_reviewer_concerns(
        self,
        conditions_tested: List[str],
        max_timepoint: int,
        proposed_shelf_life: Optional[int],
        attributes_monitored: List[str],
        oos_events: List[Dict[str, Any]],
        trend_concerns: List[Dict[str, Any]],
        combined_text: str,
    ) -> List[str]:
        """Predict ICH Q1A-specific reviewer concerns for stability data."""
        concerns = []

        # Insufficient timepoints: < 4 timepoints for long-term
        tp_count = len(re.findall(r"\b\d+\s*M(?:onths?)?\b", combined_text, re.IGNORECASE))
        if tp_count < 4 and max_timepoint > 0:
            concerns.append(
                f"Insufficient timepoints ({tp_count} detected). ICH Q1A recommends "
                f"at minimum T=0, 3M, 6M, 9M, 12M for the first year."
            )

        # Missing accelerated condition
        if "40C/75RH" not in conditions_tested:
            concerns.append(
                "No accelerated stability data (40C/75RH). Reviewer will require "
                "accelerated condition per ICH Q1A Section 2.1.7."
            )

        # Extrapolation beyond data
        if proposed_shelf_life is not None and max_timepoint > 0:
            if max_timepoint < proposed_shelf_life:
                ratio = max_timepoint / proposed_shelf_life
                if ratio < 0.5:
                    concerns.append(
                        f"CRITICAL: Proposed shelf-life ({proposed_shelf_life}M) exceeds "
                        f"real-time data ({max_timepoint}M) by >2x. ICH Q1A limits "
                        f"extrapolation to 2x the real-time data period."
                    )
                else:
                    concerns.append(
                        f"Shelf-life claim ({proposed_shelf_life}M) requires extrapolation "
                        f"beyond available data ({max_timepoint}M). Reviewer will scrutinize "
                        f"statistical justification per ICH Q1E."
                    )

        # OOS events will draw attention
        if oos_events:
            concerns.append(
                f"{len(oos_events)} OOS/OOT event(s) detected. Reviewer will require "
                f"root cause analysis and investigation report."
            )

        # Trend concerns
        for tc in trend_concerns:
            attr = tc.get("attribute", "unknown")
            projected = tc.get("projected_value_at_shelf_life")
            if projected is not None:
                concerns.append(
                    f"Trending concern for '{attr}': projected value {projected} at "
                    f"shelf-life end may breach specification. Reviewer will evaluate "
                    f"degradation kinetics."
                )

        # Missing key stability-indicating attributes
        if "potency" not in attributes_monitored:
            concerns.append(
                "Potency not monitored in stability study. Reviewer will flag this "
                "as a critical omission per ICH Q5C."
            )

        return concerns

    # ------------------------------------------------------------------
    # Column finding helpers
    # ------------------------------------------------------------------

    def _find_timepoint_columns(self, headers: List[str]) -> List[tuple]:
        """Find columns that represent timepoints. Returns list of (index, label)."""
        tp_cols = []
        for i, h in enumerate(headers):
            if _TIMEPOINT_HEADER_RE.search(h):
                tp_cols.append((i, h.strip()))
            elif h.strip().lower() in ("t=0", "t0", "initial", "0m", "0 m"):
                tp_cols.append((i, h.strip()))
        return tp_cols

    def _find_name_column(self, headers: List[str]) -> Optional[int]:
        """Find column with attribute names."""
        name_patterns = [
            "attribute", "parameter", "test", "assay", "analyte",
            "quality attribute", "name", "analysis",
        ]
        for i, h in enumerate(headers):
            for pat in name_patterns:
                if pat in h:
                    return i
        # Fallback: first non-timepoint column
        tp_indices = {idx for idx, _ in self._find_timepoint_columns(headers)}
        for i in range(len(headers)):
            if i not in tp_indices:
                return i
        if headers:
            return 0
        return None

    def _find_condition_column(self, headers: List[str]) -> Optional[int]:
        """Find column with storage conditions."""
        cond_patterns = ["condition", "storage", "temperature"]
        for i, h in enumerate(headers):
            for pat in cond_patterns:
                if pat in h:
                    return i
        return None

    def _parse_timepoint_months(self, label: str) -> int:
        """Parse months from a timepoint label like '3M', '6 Months', 'T=0'."""
        label = label.strip().lower()
        if label in ("t=0", "t0", "initial", "0m", "0 m"):
            return 0
        m = re.search(r"(\d+)\s*m", label, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return 0

    # ------------------------------------------------------------------
    # Generic helpers
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
        # Check for OOS/OOT/FAIL flags first
        for pattern in _OOS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return None
        cleaned = re.sub(r'[%<>~]', '', text).strip()
        if cleaned.upper() in ("N/A", "NA", "ND", "NT", "-", ""):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _evidence_to_dict(self, evidence: StabilityEvidence) -> Dict[str, Any]:
        """Convert StabilityEvidence to dict."""
        return {
            "conditions_tested": evidence.conditions_tested,
            "max_timepoint_months": evidence.max_timepoint_months,
            "attributes_monitored": evidence.attributes_monitored,
            "oos_events": evidence.oos_events,
            "trend_concerns": evidence.trend_concerns,
            "proposed_shelf_life": evidence.proposed_shelf_life,
            "sufficiency_for_claim": evidence.sufficiency_for_claim,
            "critical_gaps": evidence.critical_gaps,
            "reviewer_concerns": evidence.reviewer_concerns,
            "tables_found": evidence.tables_found,
            "extractor": evidence.extractor,
        }
