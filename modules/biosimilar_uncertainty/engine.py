"""
Biosimilar Residual Uncertainty Engine (#57)

Quantifies attribute-level uncertainty in biosimilar comparisons across
5 dimensions:
  1. Assay Coverage — how many orthogonal methods tested this attribute?
  2. Statistical Power — enough lots/replicates for robust comparison?
  3. Functional Impact — does analytical difference translate to clinical effect?
  4. Data Quality — within-assay variability, trending, outliers?
  5. Regulatory Precedent — has FDA/EMA accepted similar differences before?

Each dimension scored 0-1. Overall Residual Uncertainty = weighted composite.
Low uncertainty = strong evidence of similarity. High = gaps remain.

Reference: FDA Biosimilarity Guidance (2015), EMA CHMP/BWP/247713

Usage:
    python -m modules.biosimilar_uncertainty.engine --demo
    python -m modules.biosimilar_uncertainty.engine --input comparison.json
"""

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class AttributeUncertainty:
    attribute: str
    category: str
    assay_coverage: float  # 0-1 (1 = multiple orthogonal methods)
    statistical_power: float  # 0-1 (1 = adequate lots + replicates)
    functional_impact: float  # 0-1 (1 = well-characterized impact)
    data_quality: float  # 0-1 (1 = high quality, low variability)
    regulatory_precedent: float  # 0-1 (1 = strong precedent for acceptance)
    residual_uncertainty: float = 0.0
    confidence_level: str = ""  # high, medium, low
    recommendation: str = ""


DIMENSION_WEIGHTS = {
    "assay_coverage": 0.20,
    "statistical_power": 0.25,
    "functional_impact": 0.25,
    "data_quality": 0.15,
    "regulatory_precedent": 0.15,
}


# =========================================================================
# Molecule-Type-Specific Profiles (GAP-UNC-001)
# =========================================================================
# Each profile defines baseline adjustments for the 5 uncertainty dimensions.
# Positive values INCREASE the raw dimension score (reduce uncertainty).
# Negative values DECREASE the raw dimension score (increase uncertainty).
# These reflect inherent characteristics of each molecule class.

MOLECULE_TYPE_PROFILES = {
    "mAb": {
        # Standard mAb — well-characterized platform, strong regulatory precedent
        "assay_coverage": 0.0,
        "statistical_power": 0.0,
        "functional_impact": 0.0,
        "data_quality": 0.0,
        "regulatory_precedent": 0.0,
    },
    "fusion_protein": {
        # Fc-fusion proteins — more complex glycosylation, fewer precedents
        "assay_coverage": -0.05,
        "statistical_power": 0.0,
        "functional_impact": -0.10,  # functional readout harder to correlate
        "data_quality": -0.05,
        "regulatory_precedent": -0.10,  # fewer approved biosimilars
    },
    "adc": {
        # Antibody-drug conjugates — high complexity, limited biosimilar precedent
        "assay_coverage": -0.15,  # need drug-load, linker, payload assays
        "statistical_power": -0.05,
        "functional_impact": -0.15,  # dual mechanism complicates correlation
        "data_quality": -0.10,  # higher inherent variability
        "regulatory_precedent": -0.20,  # very few biosimilar ADCs
    },
    "peptide": {
        # Peptide therapeutics — simpler structure, well-characterized
        "assay_coverage": 0.10,  # fewer attributes to cover
        "statistical_power": 0.05,
        "functional_impact": 0.05,
        "data_quality": 0.10,  # lower variability typical
        "regulatory_precedent": 0.05,
    },
    "other": {
        # Catch-all for unclassified biologics
        "assay_coverage": -0.05,
        "statistical_power": 0.0,
        "functional_impact": -0.05,
        "data_quality": 0.0,
        "regulatory_precedent": -0.10,
    },
}


