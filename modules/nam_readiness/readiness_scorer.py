"""Rule-based NAM readiness scorer used by READY-* benchmarks."""

from __future__ import annotations

from typing import Any, Dict, List

from modules.nam_readiness.context_taxonomy import CONTEXT_OF_USE_TAXONOMY
from schemas.label_schema import NAMReadinessRecord


def score_readiness(record: NAMReadinessRecord) -> NAMReadinessRecord:
    """Score New Approach Methodology readiness on a 0-1 scale.

    The scoring intentionally remains transparent: validation quality, direct
    regulatory precedent, qualification pathway, and context maturity are
    combined into a deterministic readiness score. Gaps are human-readable
    reasons a reviewer would ask for more support.
    """
    evidence_score, evidence_gaps = _score_validation_evidence(record.validation_evidence)
    precedent_score, precedent_gaps = _score_precedent(record.regulatory_precedent, record.context_of_use)
    pathway_score, pathway_gaps = _score_pathway(record.qualification_pathway)
    context_score, context_gaps = _score_context(record.context_of_use)

    score = (
        0.45 * evidence_score
        + 0.25 * precedent_score
        + 0.20 * pathway_score
        + 0.10 * context_score
    )
    score = round(max(0.0, min(1.0, score)), 4)

    gaps: List[str] = []
    gaps.extend(evidence_gaps)
    gaps.extend(precedent_gaps)
    gaps.extend(pathway_gaps)
    gaps.extend(context_gaps)

    return NAMReadinessRecord(
        record_id=record.record_id,
        nam_type=record.nam_type,
        context_of_use=record.context_of_use,
        species_replaced=record.species_replaced,
        validation_evidence=list(record.validation_evidence),
        regulatory_precedent=list(record.regulatory_precedent),
        qualification_pathway=record.qualification_pathway,
        readiness_score=score,
        readiness_gaps=_dedupe(gaps),
    )


def _score_validation_evidence(evidence: List[Dict[str, Any]]) -> tuple[float, List[str]]:
    if not evidence:
        return 0.0, ["No validation evidence provided."]

    concordances = [
        float(item.get("concordance_rate", 0.0))
        for item in evidence
        if isinstance(item, dict) and item.get("concordance_rate") is not None
    ]
    sample_total = sum(
        int(item.get("sample_size", 0) or 0)
        for item in evidence
        if isinstance(item, dict)
    )
    study_types = {
        str(item.get("study_type", "")).lower()
        for item in evidence
        if isinstance(item, dict)
    }

    avg_concordance = sum(concordances) / len(concordances) if concordances else 0.0
    concordance_component = _linear_scale(avg_concordance, 0.50, 0.88)
    sample_component = min(sample_total / 150.0, 1.0)
    external_component = 1.0 if "external_validation" in study_types else 0.0
    feasibility_penalty = 0.20 if study_types == {"feasibility"} else 0.0

    score = (
        0.60 * concordance_component
        + 0.25 * sample_component
        + 0.15 * external_component
        - feasibility_penalty
    )
    score = max(0.0, min(1.0, score))

    gaps: List[str] = []
    if sample_total < 50:
        gaps.append("Validation package has limited sample size.")
    if "external_validation" not in study_types and (avg_concordance < 0.75 or sample_total < 75):
        gaps.append("No independent external validation study.")
    if avg_concordance < 0.75:
        gaps.append("Validation concordance is below mature qualification expectations.")
    return score, gaps


def _score_precedent(precedents: List[str], context_of_use: str) -> tuple[float, List[str]]:
    if not precedents:
        return 0.0, ["No regulatory precedent identified."]

    joined = " ".join(precedents).lower()
    direct_terms = _context_terms(context_of_use)
    has_direct = any(term in joined for term in direct_terms)

    if has_direct:
        score = min(0.55 + 0.15 * len(precedents), 1.0)
        gaps: List[str] = []
    else:
        score = min(0.20 + 0.10 * len(precedents), 0.45)
        gaps = ["Precedent is related but not direct for the proposed context of use."]
    return score, gaps


def _score_pathway(pathway: str | None) -> tuple[float, List[str]]:
    normalized = (pathway or "none").lower()
    if normalized in {"ddt", "istand"}:
        return 1.0, []
    if normalized == "voluntary_submission":
        return 0.55, ["No formal DDT/ISTAND qualification pathway completed."]
    return 0.0, ["No formal qualification pathway defined."]


def _score_context(context_of_use: str) -> tuple[float, List[str]]:
    entry = CONTEXT_OF_USE_TAXONOMY.get(context_of_use)
    if not entry:
        return 0.50, [f"Unknown context of use: {context_of_use}."]
    return float(entry["maturity"]), []


def _context_terms(context_of_use: str) -> List[str]:
    return {
        "safety_pharmacology": ["safety", "pharmacology", "dili", "toxicity", "organ"],
        "hepatotoxicity_screening": ["liver", "hepato", "dili"],
        "nephrotoxicity_screening": ["kidney", "nephro"],
        "cardiotoxicity_screening": ["cardio", "cipa", "proarrhythmia", "qt"],
        "immunogenicity_prediction": ["immunogenicity", "ada"],
        "viral_safety": ["viral", "clearance"],
        "bioequivalence": ["bioequivalence", "biowaiver", "pbpk", "be waiver"],
    }.get(context_of_use, [context_of_use.replace("_", " ")])


def _linear_scale(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
