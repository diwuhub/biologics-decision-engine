"""
Gap Memo Pipeline Orchestrator (A-2).

Input CTD sections, detect gaps, predict reviewer questions, suggest remediation.
This is the second product entry point -- review readiness assessment.

Given a list of CTD section dicts, produces a structured GapMemo by composing:

  1. section_classifier   -- identify / validate CTD section labels
  2. consistency_checker  -- find numerical & terminology conflicts
  3. checklist_reviewer   -- detect missing checklist items
  4. question predictor   -- generate likely reviewer questions per gap
  5. remediation engine   -- suggest specific fixes per gap
  6. memo generator       -- structured output in reviewer-style format

Usage:
    from pipelines.gap_memo import generate_gap_memo
    memo = generate_gap_memo(sections, product_type="mAb", submission_type="BLA")
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from pipelines.gap_memo_schemas import (
    GapMemo,
    GapFinding,
    ConsistencyFlag,
    PredictedQuestion,
)

# Module imports -- existing CTD reviewer building blocks
from modules.ctd_reviewer.section_classifier import classify_sections
from modules.ctd_reviewer.consistency_checker import check_consistency
from modules.ctd_reviewer.checklist_reviewer import review_checklist


# ---------------------------------------------------------------------------
# ICH-aligned reviewer question templates (built-in; no external dependency)
# ---------------------------------------------------------------------------

_QUESTION_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    # key = checklist item label substring -> list of question templates
    "process flow": [
        {
            "question": "Please provide a detailed process flow diagram showing all unit operations, in-process controls, and critical process parameters.",
            "probability": 0.95,
            "approach": "Create a comprehensive process flow diagram with CPP/IPC annotations per ICH Q11.",
        },
    ],
    "cell culture": [
        {
            "question": "What is the cell culture duration, feeding strategy, and harvest criteria? Provide data demonstrating process consistency.",
            "probability": 0.90,
            "approach": "Summarize cell culture parameters with batch data from at least 3 consistency lots.",
        },
    ],
    "viral clearance": [
        {
            "question": "Please provide the viral clearance validation study results including log reduction values for each step.",
            "probability": 0.95,
            "approach": "Include tabulated LRV data per ICH Q5A(R2) with model virus panel results.",
        },
    ],
    "in-process control": [
        {
            "question": "What in-process controls and acceptance criteria are applied at each critical step?",
            "probability": 0.85,
            "approach": "Tabulate IPCs with justified acceptance criteria linked to CQAs per ICH Q11.",
        },
    ],
    "specification": [
        {
            "question": "Please justify the proposed acceptance criteria for each specification test, including the statistical basis.",
            "probability": 0.90,
            "approach": "Provide specification justification with batch history, capability analysis, and clinical relevance per ICH Q6B.",
        },
    ],
    "acceptance criteria": [
        {
            "question": "How were acceptance criteria derived? Provide the link between specifications and clinical experience.",
            "probability": 0.85,
            "approach": "Present a specification justification table referencing clinical lots and pharmacopeial requirements.",
        },
    ],
    "stability": [
        {
            "question": "Please provide the full stability dataset supporting the proposed shelf life, including accelerated and stressed conditions.",
            "probability": 0.90,
            "approach": "Present ICH Q5C-compliant stability data with statistical trend analysis.",
        },
    ],
    "impurity": [
        {
            "question": "What is the full impurity profile? Provide clearance data for all process-related impurities.",
            "probability": 0.90,
            "approach": "Tabulate all identified impurities with qualified methods and clearance/fate data.",
        },
    ],
    "cell bank": [
        {
            "question": "Provide characterization data for the Master Cell Bank and Working Cell Bank, including genetic stability assessment.",
            "probability": 0.90,
            "approach": "Include MCB/WCB characterization per ICH Q5B/Q5D with passage stability data.",
        },
    ],
    "animal-derived": [
        {
            "question": "Confirm the TSE/BSE risk assessment for all animal-derived materials used in manufacturing.",
            "probability": 0.85,
            "approach": "Provide a complete inventory of animal-derived materials with risk assessment per EMA/410/01 rev.3.",
        },
    ],
    "composition": [
        {
            "question": "Please provide the quantitative composition per unit dose and justify each excipient selection.",
            "probability": 0.90,
            "approach": "Include a quantitative composition table with excipient function and concentration justification.",
        },
    ],
    "container closure": [
        {
            "question": "Provide extractables/leachables data for the primary container closure system.",
            "probability": 0.85,
            "approach": "Present E&L study results per USP <1663>/<1664> with toxicological risk assessment.",
        },
    ],
    "reference standard": [
        {
            "question": "Describe the qualification and traceability of the primary reference standard.",
            "probability": 0.80,
            "approach": "Document reference standard qualification with characterization data and chain of custody.",
        },
    ],
    "validation": [
        {
            "question": "Provide the process validation protocol and results demonstrating process consistency.",
            "probability": 0.85,
            "approach": "Present PPQ data with pre-defined acceptance criteria and statistical evaluation per ICH Q11.",
        },
    ],
    "method validation": [
        {
            "question": "Provide validation summaries for all analytical methods used for release and stability testing.",
            "probability": 0.85,
            "approach": "Tabulate validation parameters (accuracy, precision, linearity, range) per ICH Q2(R2).",
        },
    ],
    "hold time": [
        {
            "question": "What hold time studies have been performed to support intermediate and in-process holds?",
            "probability": 0.80,
            "approach": "Present hold time study data with qualified conditions and acceptance criteria.",
        },
    ],
}

# Severity -> effort mapping heuristic
_EFFORT_MAP = {
    "critical": "high",
    "major": "medium",
    "minor": "low",
}

# Remediation templates by severity and item type
_REMEDIATION_TEMPLATES: Dict[str, Dict[str, str]] = {
    "critical": {
        "default": "Generate required data and documentation. This is a potential refuse-to-file issue.",
        "viral clearance": "Conduct viral clearance validation study with appropriate model viruses per ICH Q5A(R2).",
        "specification": "Develop and justify specifications with batch data per ICH Q6B. Include release and shelf-life criteria.",
        "cell bank": "Complete MCB/WCB characterization per ICH Q5B/Q5D including adventitious agent testing.",
        "composition": "Prepare quantitative composition table with justified excipient concentrations.",
        "impurity": "Complete impurity profiling with qualified analytical methods and clearance data.",
    },
    "major": {
        "default": "Prepare supplemental data package. May delay review if not addressed prior to submission.",
        "process flow": "Develop detailed process flow diagram with CPP/IPC annotations.",
        "cell culture": "Document cell culture parameters with supporting batch data.",
        "in-process control": "Define and justify in-process controls at each critical step.",
        "stability": "Expand stability dataset to cover required ICH conditions and timepoints.",
        "animal-derived": "Complete TSE/BSE risk assessment and material inventory.",
        "hold time": "Perform hold time qualification studies for all process intermediates.",
    },
    "minor": {
        "default": "Address in next revision or as amendment. Low risk to review timeline.",
        "reference standard": "Document reference standard qualification and traceability.",
        "method validation": "Compile method validation summaries in standardized format.",
    },
}


# =========================================================================
# Pipeline
# =========================================================================

def generate_gap_memo(
    sections: List[Dict[str, Any]],
    product_type: str = "mAb",
    submission_type: str = "BLA",
    product_name: str = "",
) -> GapMemo:
    """Run full gap memo pipeline.

    Parameters
    ----------
    sections : list of dict
        Each dict must have:
            - name: CTD section identifier (e.g. "S.2.2")
            - title: section heading
            - content: section text content
    product_type : str
        Product modality (e.g. "mAb", "ADC", "fusion protein").
    submission_type : str
        Regulatory submission type (e.g. "BLA", "MAA", "IND").
    product_name : str
        Product identifier for the report header.

    Returns
    -------
    GapMemo
        Structured gap assessment with reviewer-style findings.
    """
    # --- Input validation ---
    if sections is None:
        sections = []
    if not isinstance(sections, list):
        return _empty_memo(product_name, submission_type)
    for sec in sections:
        if not isinstance(sec, dict) or "name" not in sec or "content" not in sec:
            return GapMemo(
                product_name=product_name or "Unknown",
                submission_type=submission_type,
                n_sections_reviewed=0, n_gaps_found=0,
                n_critical=0, n_major=0, n_minor=0,
                gaps=[], consistency_flags=[], predicted_questions=[],
                overall_readiness="Not Ready",
                recommended_actions=[
                    "Input error: each section must be a dict with 'name' and 'content' keys."
                ],
                compliance_score=0.0,
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )
    if not sections:
        return _empty_memo(product_name, submission_type)

    # ------------------------------------------------------------------
    # Step 1: Classify -- validate/enrich section labels
    # ------------------------------------------------------------------
    # Convert input sections to classification-compatible format
    classifications = _classify_sections(sections)

    # ------------------------------------------------------------------
    # Step 2: Check consistency -- numerical & terminology conflicts
    # ------------------------------------------------------------------
    consistency_result = check_consistency(classifications)
    consistency_flags = _build_consistency_flags(consistency_result)

    # ------------------------------------------------------------------
    # Step 3: Review checklist -- identify missing items
    # ------------------------------------------------------------------
    checklist_result = review_checklist(classifications)
    gaps = _build_gap_findings(checklist_result)

    # ------------------------------------------------------------------
    # Step 4: Predict reviewer questions
    # ------------------------------------------------------------------
    predicted_questions = _predict_questions(gaps, consistency_flags, sections)

    # ------------------------------------------------------------------
    # Step 5: Suggest remediation (already embedded in gap findings)
    # ------------------------------------------------------------------
    # Remediation is assigned during _build_gap_findings

    # ------------------------------------------------------------------
    # Step 6: Generate memo -- assemble structured output
    # ------------------------------------------------------------------
    n_critical = sum(1 for g in gaps if g.severity == "critical")
    n_major = sum(1 for g in gaps if g.severity == "major")
    n_minor = sum(1 for g in gaps if g.severity == "minor")

    # Determine overall readiness
    overall_readiness = _assess_readiness(
        n_critical, n_major, n_minor, consistency_flags,
        checklist_result.get("compliance_score", 0),
    )

    # Build recommended actions
    recommended_actions = _build_recommended_actions(
        gaps, consistency_flags, overall_readiness, submission_type,
    )

    return GapMemo(
        product_name=product_name or f"Unnamed {product_type}",
        submission_type=submission_type,
        n_sections_reviewed=len(sections),
        n_gaps_found=len(gaps),
        n_critical=n_critical,
        n_major=n_major,
        n_minor=n_minor,
        gaps=gaps,
        consistency_flags=consistency_flags,
        predicted_questions=predicted_questions,
        overall_readiness=overall_readiness,
        recommended_actions=recommended_actions,
        compliance_score=checklist_result.get("compliance_score", 0),
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


# =========================================================================
# Internal helpers
# =========================================================================

def _classify_sections(
    sections: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert input section dicts to classification-compatible format.

    If the input already has explicit section IDs (name field), we use those
    directly. Otherwise we run the section classifier on the content.
    """
    classifications = []
    for i, sec in enumerate(sections):
        name = sec.get("name", "")
        title = sec.get("title", "")
        content = sec.get("content", "")

        if name:
            # Use the provided section ID directly
            classifications.append({
                "classification_id": f"input-{i+1:03d}",
                "section_id": name,
                "section_heading": title,
                "content_full": content,
                "content_preview": content[:300],
                "confidence": {
                    "score": 0.95,
                    "qualifier": "high",
                    "basis": "Section ID provided in input.",
                },
            })
        else:
            # Run classifier on content
            raw_classifications = classify_sections(content)
            if raw_classifications:
                for cls in raw_classifications:
                    cls["classification_id"] = f"input-{i+1:03d}"
                classifications.extend(raw_classifications)
            else:
                classifications.append({
                    "classification_id": f"input-{i+1:03d}",
                    "section_id": "UNCLASSIFIED",
                    "section_heading": title or "Unknown",
                    "content_full": content,
                    "content_preview": content[:300],
                    "confidence": {
                        "score": None,
                        "qualifier": "unknown",
                        "basis": "Could not classify section content.",
                    },
                })

    return classifications