def score_attribute_uncertainty(
    attribute: str,
    category: str,
    n_methods: int = 1,
    n_lots_biosimilar: int = 3,
    n_lots_originator: int = 3,
    n_replicates: int = 2,
    cv_pct: float = 10.0,
    has_functional_correlation: bool = False,
    prior_approvals_with_similar_diff: int = 0,
    molecule_type: str = "mAb",
) -> AttributeUncertainty:
    """Score uncertainty for one biosimilar comparison attribute.

    Args:
        attribute: Name of the attribute being compared.
        category: CQA category (purity, potency, etc.).
        n_methods: Number of orthogonal analytical methods.
        n_lots_biosimilar: Number of biosimilar lots tested.
        n_lots_originator: Number of originator lots tested.
        n_replicates: Number of replicates per lot.
        cv_pct: Coefficient of variation (%).
        has_functional_correlation: Whether functional correlation is demonstrated.
        prior_approvals_with_similar_diff: Number of prior approvals with similar differences.
        molecule_type: Molecule class for profile-based adjustments.
            One of: 'mAb', 'fusion_protein', 'adc', 'peptide', 'other'.
            Defaults to 'mAb' for backward compatibility.
    """

    # Validate inputs
    n_methods = max(0, int(n_methods))
    n_lots_biosimilar = max(0, int(n_lots_biosimilar))
    n_lots_originator = max(0, int(n_lots_originator))
    n_replicates = max(1, int(n_replicates))
    cv_pct = max(0, float(cv_pct))

    # Look up molecule-type profile (default to mAb if unknown)
    profile = MOLECULE_TYPE_PROFILES.get(molecule_type, MOLECULE_TYPE_PROFILES["mAb"])

    # 1. Assay coverage: more orthogonal methods = lower uncertainty
    assay_cov = min(1.0, n_methods / 3.0)  # 3+ methods = full coverage
    assay_cov = max(0.0, min(1.0, assay_cov + profile["assay_coverage"]))

    # 2. Statistical power: lots x replicates
    total_data_points = (n_lots_biosimilar + n_lots_originator) * n_replicates
    stat_power = min(1.0, total_data_points / 30.0)  # 30+ data points = adequate
    stat_power = max(0.0, min(1.0, stat_power + profile["statistical_power"]))

    # 3. Functional impact: gradient scoring (not binary)
    # 0.0 = no data, 0.3 = no correlation, 0.6 = weak correlation, 0.8 = strong, 1.0 = causal
    if has_functional_correlation:
        func_impact = 0.8  # strong correlation demonstrated
    else:
        func_impact = 0.3  # no functional data — uncertainty remains
    func_impact = max(0.0, min(1.0, func_impact + profile["functional_impact"]))

    # 4. Data quality: inverse of CV (threshold 20% per FDA biosimilar guidance)
    # CV < 5% = excellent, 5-15% = good, 15-20% = acceptable, > 20% = poor
    data_qual = min(1.0, max(0, 1.0 - cv_pct / 20.0))  # CV=0→1.0, CV=20→0.0
    data_qual = max(0.0, min(1.0, data_qual + profile["data_quality"]))

    # 5. Regulatory precedent
    reg_prec = min(1.0, prior_approvals_with_similar_diff / 3.0)  # 3+ approvals = strong
    reg_prec = max(0.0, min(1.0, reg_prec + profile["regulatory_precedent"]))

    # Composite: residual uncertainty = 1 - weighted evidence score
    evidence = (
        DIMENSION_WEIGHTS["assay_coverage"] * assay_cov
        + DIMENSION_WEIGHTS["statistical_power"] * stat_power
        + DIMENSION_WEIGHTS["functional_impact"] * func_impact
        + DIMENSION_WEIGHTS["data_quality"] * data_qual
        + DIMENSION_WEIGHTS["regulatory_precedent"] * reg_prec
    )
    residual = 1.0 - evidence

    if residual <= 0.25:
        level = "low"
        rec = "Evidence is strong. Standard analytical similarity package sufficient."
    elif residual <= 0.50:
        level = "medium"
        rec = "Some gaps remain. Consider additional characterization or functional studies."
    else:
        level = "high"
        rec = "Significant uncertainty. Additional studies required before filing."

    return AttributeUncertainty(
        attribute=attribute, category=category,
        assay_coverage=round(assay_cov, 3),
        statistical_power=round(stat_power, 3),
        functional_impact=round(func_impact, 3),
        data_quality=round(data_qual, 3),
        regulatory_precedent=round(reg_prec, 3),
        residual_uncertainty=round(residual, 3),
        confidence_level=level,
        recommendation=rec,
    )


