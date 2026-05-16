"""Section Classifier — classify raw CMC / Module 3 text into CTD section categories.

Uses keyword/pattern matching against a 40+ entry section ontology covering
S.1-S.7 (Drug Substance) and P.1-P.8 (Drug Product).

Input:  raw text (str)
Output: list of classification dicts, each with:
    - section_id: CTD section identifier (e.g. "S.2.2", "P.1")
    - section_heading: human-readable heading
    - confidence: {score, qualifier, basis}
    - content_preview: first 300 chars
    - content_full: full block text
"""

import os
import re
import sys

# Allow importing shared utilities from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from utils.confidence import compute_confidence as _shared_compute_confidence  # noqa: E402

# ---------------------------------------------------------------------------
# Section ontology: keyword patterns per CTD section
# Each entry: (section_id, heading, keywords/phrases, weight_boost)
# ---------------------------------------------------------------------------
_SECTION_PATTERNS: list[tuple[str, str, list[str], float]] = [
    # --- Drug Substance: General Information ---
    ("S.1.1", "Nomenclature",
     ["INN", "USAN", "CAS number", "compendial name", "chemical name",
      "also known as", "code name", "nonproprietary name", "registry number"],
     0.0),
    ("S.1.2", "Structure",
     ["amino acid residues", "amino acid sequence", "disulfide bond",
      "glycosylation", "molecular weight", "molecular formula", "kDa",
      "primary structure", "higher-order structure", "structural diagram",
      "light chain", "heavy chain", "IgG"],
     0.0),
    ("S.1.3", "General Properties",
     ["isoelectric point", "extinction coefficient", "solubility",
      "biological activity", "binding affinity", "potency", "pI",
      "EC50", "IC50", "immunochemical"],
     0.0),

    # --- Drug Substance: Manufacture ---
    ("S.2.1", "Manufacturer(s)",
     ["manufacturer", "manufacturing site", "site address",
      "responsible for", "contract manufacturer", "CMO"],
     0.0),
    ("S.2.2", "Description of Manufacturing Process and Process Controls",
     ["manufacturing process", "cell culture", "bioreactor", "harvest",
      "purification", "chromatography", "filtration", "process flow",
      "unit operation", "in-process control", "fed-batch", "perfusion",
      "depth filtration", "Protein A", "viral inactivation",
      "anion exchange", "cation exchange", "nanofiltration",
      "ultrafiltration", "diafiltration", "hold time", "clarification"],
     0.05),
    ("S.2.3", "Control of Materials",
     ["raw material", "cell bank", "master cell bank", "working cell bank",
      "MCB", "WCB", "cell substrate", "culture media", "reagent",
      "solvent", "certificate of analysis", "qualified supplier",
      "animal-derived", "chemically defined", "resin", "membrane",
      "chromatography resin", "single-use"],
     0.0),
    ("S.2.4", "Controls of Critical Steps and Intermediates",
     ["critical process parameter", "CPP", "intermediate specification",
      "in-process control", "critical step", "intermediate testing",
      "acceptance criteria at intermediate", "bioreactor temperature",
      "harvest viability", "elution pH", "hold time", "in-process",
      "process parameter", "operating range", "set point",
      "process control strategy"],
     0.05),
    ("S.2.5", "Process Validation and/or Evaluation",
     ["process validation", "validation study", "consistency batch",
      "process capability", "hold time study", "demonstrated",
      "batch-to-batch", "PPQ", "process performance qualification"],
     0.0),
    ("S.2.6", "Manufacturing Process Development",
     ["process development", "process change", "comparability",
      "scale-up", "development history", "process evolution",
      "clinical phase", "manufacturing change"],
     0.0),

    # --- Drug Substance: Characterisation ---
    ("S.3.1", "Elucidation of Structure and Other Characteristics",
     ["characterization", "peptide mapping", "mass spectrometry",
      "glycan analysis", "charge variant", "circular dichroism", "CD",
      "DSC", "differential scanning calorimetry", "AUC",
      "analytical ultracentrifugation", "post-translational modification",
      "N-linked glycan", "primary structure confirmation"],
     0.0),
    ("S.3.2", "Impurities",
     ["host cell protein", "HCP", "residual DNA", "Protein A leaching",
      "process-related impurity", "product-related substance",
      "aggregate", "fragment", "charge variant", "oxidized species",
      "clearance", "impurity profile", "ELISA", "qPCR",
      "product-related impurity"],
     0.0),

    # --- Drug Substance: Control ---
    ("S.4.1", "Specification",
     ["specification", "acceptance criteria", "NMT", "NLT",
      "release specification", "shelf-life specification",
      "test.*method.*acceptance", "specification table"],
     0.05),
    ("S.4.2", "Analytical Procedures",
     ["analytical procedure", "method description", "the method involves",
      "sample preparation", "instrument", "HPLC method", "assay method",
      "analytical method"],
     0.0),
    ("S.4.3", "Validation of Analytical Procedures",
     ["method validation", "accuracy", "precision", "specificity",
      "linearity", "range", "LOD", "LOQ", "robustness", "%RSD",
      "ICH Q2", "limit of detection", "limit of quantitation"],
     0.0),
    ("S.4.4", "Batch Analyses",
     ["batch analysis", "batch result", "lot number", "certificate of analysis",
      "batch number", "manufacturing date", "tested batch"],
     0.0),
    ("S.4.5", "Justification of Specification",
     ["justification", "specification rationale", "based on.*clinical",
      "manufacturing history", "manufacturing capability"],
     0.0),

    # --- Drug Substance: Other ---
    ("S.5", "Reference Standards or Materials",
     ["reference standard", "qualified against", "primary standard",
      "working standard", "reference material"],
     0.0),
    ("S.6", "Container Closure System",
     ["container closure", "primary container", "polyethylene",
      "stainless steel", "extractable", "leachable",
      "drug substance storage", "container material"],
     0.0),
    ("S.7.1", "Stability Summary and Conclusions",
     ["stability summary", "shelf life", "re-test period",
      "storage condition", "stability program", "stability conclusion"],
     0.0),
    ("S.7.2", "Post-approval Stability Protocol and Stability Commitment",
     ["post-approval stability", "stability commitment", "annual batch",
      "ongoing stability"],
     0.0),
    ("S.7.3", "Stability Data",
     ["stability data", "stability result", "months", "25.*60.*RH",
      "accelerated", "long-term", "time point", "stability table"],
     0.0),

    # --- Drug Product ---
    ("P.1", "Description and Composition of the Drug Product",
     ["dosage form", "composition", "excipient", "mg/mL",
      "pre-filled syringe", "solution for injection", "lyophilized",
      "qualitative.*quantitative composition", "formulation composition",
      "polysorbate", "histidine", "sucrose", "trehalose",
      "water for injection"],
     0.0),
    ("P.2", "Pharmaceutical Development",
     ["pharmaceutical development", "formulation development",
      "excipient selection", "formulation rationale", "forced degradation",
      "compatibility study", "development study", "selected because",
      "photostability"],
     0.0),
    ("P.3", "Manufacture (Drug Product)",
     ["drug product manufacturing", "fill.*finish", "aseptic",
      "lyophilization", "drug product process", "filling",
      "visual inspection", "stoppering"],
     0.0),
    ("P.4", "Control of Excipients",
     ["excipient specification", "excipient testing", "compendial excipient",
      "excipient.*acceptance criteria", "novel excipient",
      "excipient.*animal origin", "TSE", "BSE"],
     0.0),
    ("P.5", "Control of Drug Product",
     ["drug product specification", "drug product release",
      "drug product.*acceptance criteria", "drug product.*analytical",
      "drug product.*batch analysis", "finished product specification"],
     0.0),
    ("P.6", "Reference Standards or Materials (Drug Product)",
     ["drug product reference standard"],
     0.0),
    ("P.7", "Container Closure System (Drug Product)",
     ["primary packaging", "vial", "stopper", "pre-filled syringe",
      "bromobutyl", "borosilicate", "needle shield", "plunger",
      "extractable.*leachable", "container.*drug product",
      "closure integrity"],
     0.0),
    ("P.8", "Stability (Drug Product)",
     ["drug product stability", "finished product stability",
      "formulated product stability"],
     0.0),

    # --- General / umbrella patterns (low specificity, used as fallback) ---
    ("S.2", "Drug Substance Manufacture (General)",
     ["drug substance", "manufacturing", "produced using", "manufactured",
      "production process", "CHO cell", "cell line", "purification",
      "downstream processing"],
     -0.10),

    # --- Other Module 3 ---
    ("A.1", "Facilities and Equipment",
     ["facility", "manufacturing facility", "equipment", "clean room",
      "HVAC", "water system", "facility description"],
     0.0),
    ("A.2", "Adventitious Agents Safety Evaluation",
     ["adventitious agent", "viral safety", "TSE risk", "BSE risk",
      "virus clearance", "viral clearance study"],
     0.0),
]

