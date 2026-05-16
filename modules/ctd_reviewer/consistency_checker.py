"""Consistency Checker — numerical extraction and cross-section conflict detection.

Scans classified sections for contradictory numerical values, terminology
mismatches, unit discrepancies, and named-entity conflicts.

Input:  list of (section_name, text_content) pairs  OR  classification dicts
Output: dict with consistency_status, consistency_flags (conflicts), confidence

Each conflict contains: value_a, value_b, section_a, section_b, severity.
"""

import re
from itertools import combinations

# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

_NUM_WITH_UNIT = re.compile(
    r"(?<!\w)"
    r"(\d+(?:\.\d+)?)"
    r"\s*"
    r"(–|-|to)?"
    r"\s*"
    r"(\d+(?:\.\d+)?)?"
    r"\s*"
    r"(mg/mL|mg/ml|g/L|g/l|µg/mL|µg/ml|"
    r"mM|M|%|°C|°F|kDa|Da|mL|L|"
    r"mg|µg|g|kg|nm|µm|mm|cm|"
    r"IU/mL|IU/ml|U/mL|U/ml|"
    r"ppm|ppb|min|hours?|h|days?|"
    r"CFU/mL|EU/mL|EU/mg|copies/mL)"
    r"(?!\w)",
    re.IGNORECASE,
)

_PH_PATTERN = re.compile(
    r"pH\s+(?:of\s+)?(\d+(?:\.\d+)?)"
    r"(?:\s*(?:–|-|to)\s*(\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)

_TERMINOLOGY_GROUPS: list[tuple[str, list[list[str]]]] = [
    ("process_name", [
        ["upstream process", "upstream"],
        ["cell culture process", "cell culture"],
        ["fermentation process", "fermentation"],
    ]),
    ("purification_name", [
        ["downstream process", "downstream"],
        ["purification process", "purification"],
    ]),
    ("cell_line", [
        ["CHO", "Chinese hamster ovary"],
        ["HEK293", "HEK 293", "HEK-293"],
        ["NS0", "NS/0"],
        ["Sp2/0"],
    ]),
    ("expression_system", [
        ["E. coli", "Escherichia coli"],
        ["Pichia pastoris", "P. pastoris", "Komagataella phaffii"],
    ]),
    ("column_resin", [
        ["Protein A", "ProA", "MabSelect"],
        ["Protein G"],
    ]),
]

_ENTITY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("protein_a_resin", re.compile(
        r"(MabSelect\s+\w+(?:\s+\w+)?|Amsphere\s+\w+|Toyopearl\s+AF-rProtein\s*A)",
        re.IGNORECASE,
    )),
    ("cex_resin", re.compile(
        r"((?:SP|CM)\s+Sepharose\s+\w+|Capto\s+S\s+\w+|Poros\s+\w*S|Fractogel\s+\w+\s*S)",
        re.IGNORECASE,
    )),
    ("aex_resin", re.compile(
        r"((?:Q|DEAE)\s+Sepharose\s+\w+|Capto\s+Q\s+\w+|Sartobind\s+Q|Poros\s+\w*Q)",
        re.IGNORECASE,
    )),
    ("viral_filter", re.compile(
        r"(Planova\s+\d+\w*|Viresolve\s+\w+|Virosart\s+\w+)",
        re.IGNORECASE,
    )),
    ("viral_inact_time", re.compile(
        r"(?:hold|incubat\w+|inactivat\w+)\s+(?:for\s+)?(\d+)\s*(?:\+/?-\s*\d+\s*)?(?:minutes?|min)",
        re.IGNORECASE,
    )),
]


def _extract_numerical_facts(text: str, cls_id: str, section_id: str) -> list[dict]:
    """Extract numerical value+unit pairs from text."""
    facts = []
    for m in _NUM_WITH_UNIT.finditer(text):
        val_low = float(m.group(1))
        val_high = float(m.group(3)) if m.group(3) else None
        unit = m.group(4).lower()

        start = max(0, m.start() - 80)
        end = min(len(text), m.end() + 80)
        context = text[start:end].replace("\n", " ").strip()

        facts.append({
            "type": "numerical",
            "value": val_low,
            "value_high": val_high,
            "unit": unit,
            "raw": m.group(0).strip(),
            "context": context,
            "classification_id": cls_id,
            "section_id": section_id,
        })

    for m in _PH_PATTERN.finditer(text):
        val_low = float(m.group(1))
        val_high = float(m.group(2)) if m.group(2) else None
        start = max(0, m.start() - 80)
        end = min(len(text), m.end() + 80)
        context = text[start:end].replace("\n", " ").strip()
        facts.append({
            "type": "ph",
            "value": val_low,
            "value_high": val_high,
            "unit": "pH",
            "raw": m.group(0).strip(),
            "context": context,
            "classification_id": cls_id,
            "section_id": section_id,
        })

    return facts