def _build_consistency_flags(
    consistency_result: Dict[str, Any],
) -> List[ConsistencyFlag]:
    """Convert consistency checker output to ConsistencyFlag dataclasses."""
    flags = []
    for f in consistency_result.get("consistency_flags", []):
        flags.append(ConsistencyFlag(
            finding_id=f.get("finding_id", ""),
            category=f.get("category", ""),
            severity=f.get("severity", "warning"),
            description=f.get("description", ""),
            section_a=f.get("section_a", ""),
            section_b=f.get("section_b", ""),
            value_a=str(f.get("value_a", "")),
            value_b=str(f.get("value_b", "")),
            suggested_resolution=f.get("suggested_resolution", ""),
        ))
    return flags


def _build_gap_findings(
    checklist_result: Dict[str, Any],
) -> List[GapFinding]:
    """Convert checklist reviewer output to GapFinding dataclasses with remediation."""
    gaps = []
    for section_result in checklist_result.get("section_results", []):
        section_id = section_result.get("section_id", "")
        for item in section_result.get("checklist_items", []):
            if item["status"] in ("missing", "partial"):
                severity = item.get("compliance_severity", "minor")
                item_label = item.get("expected_item", "")
                status_desc = "Missing" if item["status"] == "missing" else "Partially addressed"

                description = (
                    f"{status_desc}: {item_label} in section {section_id} "
                    f"({section_result.get('section_heading', '')})."
                )

                remediation = _get_remediation(severity, item_label)
                effort = _EFFORT_MAP.get(severity, "medium")

                gaps.append(GapFinding(
                    section=section_id,
                    checklist_item=item_label,
                    severity=severity,
                    description=description,
                    remediation=remediation,
                    estimated_effort=effort,
                ))
    return gaps


