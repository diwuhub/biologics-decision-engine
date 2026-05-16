"""Checklist Reviewer — regulatory checklist checking for CTD Module 3 submissions.

Compares classified sections against 50+ expected-item checklists per the
CTD structure. Returns a gap report with present/missing/partial items
and compliance severity ratings.

Input:  list of classification dicts (from section_classifier.classify_sections)
Output: gap report dict with section_results, compliance_score, missing counts
"""

import re

# ---------------------------------------------------------------------------
# Expected-item checklists per CTD subsection
# Each item: (item_label, keyword_evidence_patterns)
# ---------------------------------------------------------------------------
_CHECKLISTS: dict[str, list[tuple[str, list[str]]]] = {
    # --- Tier 1: Full checklists ---
    "S.1.1": [
        ("INN or nonproprietary name", ["INN", "nonproprietary", "international"]),
        ("Chemical or code name", ["chemical name", "code name", "USAN"]),
        ("CAS registry number", ["CAS", "registry"]),
        ("Compendial name", ["compendial", "pharmacopoeia"]),
    ],
    "S.1.2": [
        ("Primary amino acid sequence", ["amino acid", "sequence", "residues"]),
        ("Molecular weight", ["molecular weight", "kDa", "dalton"]),
        ("Disulfide bond pattern", ["disulfide", "cysteine"]),
        ("Glycosylation description", ["glycosylation", "glycan", "N-linked"]),
        ("Higher-order structure characterization", ["higher-order", "secondary structure", "tertiary"]),
    ],
    "S.1.3": [
        ("Isoelectric point or charge properties", ["isoelectric", "pI", "charge"]),
        ("Extinction coefficient", ["extinction coefficient", "absorptivity"]),
        ("Biological activity description", ["biological activity", "potency", "binding"]),
    ],
    "S.2.2": [
        ("Process flow diagram or narrative",
         ["manufacturing process", "unit operation", "process flow", "process consists of"]),
        ("Cell culture step description",
         ["cell culture", "bioreactor", "fed-batch", "perfusion", "seed train"]),
        ("Harvest and clarification method",
         ["harvest", "clarification", "depth filtration", "centrifugation"]),
        ("Purification steps",
         ["chromatography", "Protein A", "ion exchange", "purification"]),
        ("Viral clearance steps",
         ["viral inactivation", "viral clearance", "nanofiltration", "low pH"]),
        ("In-process controls",
         ["in-process control", "in-process test", "process parameter"]),
        ("Hold times or process timing",
         ["hold time", "process time", "duration"]),
    ],
    "S.2.3": [
        ("Cell bank system description",
         ["cell bank", "master cell bank", "MCB", "working cell bank", "WCB"]),
        ("Cell culture media description",
         ["culture media", "media component", "chemically defined"]),
        ("Raw material sourcing and qualification",
         ["raw material", "qualified supplier", "sourced from"]),
        ("Animal-derived component status",
         ["animal-derived", "animal-origin", "animal-free", "bovine"]),
        ("Chromatography resin qualification",
         ["resin", "resin lifetime", "reuse", "column"]),
    ],
    "S.3.1": [
        ("Primary structure confirmation",
         ["primary structure", "peptide mapping", "amino acid sequence"]),
        ("Post-translational modification characterization",
         ["post-translational", "glycan analysis", "glycosylation", "glycan profile"]),
        ("Higher-order structure analysis",
         ["CD", "circular dichroism", "DSC", "differential scanning"]),
        ("Biological activity characterization",
         ["biological activity", "cell-based assay", "binding assay", "potency"]),
        ("Molecular weight confirmation",
         ["mass spectrometry", "intact mass", "molecular weight"]),
    ],
    "S.3.2": [
        ("Process-related impurity identification",
         ["host cell protein", "HCP", "residual DNA", "Protein A"]),
        ("Product-related substance characterization",
         ["aggregate", "fragment", "charge variant", "oxidized"]),
        ("Impurity clearance data",
         ["clearance", "removal", "reduction"]),
    ],
    "S.4.1": [
        ("Specification table with test names",
         ["specification", "test name", "assay"]),
        ("Method references for each test",
         ["method", "analytical procedure"]),
        ("Acceptance criteria for each test",
         ["acceptance criteria", "NMT", "NLT", "limit"]),
        ("Release vs shelf-life specification distinction",
         ["release", "shelf-life", "shelf life"]),
    ],
    "S.4.4": [
        ("Batch results table with lot numbers",
         ["batch", "lot number", "batch number"]),
        ("Manufacturing date for each batch",
         ["manufacturing date", "date of manufacture"]),
        ("Test results for each specification test",
         ["result", "tested", "certificate of analysis"]),
    ],
    "S.7.1": [
        ("Stability program design summary",
         ["stability program", "stability study", "stability protocol"]),
        ("Storage conditions tested",
         ["storage condition", "25.*60", "accelerated", "long-term", "5.*3"]),
        ("Shelf-life or re-test period conclusion",
         ["shelf life", "shelf-life", "re-test period", "expiry"]),
    ],
    "S.7.3": [
        ("Tabulated stability data",
         ["stability data", "stability result", "stability table"]),
        ("Time points reported",
         ["month", "time point", "timepoint"]),
        ("At least two storage conditions",
         ["25.*60", "accelerated", "long-term", "5.*3", "40.*75"]),
    ],
    "P.1": [
        ("Dosage form description",
         ["dosage form", "solution for injection", "lyophilized", "suspension"]),
        ("Quantitative composition table",
         ["composition", "excipient", "mg/mL", "mg per"]),
        ("Route of administration",
         ["subcutaneous", "intravenous", "intramuscular", "route"]),
    ],
    "P.7": [
        ("Primary packaging component description",
         ["vial", "syringe", "pre-filled", "stopper", "closure"]),
        ("Material specifications",
         ["borosilicate", "glass", "bromobutyl", "rubber", "Type I"]),
        ("Extractables/leachables consideration",
         ["extractable", "leachable"]),
    ],

    # --- Tier 2: Basic checklists ---
    "S.2.1": [
        ("Manufacturer name and address", ["manufacturer", "address", "site"]),
        ("Manufacturing responsibilities", ["responsible for", "role", "contract"]),
    ],
    "S.2.4": [
        ("Critical process parameters identified", ["critical process parameter", "CPP"]),
        ("Intermediate specifications", ["intermediate", "in-process"]),
    ],
    "S.2.5": [
        ("Validation study described", ["validation", "validated", "PPQ"]),
        ("Consistency data presented", ["consistency", "batch-to-batch"]),
    ],
    "S.2.6": [
        ("Development history narrative", ["development", "history", "evolution"]),
        ("Process change description", ["process change", "comparability", "scale-up"]),
    ],
    "S.4.2": [
        ("Method descriptions provided", ["method", "procedure", "analytical"]),
    ],
    "S.4.3": [
        ("Validation parameters reported", ["accuracy", "precision", "linearity", "specificity"]),
    ],
    "S.4.5": [
        ("Justification rationale provided", ["justification", "rationale", "based on"]),
    ],
    "P.2": [
        ("Formulation development rationale", ["formulation", "development", "rationale"]),
        ("Excipient selection justification", ["excipient selection", "selected because"]),
    ],
    "P.3": [
        ("Drug product process description", ["fill", "finish", "aseptic", "manufacturing"]),
    ],

    # --- Tier 3: Presence only ---
    "S.5": [
        ("Reference standard information present", ["reference standard"]),
    ],
    "S.6": [
        ("Container closure information present", ["container", "closure", "packaging"]),
    ],
}