def _extract_terminology_usage(text: str, cls_id: str, section_id: str) -> list[dict]:
    """Find which terminology variants appear in text."""
    usages = []
    text_lower = text.lower()
    for group_name, variant_lists in _TERMINOLOGY_GROUPS:
        for variant_list in variant_lists:
            for term in variant_list:
                if term.lower() in text_lower:
                    usages.append({
                        "type": "terminology",
                        "group": group_name,
                        "term": term,
                        "variant_list": variant_list,
                        "classification_id": cls_id,
                        "section_id": section_id,
                    })
                    break
    return usages


def _extract_named_entities(text: str, cls_id: str, section_id: str) -> list[dict]:
    """Extract named entities for cross-section comparison."""
    entities = []
    for entity_type, pattern in _ENTITY_PATTERNS:
        for m in pattern.finditer(text):
            value = m.group(1).strip() if m.group(1) else m.group(0).strip()
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 60)
            context = text[start:end].replace("\n", " ").strip()
            entities.append({
                "type": "entity",
                "entity_type": entity_type,
                "value": value,
                "context": context,
                "classification_id": cls_id,
                "section_id": section_id,
            })
    return entities


# ---------------------------------------------------------------------------
# Comparators
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "in", "to",
    "and", "or", "for", "with", "at", "by", "on", "from", "as", "be",
    "this", "that", "which", "it", "not", "no", "has", "have", "had",
    "its", "each", "per", "should", "shall", "must", "may", "can",
    "will", "been", "being", "than", "also", "into", "between",
}