_MIN_BLOCK_CHARS = 150


def _split_into_blocks(raw_content: str) -> list[dict]:
    """Split raw text into content blocks at heading boundaries or double newlines."""
    # Try heading-based splitting first
    parts = re.split(r"\n(?=(?:S|P)\.\d+(?:\.\d+)*\s|(?:3\.2\.)\S)", raw_content)
    used_heading_split = len(parts) > 1

    if not used_heading_split:
        parts = re.split(r"\n\s*\n", raw_content)

    offset = 0
    raw_blocks: list[dict] = []
    for i, part in enumerate(parts):
        text = part.strip()
        if not text:
            offset += len(part) + 1
            continue

        lines = text.split("\n", 1)
        heading = lines[0].strip() if len(lines) > 1 else None

        start = raw_content.find(text, offset)
        if start == -1:
            start = offset

        raw_blocks.append({
            "index": i,
            "text": text,
            "heading": heading,
            "start": start,
            "end": start + len(text),
        })
        offset = start + len(text)

    # Merge small blocks when paragraph-split
    if not used_heading_split and len(raw_blocks) > 2:
        merged: list[dict] = []
        accumulator = None
        for block in raw_blocks:
            if accumulator is None:
                accumulator = block.copy()
            elif len(accumulator["text"]) < _MIN_BLOCK_CHARS:
                accumulator["text"] = accumulator["text"] + "\n\n" + block["text"]
                accumulator["end"] = block["end"]
            else:
                merged.append(accumulator)
                accumulator = block.copy()
        if accumulator:
            if merged and len(accumulator["text"]) < _MIN_BLOCK_CHARS:
                merged[-1]["text"] = merged[-1]["text"] + "\n\n" + accumulator["text"]
                merged[-1]["end"] = accumulator["end"]
            else:
                merged.append(accumulator)
        for i, b in enumerate(merged):
            b["index"] = i
        blocks = merged
    else:
        blocks = raw_blocks

    return blocks