# Compliance severity per checklist item (default: minor)
_COMPLIANCE_SEVERITY: dict[str, dict[str, str]] = {
    "S.2.2": {
        "Process flow diagram or narrative": "major",
        "Cell culture step description": "major",
        "Purification steps": "major",
        "Viral clearance steps": "critical",
        "In-process controls": "major",
    },
    "S.2.3": {
        "Cell bank system description": "critical",
        "Animal-derived component status": "major",
    },
    "S.3.2": {
        "Process-related impurity identification": "critical",
    },
    "S.4.1": {
        "Specification table with test names": "critical",
        "Acceptance criteria for each test": "critical",
    },
    "S.7.1": {
        "Shelf-life or re-test period conclusion": "major",
    },
    "P.1": {
        "Quantitative composition table": "critical",
    },
}

_SECTION_HEADINGS: dict[str, str] = {
    "S.1.1": "Nomenclature",
    "S.1.2": "Structure",
    "S.1.3": "General Properties",
    "S.2.1": "Manufacturer(s)",
    "S.2.2": "Description of Manufacturing Process and Process Controls",
    "S.2.3": "Control of Materials",
    "S.2.4": "Controls of Critical Steps and Intermediates",
    "S.2.5": "Process Validation and/or Evaluation",
    "S.2.6": "Manufacturing Process Development",
    "S.3.1": "Elucidation of Structure and Other Characteristics",
    "S.3.2": "Impurities",
    "S.4.1": "Specification",
    "S.4.2": "Analytical Procedures",
    "S.4.3": "Validation of Analytical Procedures",
    "S.4.4": "Batch Analyses",
    "S.4.5": "Justification of Specification",
    "S.5": "Reference Standards or Materials",
    "S.6": "Container Closure System",
    "S.7.1": "Stability Summary and Conclusions",
    "S.7.2": "Post-approval Stability Protocol and Stability Commitment",
    "S.7.3": "Stability Data",
    "P.1": "Description and Composition of the Drug Product",
    "P.2": "Pharmaceutical Development",
    "P.3": "Manufacture (Drug Product)",
    "P.7": "Container Closure System (Drug Product)",
}


