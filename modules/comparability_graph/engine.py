"""
Comparability Evidence Graph Engine

Loads a comparability case JSON, builds a networkx evidence graph,
scores each attribute comparison, and generates an overall verdict
with confidence and residual uncertainties.

Usage:
    python -m modules.comparability_graph.engine --input benchmarks/thermal_stability_case.json
    python -m modules.comparability_graph.engine --input case.json --output verdict.json
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False

try:
    import numpy as np
    HAS_NP = True
except ImportError:
    HAS_NP = False


# =========================================================================
# Scoring Configuration
# =========================================================================

# Category weights for overall verdict
CATEGORY_WEIGHTS = {
    "identity": 0.10,
    "purity": 0.25,
    "potency": 0.25,
    "safety": 0.15,
    "stability": 0.15,
    "physicochemical": 0.10,
}

# Default tolerance by category (% relative difference)
DEFAULT_TOLERANCES = {
    "identity": 1.0,       # tight — identity must match closely
    "purity": 5.0,         # moderate — SEC, CE-SDS
    "potency": 20.0,       # wider — bioassay variability
    "safety": 10.0,        # moderate — impurity specs
    "stability": 10.0,     # moderate — degradation rates
    "physicochemical": 5.0, # moderate — MW, pI, charge
}


# =========================================================================
# W1-3: Change-Type Expected Impact Lookup
# =========================================================================

# =========================================================================
# W2-3: Pathway Weight Override Loader
# =========================================================================

_PATHWAY_WEIGHTS: Optional[Dict] = None


def _load_pathway_weights() -> Dict:
    """Load pathway_weights.yaml (cached after first load)."""
    global _PATHWAY_WEIGHTS
    if _PATHWAY_WEIGHTS is not None:
        return _PATHWAY_WEIGHTS
    if not _HAS_YAML:
        _PATHWAY_WEIGHTS = {}
        return _PATHWAY_WEIGHTS
    config_path = Path(__file__).parent.parent.parent / "config" / "pathway_weights.yaml"
    if config_path.exists():
        with open(config_path) as f:
            _PATHWAY_WEIGHTS = _yaml.safe_load(f) or {}
    else:
        _PATHWAY_WEIGHTS = {}
    return _PATHWAY_WEIGHTS


def get_pathway_category_weights(lifecycle_stage: str) -> Optional[Dict[str, float]]:
    """Get category weight overrides for a regulatory pathway.

    Returns None if no pathway-specific weights are configured.
    """
    pw = _load_pathway_weights()
    entry = pw.get(lifecycle_stage)
    if entry and isinstance(entry, dict):
        return entry.get("emphasis")
    return None


_CHANGE_TYPE_EXPECTATIONS: Optional[Dict] = None

def _load_change_type_expectations() -> Dict:
    """Load change_type_expectations.yaml (cached after first load)."""
    global _CHANGE_TYPE_EXPECTATIONS
    if _CHANGE_TYPE_EXPECTATIONS is not None:
        return _CHANGE_TYPE_EXPECTATIONS
    if not _HAS_YAML:
        _CHANGE_TYPE_EXPECTATIONS = {}
        return _CHANGE_TYPE_EXPECTATIONS
    config_path = Path(__file__).parent.parent.parent / "config" / "change_type_expectations.yaml"
    if config_path.exists():
        with open(config_path) as f:
            _CHANGE_TYPE_EXPECTATIONS = _yaml.safe_load(f) or {}
    else:
        _CHANGE_TYPE_EXPECTATIONS = {}
    return _CHANGE_TYPE_EXPECTATIONS


def load_change_expectation(change_type: str, category: str) -> Optional[Dict]:
    """Look up expected impact for a change_type x category pair."""
    expectations = _load_change_type_expectations()
    ct_entry = expectations.get(change_type, {})
    return ct_entry.get(category, None)


# =========================================================================
# Data Classes
# =========================================================================

@dataclass
class AttributeScore:
    attribute_id: str
    name: str
    category: str
    comparable: bool
    score: float  # 0-1 (1 = identical)
    delta_pct: float
    within_spec: bool
    concern: str  # "none", "minor", "major", "critical"
    detail: str
    # Provenance fields (added for traceability)
    tolerance_used: float = 0.0          # category tolerance that was applied
    tolerance_source: str = ""           # "default" or "custom" or "specification" or "pharmacopeia"
    scoring_mode: str = ""               # "relative" or "low_value_absolute"
    concern_thresholds: Optional[Dict[str, float]] = None  # thresholds used for concern assignment
    # W1-1b: Specification awareness fields (all optional, backward compatible)
    spec_compliance: str = "no_spec"     # 'oos'/'marginal'/'within_spec'/'no_spec'
    spec_lower: Optional[float] = None
    spec_upper: Optional[float] = None
    spec_source: str = "none"            # 'product_spec'/'pharmacopeia'/'none'
    confidence_cap: Optional[float] = None  # tolerance-source-based confidence cap
    # W1-3: Change-type contextualization
    change_type_expectation: Optional[Dict[str, Any]] = None  # matched expectation entry
    # W2-1: Method adequacy (negative gate only)
    method_adequate: str = "unknown"     # 'adequate'/'marginal'/'inadequate'/'unknown'


@dataclass
class Verdict:
    comparison_id: str
    comparable: bool
    confidence: float
    overall_score: float
    n_attributes: int
    n_comparable: int
    n_concerns: int
    attribute_scores: List[AttributeScore]
    residual_uncertainties: List[str]
    narrative: str
    graph_stats: Dict[str, Any] = field(default_factory=dict)


# =========================================================================
# Graph Builder
# =========================================================================

def build_graph(case: Dict) -> "nx.DiGraph":
    """Build a networkx directed graph from a comparability case."""
    if not HAS_NX:
        raise ImportError("networkx required: pip install networkx")

    G = nx.DiGraph()

    # Add lot nodes
    for lot in case.get("lots", []):
        G.add_node(lot["lot_id"], type="lot",
                   site=lot.get("manufacturing_site", ""),
                   process=lot.get("process_version", ""))

    # Add attribute nodes with measurements
    for attr in case.get("attributes", []):
        attr_id = attr["attribute_id"]
        G.add_node(attr_id, type="attribute",
                   name=attr["name"], category=attr["category"],
                   measurements=attr["measurements"])

        # Link lots to attributes via measurement edges
        for m in attr["measurements"]:
            G.add_edge(m["lot_id"], attr_id,
                       relationship="measured_by",
                       value=m["value"], unit=m.get("unit", ""),
                       assay=m.get("assay", ""),
                       within_spec=m.get("within_spec", True))

    # Add inter-attribute edges
    for edge in case.get("edges", []):
        G.add_edge(edge["from_attribute"], edge["to_attribute"],
                   relationship=edge["relationship"],
                   confidence=edge.get("confidence", 0.5),
                   evidence_source=edge.get("evidence_source", ""))

    return G


# =========================================================================
# Attribute Scoring
# =========================================================================

def _compute_spec_compliance(post_val: float, spec_lower: Optional[float],
                             spec_upper: Optional[float]) -> str:
    """Compute spec compliance status for a post-change value."""
    if spec_lower is None and spec_upper is None:
        return 'no_spec'
    if (spec_lower is not None and post_val < spec_lower) or \
       (spec_upper is not None and post_val > spec_upper):
        return 'oos'
    if (spec_lower is not None and post_val < spec_lower * 1.05) or \
       (spec_upper is not None and post_val > spec_upper * 0.95):
        return 'marginal'
    return 'within_spec'


# Concern level ordering for hard-gate escalation
_CONCERN_ORDER = {"none": 0, "minor": 1, "major": 2, "critical": 3}
_CONCERN_FROM_ORDER = {0: "none", 1: "minor", 2: "major", 3: "critical"}

def _step_up_concern(current: str, steps: int = 1) -> str:
    """Escalate concern level by N steps, capped at critical."""
    level = _CONCERN_ORDER.get(current, 0) + steps
    return _CONCERN_FROM_ORDER.get(min(level, 3), "critical")


def score_attribute(attr: Dict, lots: List[Dict]) -> AttributeScore:
    """Score a single attribute comparison across lots."""
    measurements = attr.get("measurements", [])
    category = attr.get("category", "physicochemical")
    tolerance = DEFAULT_TOLERANCES.get(category, 10.0)

    # W1-1b: Optional spec limit fields
    spec_lower = attr.get("spec_lower", None)
    spec_upper = attr.get("spec_upper", None)
    spec_source = attr.get("spec_source", "none")  # 'product_spec'/'pharmacopeia'/'none'
    is_cqa = attr.get("is_cqa", False)

    if len(measurements) < 2:
        return AttributeScore(
            attribute_id=attr["attribute_id"], name=attr["name"],
            category=category, comparable=True, score=0.5,
            delta_pct=0, within_spec=True, concern="minor",
            detail="Insufficient data (need measurements from 2+ lots)",
        )

    # Extract numeric values
    values = []
    all_in_spec = True
    for m in measurements:
        v = m.get("value")
        if isinstance(v, (int, float)):
            values.append(float(v))
        if not m.get("within_spec", True):
            all_in_spec = False

    if len(values) < 2:
        return AttributeScore(
            attribute_id=attr["attribute_id"], name=attr["name"],
            category=category, comparable=True, score=0.5,
            delta_pct=0, within_spec=all_in_spec, concern="minor",
            detail="Non-numeric values — manual review needed",
        )

    # Compute delta
    ref_val = values[0]  # first lot = reference
    test_val = values[-1]  # last lot = test
    if abs(ref_val) > 1e-10:
        delta_pct = (test_val - ref_val) / abs(ref_val) * 100
    elif abs(test_val) < 1e-10:
        delta_pct = 0  # both near zero — no difference
    else:
        # Reference near zero, test non-zero: use absolute difference scaled
        # to tolerance rather than infinite relative change
        delta_pct = min(abs(test_val) * 10, 50)  # cap at 50% for near-zero refs

    # W1-1b: Compute spec_compliance
    spec_compliance = _compute_spec_compliance(test_val, spec_lower, spec_upper)

    # W1-1c: Three tolerance sources (priority order)
    tolerance_source = "default"
    confidence_cap = None
    if spec_source == 'product_spec' and spec_lower is not None and spec_upper is not None:
        # Priority 1: Product specification — use spec range as tolerance
        tolerance = (spec_upper - spec_lower) / 2
        tolerance_source = "specification"
        # No confidence cap for product spec
    elif spec_source == 'pharmacopeia' and spec_lower is not None and spec_upper is not None:
        # Priority 2: Reference/compendial — pharmacopeial limits
        tolerance = (spec_upper - spec_lower) / 2
        tolerance_source = "pharmacopeia"
        confidence_cap = 0.75
    else:
        # Priority 3: Generic (DEFAULT_TOLERANCES) — current behavior
        tolerance_source = "default"
        confidence_cap = 0.60

    # Score: how much of the tolerance is consumed
    # For low-absolute-value measurements (e.g., HMW 1.2% -> 1.6%),
    # use absolute delta instead of relative to avoid scoring tiny
    # absolute changes as catastrophic due to inflated relative deltas.
    LOW_VALUE_THRESHOLD = 5.0  # below this, absolute delta matters more
    if abs(ref_val) < LOW_VALUE_THRESHOLD and abs(test_val) < LOW_VALUE_THRESHOLD:
        abs_delta = abs(ref_val - test_val)
        tolerance_abs = tolerance  # treat tolerance as absolute when values are low
        score = max(0, 1.0 - abs_delta / max(tolerance_abs, 0.01))
        _scoring_mode = "low_value_absolute"
        _concern_thresholds = {"none": 0.8, "minor": 0.6, "major": 0.3, "critical": 0.0}
        # Map score to concern level using the same thresholds
        if score >= 0.8:
            concern = "none"
        elif score >= 0.6:
            concern = "minor"
        elif score >= 0.3:
            concern = "major"
        else:
            concern = "critical"
    else:
        abs_delta = abs(delta_pct)
        _scoring_mode = "relative"
        _concern_thresholds = {
            "none": f"delta <= {tolerance * 0.5:.1f}%",
            "minor": f"delta <= {tolerance:.1f}%",
            "major": f"delta <= {tolerance * 2:.1f}%",
            "critical": f"delta > {tolerance * 2:.1f}%",
        }
        if abs_delta <= tolerance * 0.5:
            score = 1.0 - (abs_delta / tolerance) * 0.2  # 0.8-1.0
            concern = "none"
        elif abs_delta <= tolerance:
            score = 0.6 + (1.0 - abs_delta / tolerance) * 0.2  # 0.6-0.8
            concern = "minor"
        elif abs_delta <= tolerance * 2:
            score = 0.3 + (1.0 - abs_delta / (tolerance * 2)) * 0.3  # 0.3-0.6
            concern = "major"
        else:
            score = max(0, 0.3 - (abs_delta - tolerance * 2) / 100)
            concern = "critical"

    # W2-1: Method adequacy — negative gate only (S-2 invariant)
    method_loq = attr.get('method_loq', None)       # limit of quantitation
    method_lod = attr.get('method_lod', None)        # limit of detection
    method_precision_cv = attr.get('method_precision_cv', None)

    _method_adequate = 'unknown'
    if method_loq is not None:
        _abs_delta_raw = abs(ref_val - test_val)
        if _abs_delta_raw < method_loq:
            _method_adequate = 'inadequate'  # delta below LOQ
            concern = max(concern, 'minor', key=lambda c: _CONCERN_ORDER.get(c, 0))
        elif _abs_delta_raw < method_loq * 1.5:
            _method_adequate = 'marginal'
            concern = max(concern, 'minor', key=lambda c: _CONCERN_ORDER.get(c, 0))
        else:
            _method_adequate = 'adequate'
    elif method_loq is None:
        _method_adequate = 'unknown'

    # W1-1a: Activate within_spec gate — OOS on CQA = hard gate (S-1)
    detail = (f"{attr['name']}: {ref_val:.3g} vs {test_val:.3g} "
              f"(delta={delta_pct:+.1f}%, tolerance={tolerance}%)")

    if not all_in_spec and is_cqa:
        concern = max(concern, 'major', key=lambda c: _CONCERN_ORDER.get(c, 0))
        score = min(score, 0.45)
        detail += ' [OOS on CQA — minimum major concern per S-1]'

    # W1-1b: Also gate on computed spec_compliance
    if spec_compliance == 'oos' and is_cqa:
        concern = max(concern, 'major', key=lambda c: _CONCERN_ORDER.get(c, 0))
        score = min(score, 0.45)
        if '[OOS on CQA' not in detail:
            detail += ' [OOS on CQA — minimum major concern per S-1]'

    # W1-1c: S-3 — generic tolerance transparency
    if tolerance_source == 'default':
        detail += ' [Assessment based on generic tolerances]'

    # W1-3b: Change-type contextualization
    change_type = attr.get("_change_type", "")
    _change_expectation = None
    if change_type:
        _change_expectation = load_change_expectation(change_type, category)
        if _change_expectation:
            _ct_expected = _change_expectation.get('expected', False)
            _ct_typical = _change_expectation.get('typical_range_pct', 0)
            _ct_abs_delta = abs(delta_pct)
            if not _ct_expected and _ct_abs_delta > _ct_typical:
                # Unexpected shift: escalate concern +1 step
                concern = _step_up_concern(concern, 1)
                detail += f' [Unexpected for {change_type}]'
            elif _ct_expected and _ct_abs_delta <= _ct_typical:
                # Expected shift: annotate only, NO attenuation
                detail += f' [Expected for {change_type}: within typical range]'

    # W2-1: Annotate method adequacy in detail when relevant
    if _method_adequate == 'inadequate':
        detail += ' [Delta below LOQ — method inadequate]'
    elif _method_adequate == 'marginal':
        detail += ' [Delta near LOQ — method marginal]'

    comparable = concern in ("none", "minor")

    return AttributeScore(
        attribute_id=attr["attribute_id"], name=attr["name"],
        category=category, comparable=comparable, score=round(score, 4),
        delta_pct=round(delta_pct, 2), within_spec=all_in_spec,
        concern=concern, detail=detail,
        tolerance_used=tolerance,
        tolerance_source=tolerance_source,
        scoring_mode=_scoring_mode,
        concern_thresholds=_concern_thresholds,
        # W1-1b: New fields stored for downstream use
        spec_compliance=spec_compliance,
        spec_lower=spec_lower,
        spec_upper=spec_upper,
        spec_source=spec_source,
        confidence_cap=confidence_cap,
        # W1-3: Change-type expectation match
        change_type_expectation=_change_expectation,
        # W2-1: Method adequacy
        method_adequate=_method_adequate,
    )


# =========================================================================
# Verdict Generation
# =========================================================================

def generate_verdict(case: Dict) -> Verdict:
    """Generate comparability verdict from a case."""
    comparison_id = case.get("comparison_id", "unknown")
    lots = case.get("lots", [])
    attributes = case.get("attributes", [])

    # Score each attribute
    attr_scores = []
    for attr in attributes:
        attr_scores.append(score_attribute(attr, lots))

    # Weighted overall score — average per category, then weight categories
    # (avoids double-counting when multiple attributes share a category)
    from collections import defaultdict
    cat_scores = defaultdict(list)
    for a in attr_scores:
        cat_scores[a.category].append(a.score)

    weighted_sum = 0
    weight_total = 0
    for cat, scores in cat_scores.items():
        w = CATEGORY_WEIGHTS.get(cat, 0.10)
        cat_avg = sum(scores) / len(scores)
        weighted_sum += cat_avg * w
        weight_total += w

    overall = weighted_sum / weight_total if weight_total > 0 else 0.5

    n_comparable = sum(1 for a in attr_scores if a.comparable)
    n_concerns = sum(1 for a in attr_scores if a.concern in ("major", "critical"))

    # Residual uncertainties
    uncertainties = []
    for a in attr_scores:
        if a.concern == "major":
            uncertainties.append(f"{a.name}: {a.concern} concern (delta={a.delta_pct:+.1f}%)")
        elif a.concern == "critical":
            uncertainties.append(f"{a.name}: CRITICAL — outside tolerance (delta={a.delta_pct:+.1f}%)")

    if len(attributes) < 5:
        uncertainties.append("Limited attribute coverage — consider testing more CQAs")

    # Overall verdict: score-based with concern override
    # - Base decision from weighted score (>= 0.7 = comparable)
    # - Any critical concern forces NOT COMPARABLE regardless of score
    # - Major concerns lower the threshold to 0.8
    has_critical = any(a.concern == "critical" for a in attr_scores)
    has_major = any(a.concern == "major" for a in attr_scores)

    if has_critical:
        comparable = False  # critical concerns always override
    elif has_major:
        comparable = overall >= 0.80  # stricter threshold when major concerns exist
    else:
        comparable = overall >= 0.70

    # Confidence: how much evidence supports the verdict
    # High confidence = many attributes tested + consistent scores
    data_completeness = n_comparable / max(len(attr_scores), 1)
    score_consistency = 1.0 - (max(a.score for a in attr_scores) - min(a.score for a in attr_scores)) if attr_scores else 0
    confidence = min(1.0, 0.5 * data_completeness + 0.3 * overall + 0.2 * max(0, score_consistency))

    # Narrative
    lot_names = " vs ".join(lot["lot_id"] for lot in lots[:2])
    if comparable:
        narrative = (f"Comparability assessment for {lot_names}: COMPARABLE. "
                    f"{n_comparable}/{len(attr_scores)} attributes comparable "
                    f"(overall score {overall:.2f}, confidence {confidence:.2f}). "
                    f"No major concerns identified.")
    else:
        narrative = (f"Comparability assessment for {lot_names}: NOT COMPARABLE. "
                    f"{n_concerns} concern(s) identified across {len(attr_scores)} attributes "
                    f"(overall score {overall:.2f}). "
                    f"Residual uncertainties: {'; '.join(uncertainties[:3])}.")

    # Graph stats
    graph_stats = {}
    if HAS_NX:
        try:
            G = build_graph(case)
            graph_stats = {
                "nodes": G.number_of_nodes(),
                "edges": G.number_of_edges(),
                "lot_nodes": sum(1 for _, d in G.nodes(data=True) if d.get("type") == "lot"),
                "attribute_nodes": sum(1 for _, d in G.nodes(data=True) if d.get("type") == "attribute"),
            }
        except Exception:
            pass

    return Verdict(
        comparison_id=comparison_id,
        comparable=comparable,
        confidence=round(confidence, 4),
        overall_score=round(overall, 4),
        n_attributes=len(attr_scores),
        n_comparable=n_comparable,
        n_concerns=n_concerns,
        attribute_scores=attr_scores,
        residual_uncertainties=uncertainties,
        narrative=narrative,
        graph_stats=graph_stats,
    )


# =========================================================================
# Report Generator
# =========================================================================

def generate_report(verdict: Verdict) -> str:
    """Generate markdown report from verdict."""
    lines = [
        f"# Comparability Evidence Graph — Verdict Report",
        f"\n**Comparison:** {verdict.comparison_id}",
        f"**Verdict:** {'COMPARABLE' if verdict.comparable else 'NOT COMPARABLE'}",
        f"**Overall Score:** {verdict.overall_score:.4f}",
        f"**Confidence:** {verdict.confidence:.4f}",
        f"**Attributes:** {verdict.n_comparable}/{verdict.n_attributes} comparable, "
        f"{verdict.n_concerns} concerns\n",
    ]

    if verdict.graph_stats:
        lines.append(f"**Graph:** {verdict.graph_stats.get('nodes', 0)} nodes, "
                    f"{verdict.graph_stats.get('edges', 0)} edges\n")

    lines.append("## Attribute Scores\n")
    lines.append("| Attribute | Category | Score | Delta | Concern | Within Spec |")
    lines.append("|-----------|----------|-------|-------|---------|-------------|")
    for a in verdict.attribute_scores:
        lines.append(f"| {a.name} | {a.category} | {a.score:.3f} | {a.delta_pct:+.1f}% | "
                    f"{a.concern} | {'Yes' if a.within_spec else 'No'} |")

    if verdict.residual_uncertainties:
        lines.append("\n## Residual Uncertainties\n")
        for u in verdict.residual_uncertainties:
            lines.append(f"- {u}")

    lines.append(f"\n## Narrative\n\n{verdict.narrative}")
    lines.append("\n---\n*Generated by Comparability Evidence Graph Engine.*")
    return "\n".join(lines) + "\n"


# =========================================================================
# Main
# =========================================================================

def main():
    parser = argparse.ArgumentParser(description="Comparability Evidence Graph Engine")
    parser.add_argument("--input", required=True, help="Comparability case JSON")
    parser.add_argument("--output", help="Verdict output JSON")
    parser.add_argument("--report", help="Markdown report output")
    parser.add_argument("--record-labels", action="store_true", help="Save LabelRecord for training")
    args = parser.parse_args()

    try:
        with open(args.input) as f:
            case = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Malformed JSON in {args.input}: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"ERROR: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Validate required fields
    for field in ["comparison_id", "lots", "attributes"]:
        if field not in case:
            print(f"ERROR: Missing required field '{field}' in input JSON", file=sys.stderr)
            sys.exit(1)

    verdict = generate_verdict(case)

    # Print summary
    print(f"Comparison: {verdict.comparison_id}")
    print(f"Verdict: {'COMPARABLE' if verdict.comparable else 'NOT COMPARABLE'}")
    print(f"Score: {verdict.overall_score:.4f}, Confidence: {verdict.confidence:.4f}")
    print(f"Attributes: {verdict.n_comparable}/{verdict.n_attributes} comparable, {verdict.n_concerns} concerns")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(asdict(verdict), f, indent=2)
        print(f"Verdict saved to {args.output}")

    if args.report:
        report = generate_report(verdict)
        with open(args.report, "w") as f:
            f.write(report)
        print(f"Report saved to {args.report}")
    else:
        print("\n" + generate_report(verdict))

    # Label recording (Stage 5B)
    if args.record_labels:
        try:
            from schemas.label_emitter import emit_label
            rid = emit_label("comparability_graph", asdict(verdict),
                            metadata={"input": args.input})
            print(f"Label saved: {rid}")
        except ImportError:
            print("WARNING: label_emitter not available, skipping label recording", file=sys.stderr)


if __name__ == "__main__":
    main()