# Demo: typical biosimilar trastuzumab assessment
DEMO_ATTRIBUTES = [
    {"attribute": "SEC Monomer %", "category": "purity", "n_methods": 2,
     "n_lots_biosimilar": 10, "n_lots_originator": 10, "n_replicates": 3,
     "cv_pct": 1.5, "has_functional_correlation": False, "prior_approvals_with_similar_diff": 5},
    {"attribute": "Potency (ADCC)", "category": "potency", "n_methods": 1,
     "n_lots_biosimilar": 5, "n_lots_originator": 5, "n_replicates": 3,
     "cv_pct": 18.0, "has_functional_correlation": True, "prior_approvals_with_similar_diff": 3},
    {"attribute": "Afucosylation %", "category": "physicochemical", "n_methods": 2,
     "n_lots_biosimilar": 10, "n_lots_originator": 10, "n_replicates": 2,
     "cv_pct": 8.0, "has_functional_correlation": True, "prior_approvals_with_similar_diff": 4},
    {"attribute": "Charge Variants (cIEF)", "category": "physicochemical", "n_methods": 1,
     "n_lots_biosimilar": 10, "n_lots_originator": 8, "n_replicates": 2,
     "cv_pct": 5.0, "has_functional_correlation": False, "prior_approvals_with_similar_diff": 5},
    {"attribute": "FcRn Binding", "category": "potency", "n_methods": 1,
     "n_lots_biosimilar": 3, "n_lots_originator": 3, "n_replicates": 2,
     "cv_pct": 12.0, "has_functional_correlation": True, "prior_approvals_with_similar_diff": 2},
    {"attribute": "Thermal Stability (Tm1)", "category": "stability", "n_methods": 1,
     "n_lots_biosimilar": 5, "n_lots_originator": 5, "n_replicates": 2,
     "cv_pct": 2.0, "has_functional_correlation": False, "prior_approvals_with_similar_diff": 3},
]


def main():
    parser = argparse.ArgumentParser(description="Biosimilar Residual Uncertainty Engine")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--input", help="Comparison data JSON")
    parser.add_argument("--output", help="Output JSON")
    args = parser.parse_args()

    if args.demo:
        attributes = DEMO_ATTRIBUTES
    elif args.input:
        with open(args.input) as f:
            attributes = json.load(f)
    else:
        parser.print_help()
        return

    results = [score_attribute_uncertainty(**a) for a in attributes]
    results.sort(key=lambda r: r.residual_uncertainty, reverse=True)

    avg_uncertainty = sum(r.residual_uncertainty for r in results) / len(results) if results else 0

    print(f"Biosimilar Residual Uncertainty Assessment")
    print(f"Attributes: {len(results)}, Average uncertainty: {avg_uncertainty:.3f}")
    print()
    print(f"{'Attribute':<25} {'Uncertainty':>12} {'Level':<8} {'Recommendation'}")
    print("-" * 90)
    for r in results:
        print(f"{r.attribute:<25} {r.residual_uncertainty:>12.3f} {r.confidence_level:<8} {r.recommendation[:45]}...")

    if args.output:
        with open(args.output, "w") as f:
            json.dump([asdict(r) for r in results], f, indent=2)
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