def _min_qualifier(a: str, b: str) -> str:
    """Return the lower of two confidence qualifiers."""
    order = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    return a if order.get(a, 0) <= order.get(b, 0) else b


def _search_evidence(text: str, patterns: list[str]) -> tuple[bool, list[str]]:
    """Search text for keyword patterns."""
    text_lower = text.lower()
    matched = []
    for pattern in patterns:
        if ".*" in pattern:
            if re.search(pattern, text_lower):
                matched.append(pattern)
        elif pattern.lower() in text_lower:
            matched.append(pattern)
    return len(matched) > 0, matched


def _find_section_content(classifications: list[dict], section_id: str) -> list[dict]:
    """Find all classification entries matching a section ID (or parent)."""
    results = []
    for cls in classifications:
        cls_sid = cls.get("section_id", "")
        if cls_sid == section_id or cls_sid.startswith(section_id + "."):
            results.append(cls)
        elif cls_sid and section_id.startswith(cls_sid):
            results.append(cls)
    return results


def _extract_evidence_snippet(text: str, matched_keywords: list[str], max_len: int = 200) -> str:
    """Extract a text snippet around the first matched keyword."""
    text_lower = text.lower()
    for kw in matched_keywords:
        pos = text_lower.find(kw.lower())
        if pos >= 0:
            start = max(0, pos - 40)
            end = min(len(text), pos + len(kw) + max_len - 40)
            snippet = text[start:end].strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(text):
                snippet = snippet + "..."
            return snippet
    return ""