def _contexts_related(ctx_a: str, ctx_b: str) -> bool:
    """Heuristic: do two contexts describe the same parameter?"""
    words_a = {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", ctx_a)} - _STOP_WORDS
    words_b = {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", ctx_b)} - _STOP_WORDS
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) >= 2


def _values_conflict(a: dict, b: dict) -> dict | None:
    """Check if two numerical facts contradict each other."""
    if a["value"] == b["value"] and a.get("value_high") == b.get("value_high"):
        return None

    # Both single values
    if a.get("value_high") is None and b.get("value_high") is None:
        return {
            "category": "numerical_contradiction",
            "description": (
                f"Value mismatch: {a['raw']} (section {a['section_id']}) "
                f"vs. {b['raw']} (section {b['section_id']})."
            ),
        }

    # One range, one point
    range_fact = a if a.get("value_high") is not None else b
    point_fact = b if a.get("value_high") is not None else a
    if range_fact.get("value_high") is not None and point_fact.get("value_high") is None:
        low, high = range_fact["value"], range_fact["value_high"]
        val = point_fact["value"]
        if val < low or val > high:
            return {
                "category": "value_range_conflict",
                "description": (
                    f"Value {point_fact['raw']} (section {point_fact['section_id']}) "
                    f"falls outside range {range_fact['raw']} "
                    f"(section {range_fact['section_id']})."
                ),
            }

    # Both ranges — non-overlapping
    if a.get("value_high") is not None and b.get("value_high") is not None:
        if a["value_high"] < b["value"] or b["value_high"] < a["value"]:
            return {
                "category": "value_range_conflict",
                "description": (
                    f"Non-overlapping ranges: {a['raw']} (section {a['section_id']}) "
                    f"vs. {b['raw']} (section {b['section_id']})."
                ),
            }

    return None


def _numerical_severity(a: dict, b: dict, unit: str) -> str:
    """Assign severity based on parameter type."""
    safety_units = {"mg/ml", "mg/l", "g/l", "iu/ml", "eu/ml", "eu/mg", "cfu/ml"}
    safety_contexts = {"dose", "potency", "endotoxin", "bioburden", "sterility", "safety"}
    combined_context = (a["context"] + " " + b["context"]).lower()

    if unit.lower() in safety_units:
        for term in safety_contexts:
            if term in combined_context:
                return "error"
        return "warning"

    if a["type"] in ("ph",) or unit.lower() in ("°c", "°f"):
        return "warning"

    return "warning"


def _mismatch_confidence(a: dict, b: dict) -> dict:
    """Confidence in a numerical mismatch finding."""
    words_a = {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", a["context"])}
    words_b = {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", b["context"])}
    overlap = len(words_a & words_b)
    total = max(len(words_a | words_b), 1)
    raw_score = min(0.5 + (overlap / total) * 0.5, 0.95)

    if raw_score >= 0.80:
        qualifier = "high"
    elif raw_score >= 0.55:
        qualifier = "medium"
    else:
        qualifier = "low"

    return {
        "score": round(raw_score, 2),
        "qualifier": qualifier,
        "basis": f"Context overlap: {overlap} shared terms out of {total} total.",
    }


def _compare_numerical_facts(facts: list[dict]) -> list[dict]:
    """Find numerical contradictions across sections."""
    findings = []
    by_unit: dict[str, list[dict]] = {}
    for f in facts:
        by_unit.setdefault(f["unit"], []).append(f)

    for unit, group in by_unit.items():
        cross_section_pairs = [
            (a, b) for a, b in combinations(group, 2)
            if a["section_id"] != b["section_id"]
        ]
        for a, b in cross_section_pairs:
            if not _contexts_related(a["context"], b["context"]):
                continue
            mismatch = _values_conflict(a, b)
            if mismatch:
                severity = _numerical_severity(a, b, unit)
                findings.append({
                    "category": mismatch["category"],
                    "severity": severity,
                    "description": mismatch["description"],
                    "value_a": a["raw"],
                    "value_b": b["raw"],
                    "section_a": a["section_id"],
                    "section_b": b["section_id"],
                    "confidence": _mismatch_confidence(a, b),
                })
    return findings


def _compare_terminology(usages: list[dict]) -> list[dict]:
    """Find terminology inconsistencies across sections."""
    findings = []
    by_group: dict[str, list[dict]] = {}
    for u in usages:
        by_group.setdefault(u["group"], []).append(u)

    for group_name, group_usages in by_group.items():
        section_terms: dict[str, set[str]] = {}
        for u in group_usages:
            section_terms.setdefault(u["section_id"], set()).add(u["term"])

        if len(section_terms) < 2:
            continue

        variant_lists_by_section: dict[str, set[tuple[str, ...]]] = {}
        for u in group_usages:
            vl = tuple(sorted(u["variant_list"]))
            variant_lists_by_section.setdefault(u["section_id"], set()).add(vl)

        all_variant_lists: set[tuple[str, ...]] = set()
        for vls in variant_lists_by_section.values():
            all_variant_lists.update(vls)

        if len(all_variant_lists) <= 1:
            continue

        all_terms: set[str] = set()
        for terms in section_terms.values():
            all_terms.update(terms)

        sections = sorted(section_terms.keys())
        if len(sections) >= 2:
            findings.append({
                "category": "terminology_mismatch",
                "severity": "info",
                "description": (
                    f"Terminology inconsistency for {group_name.replace('_', ' ')}: "
                    f"sections {' and '.join(sections)} use different terms "
                    f"({', '.join(sorted(all_terms))})."
                ),
                "value_a": sorted(section_terms[sections[0]])[0],
                "value_b": sorted(section_terms[sections[1]])[0],
                "section_a": sections[0],
                "section_b": sections[1],
                "confidence": {
                    "score": 0.70,
                    "qualifier": "medium",
                    "basis": "Different synonym groups detected across sections.",
                },
            })
    return findings


def _compare_named_entities(entities: list[dict]) -> list[dict]:
    """Find entity-level contradictions across sections."""
    findings = []
    by_type: dict[str, list[dict]] = {}
    for e in entities:
        by_type.setdefault(e["entity_type"], []).append(e)

    for entity_type, group in by_type.items():
        section_values: dict[str, set[str]] = {}
        for e in group:
            section_values.setdefault(e["section_id"], set()).add(e["value"].lower().strip())

        all_values: set[str] = set()
        for vals in section_values.values():
            all_values.update(vals)

        if len(all_values) <= 1 or len(section_values) < 2:
            continue

        sections = sorted(section_values.keys())
        severity = "warning"
        if entity_type in ("viral_inact_time", "viral_filter"):
            severity = "error"

        findings.append({
            "category": "entity_contradiction",
            "severity": severity,
            "description": (
                f"Entity mismatch for {entity_type.replace('_', ' ')}: "
                f"different values across sections ({', '.join(sorted(all_values))})."
            ),
            "value_a": sorted(section_values[sections[0]])[0],
            "value_b": sorted(section_values[sections[1]])[0],
            "section_a": sections[0],
            "section_b": sections[1],
            "confidence": {
                "score": 0.85,
                "qualifier": "high",
                "basis": f"Distinct {entity_type.replace('_', ' ')} names differ between sections.",
            },
        })
    return findings


def _deduplicate_findings(findings: list[dict]) -> list[dict]:
    """Remove near-duplicate findings."""
    seen: set[str] = set()
    deduped = []
    for f in findings:
        key = f"{f['category']}|{f.get('section_a', '')}|{f.get('section_b', '')}"
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return deduped


def _suggest_resolution(finding: dict) -> str | None:
    """Generate a brief suggested resolution."""
    cat = finding.get("category", "")
    if cat == "numerical_contradiction":
        return "Verify which value is correct; update the inconsistent section."
    elif cat == "value_range_conflict":
        return "Confirm whether range and point value describe the same parameter."
    elif cat == "terminology_mismatch":
        return "Standardize terminology across sections."
    elif cat == "entity_contradiction":
        return "Verify which named entity is correct; document any process changes."
    return None


# ---------------------------------------------------------------------------
# Normalisation: accept either classification dicts or (section, text) pairs
# ---------------------------------------------------------------------------

def _normalise_input(sections) -> list[dict]:
    """Convert input to list of classification-style dicts.

    Accepts:
        - list of dicts with section_id + content_full/content_preview
        - list of (section_name, text_content) tuples
    """
    normalised = []
    for i, item in enumerate(sections):
        if isinstance(item, dict):
            normalised.append(item)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            normalised.append({
                "classification_id": f"input-{i+1:03d}",
                "section_id": item[0],
                "content_full": item[1],
            })
        else:
            raise ValueError(
                f"Expected dict or (section_name, text) tuple, got {type(item)}"
            )
    return normalised


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_consistency(sections: list) -> dict:
    """Check for internal consistency issues across classified sections.

    Args:
        sections: list of classification dicts (from classify_sections)
                  OR list of (section_name, text_content) tuples.

    Returns:
        Dict with:
            consistency_status: "pass" | "findings_present" | "insufficient_data"
            consistency_flags: list of conflict dicts, each with
                value_a, value_b, section_a, section_b, severity, confidence
            consistency_notes: free-text summary
            confidence: overall confidence object
    """
    classifications = _normalise_input(sections or [])

    classified = [
        c for c in classifications
        if c.get("section_id") not in (None, "UNCLASSIFIED")
    ]

    if len(classified) < 2:
        return {
            "consistency_status": "insufficient_data",
            "consistency_flags": [],
            "consistency_notes": (
                "Fewer than 2 classified sections available. "
                "Cross-section consistency checking requires at least 2 sections."
            ),
            "confidence": {
                "score": None,
                "qualifier": "unknown",
                "basis": "Insufficient data to perform consistency analysis.",
            },
        }

    # --- Extract comparable facts ---
    all_numerical: list[dict] = []
    all_terminology: list[dict] = []
    all_entities: list[dict] = []

    for cls in classified:
        text = cls.get("content_full", cls.get("content_preview", ""))
        cls_id = cls.get("classification_id", "unknown")
        section_id = cls.get("section_id", "unknown")

        all_numerical.extend(_extract_numerical_facts(text, cls_id, section_id))
        all_terminology.extend(_extract_terminology_usage(text, cls_id, section_id))
        all_entities.extend(_extract_named_entities(text, cls_id, section_id))

    # --- Run comparisons ---
    raw_findings: list[dict] = []
    raw_findings.extend(_compare_numerical_facts(all_numerical))
    raw_findings.extend(_compare_terminology(all_terminology))
    raw_findings.extend(_compare_named_entities(all_entities))

    # --- Deduplicate and enrich ---
    findings = _deduplicate_findings(raw_findings)
    for i, f in enumerate(findings, 1):
        f["finding_id"] = f"cf-{i:03d}"
        if "suggested_resolution" not in f:
            f["suggested_resolution"] = _suggest_resolution(f)

    # --- Status ---
    if not findings:
        status = "pass"
        notes = "No cross-section inconsistencies detected."
    else:
        status = "findings_present"
        sev_counts: dict[str, int] = {}
        for f in findings:
            sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1
        parts = [f"{count} {sev}" for sev, count in sorted(sev_counts.items())]
        notes = f"{len(findings)} consistency finding(s): {', '.join(parts)}."

    # --- Overall confidence ---
    if findings:
        scores = [
            f["confidence"]["score"]
            for f in findings
            if f["confidence"]["score"] is not None
        ]
        avg_score = round(sum(scores) / len(scores), 2) if scores else 0.5
    else:
        avg_score = 0.85

    overall_qualifier = (
        "high" if avg_score >= 0.75
        else "medium" if avg_score >= 0.50
        else "low"
    )

    sections_analyzed = len(set(c["section_id"] for c in classified))

    return {
        "consistency_status": status,
        "consistency_flags": findings,
        "consistency_notes": notes,
        "confidence": {
            "score": avg_score,
            "qualifier": overall_qualifier,
            "basis": (
                f"Analyzed {sections_analyzed} sections. "
                f"Extracted {len(all_numerical)} numerical facts, "
                f"{len(all_terminology)} terminology usages, "
                f"{len(all_entities)} named entities. "
                f"{len(findings)} finding(s)."
            ),
        },
    }