def _get_remediation(severity: str, item_label: str) -> str:
    """Look up remediation text from templates."""
    severity_templates = _REMEDIATION_TEMPLATES.get(severity, {})
    item_lower = item_label.lower()

    # Try to match a specific template
    for key, text in severity_templates.items():
        if key != "default" and key in item_lower:
            return text

    # Fall back to default for this severity
    return severity_templates.get(
        "default",
        "Review and address this gap prior to submission.",
    )


def _predict_questions(
    gaps: List[GapFinding],
    consistency_flags: List[ConsistencyFlag],
    sections: List[Dict[str, Any]],
) -> List[PredictedQuestion]:
    """Generate predicted reviewer questions based on gaps and flags."""
    questions: List[PredictedQuestion] = []
    seen_keys: set = set()

    # Questions from gaps
    for gap in gaps:
        item_lower = gap.checklist_item.lower()
        for template_key, templates in _QUESTION_TEMPLATES.items():
            if template_key in item_lower:
                for tmpl in templates:
                    q_key = f"{gap.section}:{template_key}"
                    if q_key not in seen_keys:
                        seen_keys.add(q_key)
                        # Boost probability for critical gaps
                        prob = tmpl["probability"]
                        if gap.severity == "critical":
                            prob = min(prob + 0.05, 1.0)

                        questions.append(PredictedQuestion(
                            question=tmpl["question"],
                            section=gap.section,
                            probability=round(prob, 2),
                            suggested_response_approach=tmpl["approach"],
                        ))

    # Questions from consistency flags
    for flag in consistency_flags:
        if flag.severity in ("error", "warning"):
            q_key = f"consistency:{flag.section_a}:{flag.section_b}"
            if q_key not in seen_keys:
                seen_keys.add(q_key)
                questions.append(PredictedQuestion(
                    question=(
                        f"Please clarify the discrepancy between sections "
                        f"{flag.section_a} and {flag.section_b}: {flag.description}"
                    ),
                    section=flag.section_a,
                    probability=0.90 if flag.severity == "error" else 0.75,
                    suggested_response_approach=(
                        f"Verify the correct value and update the inconsistent section. "
                        f"{flag.suggested_resolution}"
                    ),
                ))

    # Generic fallback question if we have gaps but no template matches
    if gaps and not questions:
        top_gap = sorted(gaps, key=lambda g: {"critical": 0, "major": 1, "minor": 2}.get(g.severity, 3))[0]
        questions.append(PredictedQuestion(
            question=f"Please provide the missing information for {top_gap.checklist_item} in section {top_gap.section}.",
            section=top_gap.section,
            probability=0.80,
            suggested_response_approach=top_gap.remediation,
        ))

    # Sort by probability descending
    questions.sort(key=lambda q: q.probability, reverse=True)
    return questions