def review_checklist(classifications: list[dict]) -> dict:
    """Detect likely missing items based on classified sections.

    Args:
        classifications: list of classification dicts from classify_sections.

    Returns:
        Gap report dict with:
            sections_analyzed, sections_with_gaps, compliance_score,
            critical_missing, major_missing, section_results (detailed).
    """
    if not classifications:
        return {
            "disclaimer": (
                "Expected items are based on typical submission patterns, "
                "not regulatory requirements."
            ),
            "sections_analyzed": 0,
            "sections_with_gaps": 0,
            "compliance_score": 0,
            "critical_missing": 0,
            "major_missing": 0,
            "section_results": [],
        }

    # Determine which sections are present
    present_ids: set[str] = set()
    for cls in classifications:
        sid = cls.get("section_id", "")
        if sid != "UNCLASSIFIED":
            present_ids.add(sid)
            parts = sid.split(".")
            for i in range(1, len(parts)):
                present_ids.add(".".join(parts[:i]))

    # Determine relevant checklists
    relevant_checklists: dict[str, list[tuple[str, list[str]]]] = {}
    for checklist_sid, items in _CHECKLISTS.items():
        parent = checklist_sid.rsplit(".", 1)[0] if "." in checklist_sid else checklist_sid
        if (checklist_sid in present_ids
                or parent in present_ids
                or any(pid.startswith(checklist_sid) for pid in present_ids)):
            relevant_checklists[checklist_sid] = items

    section_results = []
    total_present = 0
    total_items = 0
    sections_with_gaps = 0

    for section_id, expected_items in sorted(relevant_checklists.items()):
        section_content = _find_section_content(classifications, section_id)

        combined_text = " ".join(
            cls.get("content_full", cls.get("content_preview", ""))
            for cls in section_content
        )

        section_conf_low = any(
            cls.get("confidence", {}).get("qualifier") in ("low", "unknown")
            for cls in section_content
        )
        conf_ceiling = "medium" if section_conf_low else "high"

        checklist_items = []
        items_present = 0
        items_missing = 0
        items_partial = 0

        for item_idx, (item_label, patterns) in enumerate(expected_items, 1):
            item_id = f"gi-{section_id}-{item_idx}"

            if not combined_text.strip():
                status = "missing"
                items_missing += 1
                evidence = None
                item_conf_qualifier = _min_qualifier(conf_ceiling, "medium")
            else:
                found, matched = _search_evidence(combined_text, patterns)

                if found and len(matched) >= len(patterns) * 0.5:
                    status = "present"
                    items_present += 1
                    item_conf_score = min(0.70 + len(matched) * 0.1, 0.95)
                    item_conf_qualifier = _min_qualifier(
                        conf_ceiling,
                        "high" if item_conf_score >= 0.80 else "medium"
                    )
                    evidence = f"Matched {len(matched)} of {len(patterns)} patterns."
                elif found:
                    status = "partial"
                    items_partial += 1
                    item_conf_qualifier = _min_qualifier(conf_ceiling, "medium")
                    evidence = f"Partial: {len(matched)} of {len(patterns)} patterns."
                else:
                    status = "missing"
                    items_missing += 1
                    evidence = None
                    item_conf_qualifier = _min_qualifier(conf_ceiling, "medium")

            sev_map = _COMPLIANCE_SEVERITY.get(section_id, {})
            compliance_severity = sev_map.get(item_label, "minor")

            checklist_items.append({
                "item_id": item_id,
                "expected_item": item_label,
                "status": status,
                "evidence": evidence,
                "compliance_severity": compliance_severity,
            })

        total_items += len(expected_items)
        total_present += items_present

        if items_missing > 0 or items_partial > 0:
            coverage_status = "gaps_found"
            sections_with_gaps += 1
        else:
            coverage_status = "complete"

        heading = _SECTION_HEADINGS.get(section_id, section_id)

        section_results.append({
            "section_id": section_id,
            "section_heading": heading,
            "coverage_status": coverage_status,
            "items_checked": len(expected_items),
            "items_present": items_present,
            "items_missing": items_missing,
            "items_partial": items_partial,
            "checklist_items": checklist_items,
        })

    compliance_score = round(total_present / total_items * 100, 1) if total_items else 0

    critical_missing = 0
    major_missing = 0
    for sr in section_results:
        for item in sr.get("checklist_items", []):
            if item["status"] == "missing":
                sev = item.get("compliance_severity", "minor")
                if sev == "critical":
                    critical_missing += 1
                elif sev == "major":
                    major_missing += 1

    return {
        "disclaimer": (
            "Expected items are based on typical submission patterns, "
            "not regulatory requirements."
        ),
        "sections_analyzed": len(section_results),
        "sections_with_gaps": sections_with_gaps,
        "compliance_score": compliance_score,
        "critical_missing": critical_missing,
        "major_missing": major_missing,
        "section_results": section_results,
    }
