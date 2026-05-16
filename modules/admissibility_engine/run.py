"""
E2E runner for the Science-to-Admissibility Engine.

This module consolidates claim ingestion, 6-question scoring, and gap
analysis into a single file.  The action_recommender adapter remains
separate (it delegates to the shared 5-level taxonomy).

Usage:
    python -m modules.admissibility_engine.run --input modules/admissibility_engine/benchmarks/adalimumab_biosimilar_case.json
    python -m modules.admissibility_engine.run --demo
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from schemas.label_schema import EvidenceClaim
from .action_recommender import recommend_action


# =========================================================================
# Evidence-backed scoring support (GAP-ADM-001)
# =========================================================================

# Map 6Q keys to registry query categories / tags for evidence lookup
_QUESTION_REGISTRY_MAP = {
    "biology_credible": {"tags": ["biology", "mechanism", "credibility"], "keyword": "mechanism"},
    "signal_measurable": {"tags": ["assay", "measurement", "quantitation"], "keyword": "measurable"},
    "model_translatable": {"tags": ["translational", "clinical", "preclinical"], "keyword": "translational"},
    "cmc_supportable": {"tags": ["cmc", "manufacturing", "process"], "keyword": "manufacturing"},
    "regulatory_acceptable": {"tags": ["regulatory", "fda", "ema", "precedent"], "keyword": "regulatory"},
    "commercial_window": {"tags": ["commercial", "market", "timeline"], "keyword": "commercial"},
}


def _init_evidence_support():
    """Lazily initialize EvidenceRegistry and claim_evidence_grader.

    Returns (registry, grader_model, grader_vectorizer) or (None, None, None)
    if initialization fails.
    """
    try:
        from evidence_registry.registry import EvidenceRegistry
        from models.claim_evidence_grader import train_model, predict
        registry = EvidenceRegistry()
        if registry.count == 0:
            return None, None, None
        model, vectorizer = train_model()
        return registry, model, vectorizer
    except Exception:
        return None, None, None


def _grade_claim_against_registry(claim_text, question_key, registry, model, vectorizer):
    """Query registry for entries relevant to a 6Q question and grade the claim.

    Returns (evidence_score, provenance_records) where evidence_score is 0-1
    and provenance_records is a list of ProvenanceRecord dicts.
    """
    from models.claim_evidence_grader import predict as grader_predict

    qmap = _QUESTION_REGISTRY_MAP.get(question_key, {})
    tags = qmap.get("tags", [])
    keyword = qmap.get("keyword", "")

    # Query registry by tags first, fall back to keyword
    entries = registry.query(tags=tags)
    if not entries and keyword:
        entries = registry.query(keyword=keyword)
    if not entries:
        return None, []

    # Grade claim against each entry's content
    strength_to_score = {"strong": 0.9, "moderate": 0.6, "weak": 0.3, "anecdotal": 0.1}
    scores = []
    provenance = []

    for entry in entries[:5]:  # Limit to top 5 to avoid perf issues
        combined_text = f"{claim_text} | Evidence: {entry.content}"
        try:
            result = grader_predict(combined_text, model, vectorizer)
            grade = result["prediction"]
            score = strength_to_score.get(grade, 0.4)
            # Weight by entry confidence
            weighted = score * entry.confidence
            scores.append(weighted)

            prov = entry.to_provenance(
                module="admissibility_engine",
                context=f"6Q/{question_key}: graded '{grade}' (p={result['probabilities'].get(grade, 0):.2f})"
            )
            provenance.append(prov.to_dict())
        except Exception:
            continue

    if not scores:
        return None, []

    evidence_score = sum(scores) / len(scores)
    return min(1.0, evidence_score), provenance


# =========================================================================
# Claim Ingestion (formerly claim_ingester.py)
# =========================================================================

def _ingest_claims(data: List[Dict]) -> List[EvidenceClaim]:
    """Parse a list of dicts into EvidenceClaim objects."""
    return [EvidenceClaim.from_dict(d) for d in data]


def _load_claims_file(path: str) -> List[EvidenceClaim]:
    """Load claims from a JSON file (list of claim dicts)."""
    with open(path) as f:
        raw = json.load(f)
    claims_data = raw.get("claims", raw) if isinstance(raw, dict) else raw
    return _ingest_claims(claims_data)


# Public aliases for backward compatibility
ingest_claims = _ingest_claims
load_claims_file = _load_claims_file


# =========================================================================
# Six-Question Scoring (formerly six_question_scorer.py)
# =========================================================================

QUESTION_KEYS = [
    "biology_credible",
    "signal_measurable",
    "model_translatable",
    "cmc_supportable",
    "regulatory_acceptable",
    "commercial_window",
]


def _score_claim_heuristic(claim: EvidenceClaim) -> Dict[str, float]:
    """Score a claim on the 6 strategic questions using rule-based heuristics.

    This is the original heuristic scorer, preserved as fallback.
    """
    scores = {}

    # Map evidence strength to base score
    strength_map = {"strong": 0.9, "moderate": 0.6, "weak": 0.3, "anecdotal": 0.1}
    base = strength_map.get(claim.evidence_strength or "", 0.4)

    # Source type credibility
    source_cred = {
        "journal_paper": 0.9, "fda_announcement": 0.95,
        "conference_abstract": 0.6, "company_pr": 0.3, "wechat_article": 0.2,
    }.get(claim.source_type, 0.4)

    # 1. Biology credible: high if strong evidence + entities identified
    scores["biology_credible"] = min(1.0, base * 0.7 + (0.3 if len(claim.extracted_entities) >= 2 else 0.1))

    # 2. Signal measurable: high if source is quantitative (journal, FDA)
    scores["signal_measurable"] = min(1.0, source_cred * 0.8 + base * 0.2)

    # 3. Model translatable: moderate by default, boosted by clinical evidence
    scores["model_translatable"] = 0.5 if claim.source_type in ("journal_paper", "fda_announcement") else 0.3

    # 4. CMC supportable: depends on whether entities include process/analytical terms
    cmc_terms = {"process", "manufacturing", "formulation", "purification", "chromatography", "analytical"}
    cmc_match = any(any(t in e.lower() for t in cmc_terms) for e in claim.extracted_entities)
    scores["cmc_supportable"] = 0.7 if cmc_match else 0.4

    # 5. Regulatory acceptable: high if FDA/regulatory source, low if informal
    scores["regulatory_acceptable"] = {
        "fda_announcement": 0.95, "journal_paper": 0.7, "conference_abstract": 0.5,
        "company_pr": 0.3, "wechat_article": 0.1,
    }.get(claim.source_type, 0.3)

    # 6. Commercial window: moderate by default (time-dependent, hard to score from text)
    scores["commercial_window"] = 0.5

    return {k: round(v, 3) for k, v in scores.items()}


def _score_claim(claim: EvidenceClaim, registry=None, grader_model=None,
                 grader_vectorizer=None) -> Dict[str, float]:
    """Score a claim on the 6 strategic questions.

    If registry and grader are provided, blends evidence-backed scores (60%)
    with heuristic scores (40%). Falls back to pure heuristics on any failure.

    Returns dict of question_key -> score (0-1).
    Also attaches a 'provenance' key with list of ProvenanceRecord dicts
    when evidence-backed scoring is used.
    """
    heuristic_scores = _score_claim_heuristic(claim)

    if registry is None or grader_model is None or grader_vectorizer is None:
        return heuristic_scores

    # Try evidence-backed scoring
    try:
        blended = {}
        all_provenance = []
        for qkey in QUESTION_KEYS:
            h_score = heuristic_scores[qkey]
            ev_score, prov = _grade_claim_against_registry(
                claim.claim_text, qkey, registry, grader_model, grader_vectorizer
            )
            if ev_score is not None:
                # Blend: 60% evidence, 40% heuristic
                blended[qkey] = round(ev_score * 0.6 + h_score * 0.4, 3)
                all_provenance.extend(prov)
            else:
                blended[qkey] = h_score

        blended["_provenance"] = all_provenance
        return blended
    except Exception:
        # Fall back to heuristics on any error
        return heuristic_scores


# Public alias for backward compatibility
score_claim = _score_claim


# =========================================================================
# Gap Analysis (formerly gap_analyzer.py)
# =========================================================================

# Regulatory evidence requirements for biologics approval
REGULATORY_CHECKLIST = {
    "analytical_similarity": "Comprehensive analytical characterization (structure, function, purity)",
    "functional_equivalence": "Functional assays demonstrating equivalent mechanism of action",
    "pk_bioequivalence": "Clinical PK study demonstrating bioequivalence",
    "clinical_efficacy": "At least one confirmatory clinical efficacy trial (or waiver justification)",
    "immunogenicity_data": "Comparative immunogenicity assessment",
    "stability_data": "Stability program per ICH Q1A/Q5C with adequate real-time data",
    "manufacturing_validation": "Process validation (at least 3 consecutive batches)",
    "viral_safety": "Viral clearance validation studies",
    "specification_justification": "Specification justified per ICH Q6B",
    "reference_standard": "Qualified reference standard with full characterization",
}


def _analyze_gaps(claims: List[EvidenceClaim]) -> Dict:
    """Identify which regulatory requirements are NOT covered by any claim.

    Returns dict with: covered, uncovered, coverage_pct, gap_details.
    """
    covered = set()
    evidence_map: Dict[str, List[str]] = {}

    for claim in claims:
        text_lower = claim.claim_text.lower()
        entities_lower = [e.lower() for e in claim.extracted_entities]
        all_text = text_lower + " " + " ".join(entities_lower)

        for req_id, req_desc in REGULATORY_CHECKLIST.items():
            # Match requirement keywords against claim text + entities
            keywords = req_id.replace("_", " ").split()
            if any(kw in all_text for kw in keywords if len(kw) > 3):
                covered.add(req_id)
                evidence_map.setdefault(req_id, []).append(claim.claim_id)

    uncovered = set(REGULATORY_CHECKLIST.keys()) - covered
    coverage_pct = len(covered) / len(REGULATORY_CHECKLIST) * 100 if REGULATORY_CHECKLIST else 0

    gap_details = []
    for req_id in sorted(uncovered):
        gap_details.append({
            "requirement": req_id,
            "description": REGULATORY_CHECKLIST[req_id],
            "action_needed": f"Provide evidence for: {REGULATORY_CHECKLIST[req_id]}",
        })

    return {
        "covered": sorted(covered),
        "uncovered": sorted(uncovered),
        "coverage_pct": round(coverage_pct, 1),
        "evidence_map": evidence_map,
        "gap_details": gap_details,
    }


# Public alias for backward compatibility
analyze_gaps = _analyze_gaps


# =========================================================================
# Pipeline Orchestration
# =========================================================================

def run_engine(input_path: str, record_labels: bool = False) -> dict:
    """Run the full admissibility pipeline on a claims file."""

    # 1. Ingest claims
    claims = _load_claims_file(input_path)

    # 1b. Initialize evidence support (graceful fallback if unavailable)
    registry, grader_model, grader_vectorizer = _init_evidence_support()

    # 2. Score each claim on 6 questions
    scored_claims = []
    all_provenance = []
    for claim in claims:
        scores = _score_claim(claim, registry, grader_model, grader_vectorizer)
        # Extract provenance if present
        claim_provenance = scores.pop("_provenance", [])
        all_provenance.extend(claim_provenance)
        claim.six_question_scores = scores
        scored_claims.append({
            "claim_id": claim.claim_id,
            "claim_text": claim.claim_text[:80],
            "source_type": claim.source_type,
            "evidence_strength": claim.evidence_strength,
            "six_q_scores": scores,
            **({"provenance": claim_provenance} if claim_provenance else {}),
        })

    # 3. Analyze gaps
    gap_analysis = _analyze_gaps(claims)

    # 4. Recommend action
    all_scores = [c["six_q_scores"] for c in scored_claims]
    recommendation = recommend_action(gap_analysis, all_scores)

    result = {
        "n_claims": len(claims),
        "claims_scored": scored_claims,
        "gap_analysis": gap_analysis,
        "recommendation": recommendation,
        "evidence_backed": registry is not None,
    }
    if all_provenance:
        result["provenance"] = all_provenance

    # 5. Label recording
    if record_labels:
        try:
            from schemas.label_emitter import emit_label
            emit_label("admissibility_engine", result, metadata={"input": input_path})
        except ImportError:
            pass

    return result


def main():
    parser = argparse.ArgumentParser(description="Science-to-Admissibility Engine")
    parser.add_argument("--input", help="Claims JSON file")
    parser.add_argument("--output", help="Output decision JSON")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--record-labels", action="store_true")
    args = parser.parse_args()

    if args.demo:
        bench = os.path.join(os.path.dirname(__file__), "benchmarks", "adalimumab_biosimilar_case.json")
        args.input = bench

    if not args.input:
        parser.print_help()
        return

    result = run_engine(args.input, args.record_labels)

    rec = result["recommendation"]
    gap = result["gap_analysis"]

    print(f"Science-to-Admissibility Engine")
    print(f"{'=' * 50}")
    print(f"Claims analyzed: {result['n_claims']}")
    print(f"Coverage: {gap['coverage_pct']:.0f}% ({len(gap['covered'])}/{len(gap['covered'])+len(gap['uncovered'])} requirements)")
    print(f"Gaps: {len(gap['uncovered'])}")
    print(f"\nRecommendation: {rec['action'].upper()} (confidence={rec['confidence']:.2f})")
    print(f"  5-level action: {rec.get('action_5level', 'N/A')} -- {rec.get('action_5level_label', '')}")
    print(f"Rationale: {rec['rationale']}")

    if rec["priority_actions"]:
        print(f"\nPriority actions:")
        for a in rec["priority_actions"]:
            print(f"  - {a}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