def _assess_readiness(
    n_critical: int,
    n_major: int,
    n_minor: int,
    consistency_flags: List[ConsistencyFlag],
    compliance_score: float,
) -> str:
    """Determine overall submission readiness."""
    n_error_flags = sum(1 for f in consistency_flags if f.severity == "error")

    if n_critical > 0 or n_error_flags > 0:
        return "Not Ready"
    elif n_major > 2 or compliance_score < 60:
        return "Not Ready"
    elif n_major > 0 or compliance_score < 80:
        return "Near-Ready"
    else:
        return "Ready"


def _build_recommended_actions(
    gaps: List[GapFinding],
    consistency_flags: List[ConsistencyFlag],
    overall_readiness: str,
    submission_type: str,
) -> List[str]:
    """Build prioritized list of recommended actions."""
    actions: List[str] = []

    # Critical gaps first
    critical_gaps = [g for g in gaps if g.severity == "critical"]
    if critical_gaps:
        sections = sorted(set(g.section for g in critical_gaps))
        actions.append(
            f"CRITICAL: Address {len(critical_gaps)} critical gap(s) in "
            f"section(s) {', '.join(sections)} before submission. "
            f"These are potential refuse-to-file issues."
        )

    # Major gaps
    major_gaps = [g for g in gaps if g.severity == "major"]
    if major_gaps:
        sections = sorted(set(g.section for g in major_gaps))
        actions.append(
            f"Address {len(major_gaps)} major gap(s) in section(s) "
            f"{', '.join(sections)} to reduce risk of information requests."
        )

    # Consistency issues
    error_flags = [f for f in consistency_flags if f.severity == "error"]
    warning_flags = [f for f in consistency_flags if f.severity == "warning"]
    if error_flags:
        actions.append(
            f"Resolve {len(error_flags)} cross-section contradiction(s) "
            f"flagged as errors before submission."
        )
    if warning_flags:
        actions.append(
            f"Review {len(warning_flags)} cross-section inconsistency warning(s) "
            f"for potential corrections."
        )

    # Readiness-specific advice
    if overall_readiness == "Ready":
        actions.append(
            f"Submission appears ready for {submission_type} filing. "
            f"Perform final QC review of all sections."
        )
    elif overall_readiness == "Near-Ready":
        actions.append(
            f"Address remaining major gaps to achieve {submission_type} readiness. "
            f"Estimated additional preparation: 2-4 weeks."
        )
    else:
        actions.append(
            f"Significant gaps remain. Recommend a comprehensive gap remediation "
            f"effort before {submission_type} filing. Estimated: 1-3 months."
        )

    return actions


def _empty_memo(product_name: str, submission_type: str) -> GapMemo:
    """Return an empty memo when no sections are provided."""
    return GapMemo(
        product_name=product_name or "Unknown",
        submission_type=submission_type,
        n_sections_reviewed=0,
        n_gaps_found=0,
        n_critical=0,
        n_major=0,
        n_minor=0,
        gaps=[],
        consistency_flags=[],
        predicted_questions=[],
        overall_readiness="Not Ready",
        recommended_actions=["No sections provided for review. Submit CTD Module 3 sections for assessment."],
        compliance_score=0.0,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