def _score_block(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    """Score a text block against a keyword list."""
    text_lower = text.lower()
    matched = []
    for kw in keywords:
        if ".*" in kw or "(" in kw:
            if re.search(kw, text_lower):
                matched.append(kw)
        elif kw.lower() in text_lower:
            matched.append(kw)
    return len(matched), matched


def _compute_confidence(
    match_count: int, total_keywords: int, weight_boost: float
) -> tuple[float, str]:
    """Compute confidence score and qualifier from match metrics.

    Delegates qualifier bucketing to the shared ``utils.confidence`` module.
    """
    if total_keywords == 0:
        return 0.0, "unknown"

    raw_score = min(match_count / max(total_keywords * 0.3, 1), 1.0)
    score = round(min(raw_score + weight_boost, 1.0), 2)

    result = _shared_compute_confidence(score)
    return result["score"], result["qualifier"]


def classify_sections(raw_content: str) -> list[dict]:
    """Classify raw text into Module 3 CTD section categories.

    Args:
        raw_content: raw text from a CMC / Module 3 document.

    Returns:
        List of classification dicts, each containing:
            classification_id, section_id, section_heading,
            confidence {score, qualifier, basis},
            content_preview (300 chars), content_full,
            alternative_sections (if any).
    """
    if not raw_content or not raw_content.strip():
        return []

    blocks = _split_into_blocks(raw_content)
    classifications: list[dict] = []

    for block in blocks:
        text = block["text"]
        if len(text.strip()) < 10:
            continue

        # Check if heading explicitly contains a section ID
        heading_section_id = None
        if block["heading"]:
            heading_match = re.match(
                r"^((?:S|P)\.\d+(?:\.\d+)*)", block["heading"]
            )
            if heading_match:
                heading_section_id = heading_match.group(1)

        # Score against all sections
        scores: list[tuple[str, str, float, str, list[str], int]] = []
        for section_id, heading, keywords, boost in _SECTION_PATTERNS:
            match_count, matched = _score_block(text, keywords)
            heading_boost = 0.0
            if heading_section_id and section_id.startswith(heading_section_id):
                heading_boost = 0.40
            if match_count > 0 or heading_boost > 0:
                effective_matches = max(match_count, 1) if heading_boost > 0 else match_count
                score, qualifier = _compute_confidence(
                    effective_matches, len(keywords), boost + heading_boost
                )
                scores.append(
                    (section_id, heading, score, qualifier, matched, match_count)
                )

        scores.sort(key=lambda x: (x[2], x[5]), reverse=True)

        cls_id = f"cls-{len(classifications) + 1:03d}"

        if scores:
            best = scores[0]
            section_id, section_heading = best[0], best[1]
            conf_score, conf_qualifier = best[2], best[3]
            matched_keywords = best[4]

            # Build alternatives
            alternatives = None
            if len(scores) > 1 and scores[1][2] >= 0.30:
                alternatives = []
                for alt in scores[1:3]:
                    alternatives.append({
                        "section_id": alt[0],
                        "section_heading": alt[1],
                        "rationale": f"Matched {alt[5]} keywords (score: {alt[2]:.2f})",
                    })

            # Confidence basis
            basis_str = f"Matched {best[5]} keywords for {section_id}."
            for sid, _, kws, _ in _SECTION_PATTERNS:
                if sid == section_id:
                    basis_str = (
                        f"Matched {best[5]} keywords from {section_id} pattern set "
                        f"({len(kws)} total). Top matches: "
                        + ", ".join(f"'{k}'" for k in matched_keywords[:3])
                        + "."
                    )
                    break
        else:
            section_id = "UNCLASSIFIED"
            section_heading = "Unclassified Content"
            conf_score = None
            conf_qualifier = "unknown"
            basis_str = (
                "Content does not match any recognized CTD Module 3 section "
                "keyword patterns."
            )
            alternatives = None

        entry = {
            "classification_id": cls_id,
            "source_location": {
                "paragraph_index": block["index"],
                "text_span": {"start": block["start"], "end": block["end"]},
                "heading": block["heading"],
            },
            "content_preview": text[:300],
            "content_full": text,
            "section_id": section_id,
            "section_heading": section_heading,
            "confidence": {
                "score": conf_score,
                "qualifier": conf_qualifier,
                "basis": basis_str,
            },
            "alternative_sections": alternatives,
        }
        classifications.append(entry)

    return classifications
