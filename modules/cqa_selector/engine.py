"""
CQA Selection Engine (#52)

Evidence-based Critical Quality Attribute selection using a structured
risk scoring framework: Impact x Detectability x Controllability.

Each candidate CQA is scored across 3 dimensions (1-5 each):
  - Impact: effect on safety and efficacy if out of spec
  - Detectability: ability to measure and monitor
  - Controllability: ability to control through process parameters

CQA Risk Priority Number (RPN) = Impact x (6 - Detectability) x (6 - Controllability)
Higher RPN = higher priority for CQA designation.

Reference: ICH Q8(R2), ICH Q9, FDA "Quality by Design for ANDAs" (2012)

Usage:
    python -m modules.cqa_selector.engine --demo
    python -m modules.cqa_selector.engine --input candidate_cqas.json
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@dataclass
class CQACandidate:
    name: str
    category: str  # identity, purity, potency, safety, stability, physicochemical
    assay: str
    impact: int  # 1-5 (5 = severe patient safety impact)
    detectability: int  # 1-5 (5 = easily detected and quantified)
    controllability: int  # 1-5 (5 = fully controlled by process)
    rpn: float = 0.0
    designation: str = ""  # CQA, KQA (key), QA (quality), or Monitor
    rationale: str = ""
    precedent_score: float = 0.0  # 0-1, from EvidenceRegistry precedent lookup
    n_precedents: int = 0  # number of matching registry precedents


# =========================================================================
# Default mAb CQA Candidates (ICH Q6B + industry consensus)
# =========================================================================

DEFAULT_MAB_CANDIDATES = [
    {"name": "Sequence Identity", "category": "identity", "assay": "Peptide Map LC-MS/MS",
     "impact": 5, "detectability": 5, "controllability": 5},
    {"name": "Molecular Weight", "category": "identity", "assay": "Intact Mass LC-MS",
     "impact": 4, "detectability": 5, "controllability": 5},
    {"name": "SEC Monomer %", "category": "purity", "assay": "SE-HPLC",
     "impact": 4, "detectability": 5, "controllability": 3},
    {"name": "SEC HMW %", "category": "purity", "assay": "SE-HPLC",
     "impact": 4, "detectability": 5, "controllability": 3},
    {"name": "CE-SDS Purity", "category": "purity", "assay": "CE-SDS (NR/R)",
     "impact": 3, "detectability": 5, "controllability": 4},
    {"name": "Charge Variants (cIEF)", "category": "physicochemical", "assay": "cIEF",
     "impact": 3, "detectability": 4, "controllability": 2},
    {"name": "Potency", "category": "potency", "assay": "Cell-based bioassay",
     "impact": 5, "detectability": 3, "controllability": 3},
    {"name": "Binding Affinity (Kd)", "category": "potency", "assay": "SPR/BLI",
     "impact": 5, "detectability": 4, "controllability": 4},
    {"name": "ADCC Activity", "category": "potency", "assay": "ADCC reporter assay",
     "impact": 4, "detectability": 3, "controllability": 2},
    {"name": "N-Glycan Profile", "category": "physicochemical", "assay": "HILIC-FLD",
     "impact": 4, "detectability": 4, "controllability": 2},
    {"name": "Afucosylation %", "category": "physicochemical", "assay": "HILIC-FLD",
     "impact": 5, "detectability": 4, "controllability": 2},
    {"name": "Deamidation %", "category": "stability", "assay": "Peptide Map",
     "impact": 3, "detectability": 4, "controllability": 2},
    {"name": "Oxidation %", "category": "stability", "assay": "Peptide Map",
     "impact": 3, "detectability": 4, "controllability": 3},
    {"name": "Host Cell Protein", "category": "safety", "assay": "ELISA + LC-MS/MS",
     "impact": 4, "detectability": 4, "controllability": 3},
    {"name": "Host Cell DNA", "category": "safety", "assay": "qPCR",
     "impact": 3, "detectability": 5, "controllability": 4},
    {"name": "Endotoxin", "category": "safety", "assay": "LAL",
     "impact": 5, "detectability": 5, "controllability": 4},
    {"name": "Bioburden", "category": "safety", "assay": "Membrane filtration",
     "impact": 5, "detectability": 5, "controllability": 5},
    {"name": "Particulate Matter", "category": "safety", "assay": "HIAC/MFI",
     "impact": 3, "detectability": 4, "controllability": 3},
    {"name": "Tm1 (Thermal Stability)", "category": "stability", "assay": "DSF/DSC",
     "impact": 3, "detectability": 5, "controllability": 2},
    {"name": "Shelf Life (HMW growth)", "category": "stability", "assay": "SEC trending",
     "impact": 4, "detectability": 4, "controllability": 2},
]


def compute_rpn(impact: int, detectability: int, controllability: int) -> float:
    """Compute Risk Priority Number.

    Higher RPN = higher risk = higher priority for CQA designation.
    Uses inverted detectability and controllability (harder to detect/control = higher risk).
    """
    return impact * (6 - detectability) * (6 - controllability)


def classify_cqa(rpn: float, impact: int) -> str:
    """Classify attribute based on RPN and impact."""
    if impact >= 5 or rpn >= 40:
        return "CQA"  # Critical Quality Attribute — must be in specification
    elif impact >= 4 or rpn >= 20:
        return "KQA"  # Key Quality Attribute — monitored with action limits
    elif rpn >= 10:
        return "QA"   # Quality Attribute — characterized, may not be in spec
    else:
        return "Monitor"  # Monitor only — report but no specification


def _clamp(val, lo, hi, field_name=""):
    """Clamp value to valid range with warning."""
    v = val if isinstance(val, (int, float)) else lo
    return max(lo, min(hi, int(v)))


def _query_precedent_score(category: str, name: str, registry) -> tuple:
    """Query EvidenceRegistry for precedents matching a CQA category.

    Returns (precedent_score, n_precedents) where precedent_score is 0-1.
    """
    if registry is None:
        return 0.0, 0

    try:
        # Query by category and precedent type
        entries = registry.query(category=category, entry_type="precedent")
        if not entries:
            # Fallback: query by keyword from the attribute name
            entries = registry.query(keyword=name.lower().split()[0])
        if not entries:
            return 0.0, 0

        # Score = average confidence of matching precedents, capped at 1.0
        avg_confidence = sum(e.confidence for e in entries) / len(entries)
        return min(1.0, round(avg_confidence, 3)), len(entries)
    except Exception:
        return 0.0, 0


def select_cqas(candidates: List[Dict], registry=None) -> List[CQACandidate]:
    """Score and classify all CQA candidates.

    Args:
        candidates: List of candidate dicts with name, category, assay,
            impact, detectability, controllability.
        registry: Optional EvidenceRegistry instance. When provided,
            precedent scores are blended with RPN (50/50) for final ranking.
    """
    if not candidates:
        return []

    results = []
    for c in candidates:
        impact = _clamp(c.get("impact", 3), 1, 5, "impact")
        detect = _clamp(c.get("detectability", 3), 1, 5, "detectability")
        control = _clamp(c.get("controllability", 3), 1, 5, "controllability")
        rpn = compute_rpn(impact, detect, control)

        # Query precedent score from registry (GAP-CQA-001)
        prec_score, n_prec = _query_precedent_score(
            c.get("category", ""), c.get("name", ""), registry
        )

        # Blend RPN with precedent score if registry provided
        if registry is not None and n_prec > 0:
            # Normalize RPN to 0-1 scale (max theoretical RPN = 125)
            rpn_norm = rpn / 125.0
            blended_norm = rpn_norm * 0.5 + prec_score * 0.5
            # Convert back to RPN scale for classification
            blended_rpn = blended_norm * 125.0
        else:
            blended_rpn = rpn

        designation = classify_cqa(blended_rpn, impact)

        rationale_parts = []
        if impact >= 5:
            rationale_parts.append("Critical impact on patient safety/efficacy")
        if detect <= 2:
            rationale_parts.append("Difficult to detect — requires specialized assay")
        if control <= 2:
            rationale_parts.append("Difficult to control — inherent to sequence/process")
        if blended_rpn >= 40:
            rationale_parts.append(f"High risk priority (RPN={blended_rpn:.0f})")
        if n_prec > 0:
            rationale_parts.append(f"Precedent support: {n_prec} entries (score={prec_score:.2f})")

        results.append(CQACandidate(
            name=c["name"], category=c["category"], assay=c["assay"],
            impact=impact, detectability=detect, controllability=control,
            rpn=blended_rpn, designation=designation,
            rationale="; ".join(rationale_parts) if rationale_parts else "Standard monitoring",
            precedent_score=prec_score,
            n_precedents=n_prec,
        ))

    results.sort(key=lambda x: x.rpn, reverse=True)
    return results


def generate_report(results: List[CQACandidate]) -> str:
    """Generate CQA selection report."""
    lines = [
        "# CQA Selection Report",
        "\n## Summary\n",
        f"- Total candidates: {len(results)}",
        f"- CQA (Critical): {sum(1 for r in results if r.designation == 'CQA')}",
        f"- KQA (Key): {sum(1 for r in results if r.designation == 'KQA')}",
        f"- QA (Quality): {sum(1 for r in results if r.designation == 'QA')}",
        f"- Monitor: {sum(1 for r in results if r.designation == 'Monitor')}",
        "\n## CQA Risk Assessment Matrix\n",
        "| Attribute | Category | Impact | Detect | Control | RPN | Designation | Rationale |",
        "|-----------|----------|--------|--------|---------|-----|-------------|-----------|",
    ]
    for r in results:
        lines.append(f"| {r.name} | {r.category} | {r.impact} | {r.detectability} | "
                    f"{r.controllability} | {r.rpn:.0f} | **{r.designation}** | {r.rationale} |")

    lines.append("\n## Methodology\n")
    lines.append("RPN = Impact x (6 - Detectability) x (6 - Controllability)")
    lines.append("Reference: ICH Q8(R2), ICH Q9, FDA QbD guidance")
    lines.append("\n---\n*Generated by CQA Selection Engine.*")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="CQA Selection Engine")
    parser.add_argument("--input", help="CQA candidates JSON")
    parser.add_argument("--demo", action="store_true", help="Run with default mAb candidates")
    parser.add_argument("--output", help="Output JSON")
    parser.add_argument("--report", help="Output markdown report")
    args = parser.parse_args()

    if args.demo:
        candidates = DEFAULT_MAB_CANDIDATES
    elif args.input:
        with open(args.input) as f:
            candidates = json.load(f)
    else:
        parser.print_help()
        return

    results = select_cqas(candidates)

    print(f"CQA Selection: {len(results)} candidates scored")
    print(f"  CQA:     {sum(1 for r in results if r.designation == 'CQA')}")
    print(f"  KQA:     {sum(1 for r in results if r.designation == 'KQA')}")
    print(f"  QA:      {sum(1 for r in results if r.designation == 'QA')}")
    print(f"  Monitor: {sum(1 for r in results if r.designation == 'Monitor')}")
    print()
    for r in results[:8]:
        print(f"  {r.designation:7s} | RPN={r.rpn:3.0f} | {r.name}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump([asdict(r) for r in results], f, indent=2)

    if args.report:
        with open(args.report, "w") as f:
            f.write(generate_report(results))

    if not args.output and not args.report:
        print("\n" + generate_report(results))


if __name__ == "__main__":
    main()
