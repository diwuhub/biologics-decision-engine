"""
Reviewer Question Templates for Regulatory Evidence Service.

30+ reviewer question templates organized by CMC evidence category.
Each template captures common FDA/EMA reviewer questions triggered
by specific comparability findings. Used by the Regulatory Evidence
Service to predict likely reviewer questions from comparability reports.

Categories: purity, potency, stability, safety, physicochemical, identity,
            process_validation, specifications.

ARCHIVED (Phase 5): Replaced by services/reviewer_concern_engine.py which
generates case-specific concerns from RiskClusters rather than using
template matching. This module is retained for backward compatibility
with any code that still imports ReviewerTemplate directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ReviewerTemplate:
    """A single reviewer question template.

    Attributes
    ----------
    id : str
        Unique template identifier (e.g. "RT-PUR-001").
    category : str
        CMC evidence category this question applies to.
    subcategory : str
        Specific analytical method or area (e.g. "SEC", "CE-SDS").
    question_template : str
        Template string with {placeholders} for dynamic values.
    trigger_condition : str
        Description of when this question is triggered.
    severity : str
        "critical" | "high" | "medium" | "low"
    suggested_response_approach : str
        Guidance on how to address the question.
    ich_reference : str
        Applicable ICH guideline reference.
    delta_threshold_pct : float or None
        If set, the minimum delta (%) that triggers this question.
    """
    id: str
    category: str
    subcategory: str
    question_template: str
    trigger_condition: str
    severity: str
    suggested_response_approach: str
    ich_reference: str = ""
    delta_threshold_pct: Optional[float] = None


# =========================================================================
# Template Registry
# =========================================================================

REVIEWER_TEMPLATES: List[ReviewerTemplate] = [
    # -----------------------------------------------------------------
    # Purity -- SEC
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-PUR-001",
        category="purity",
        subcategory="SEC",
        question_template=(
            "What is the root cause of the {delta_pct:.1f}% change in "
            "SEC monomer purity from {pre_value:.1f}% to {post_value:.1f}%?"
        ),
        trigger_condition="SEC monomer delta > 0.5%",
        severity="high",
        suggested_response_approach=(
            "Provide root-cause analysis including HMW/LMW species "
            "characterization; show lot-to-lot variability covers observed delta."
        ),
        ich_reference="ICH Q5E Section 2.2; ICH Q6B Section 3.1",
        delta_threshold_pct=0.5,
    ),
    ReviewerTemplate(
        id="RT-PUR-002",
        category="purity",
        subcategory="SEC",
        question_template=(
            "Please provide characterization data for the new HMW species "
            "observed in post-change SEC profiles for {attribute_name}."
        ),
        trigger_condition="New HMW species detected or HMW increase > 0.3%",
        severity="critical",
        suggested_response_approach=(
            "Multi-angle light scattering (MALS) data on HMW species; "
            "demonstrate no new species, only known aggregates."
        ),
        ich_reference="ICH Q6B Section 3.1",
        delta_threshold_pct=0.3,
    ),
    ReviewerTemplate(
        id="RT-PUR-003",
        category="purity",
        subcategory="SEC",
        question_template=(
            "How does the post-change aggregate level compare to the approved "
            "specification limit for {attribute_name}?"
        ),
        trigger_condition="HMW/aggregate increase approaching specification limit",
        severity="high",
        suggested_response_approach=(
            "Tabulate post-change values vs specification limits; show margin "
            "to spec; include trending data from multiple lots."
        ),
        ich_reference="ICH Q5E Section 2.2",
        delta_threshold_pct=0.5,
    ),
    # -----------------------------------------------------------------
    # Purity -- CE-SDS
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-PUR-004",
        category="purity",
        subcategory="CE-SDS",
        question_template=(
            "What is the root cause of the {delta_pct:.1f}% change in "
            "CE-SDS purity from {pre_value:.1f}% to {post_value:.1f}%?"
        ),
        trigger_condition="CE-SDS main peak delta > 1.0%",
        severity="high",
        suggested_response_approach=(
            "Identify impurity bands; correlate with process parameter changes; "
            "show orthogonal confirmation (e.g., reduced/non-reduced comparison)."
        ),
        ich_reference="ICH Q6B Section 3.1",
        delta_threshold_pct=1.0,
    ),
    ReviewerTemplate(
        id="RT-PUR-005",
        category="purity",
        subcategory="CE-SDS",
        question_template=(
            "Please provide reduced and non-reduced CE-SDS comparison data "
            "for pre-change and post-change {attribute_name}."
        ),
        trigger_condition="Fragment pattern change detected in CE-SDS",
        severity="medium",
        suggested_response_approach=(
            "Side-by-side electropherograms with band identification; "
            "quantitate each species as percentage of total."
        ),
        ich_reference="ICH Q6B Section 2.1",
        delta_threshold_pct=1.5,
    ),
    # -----------------------------------------------------------------
    # Purity -- Charge Variants
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-PUR-006",
        category="purity",
        subcategory="charge_variants",
        question_template=(
            "Please explain the {delta_pct:.1f}% shift in acidic variants "
            "observed in post-change material for {attribute_name}."
        ),
        trigger_condition="Acidic or basic variant shift > 3%",
        severity="medium",
        suggested_response_approach=(
            "Characterize variant species (deamidation, oxidation, etc.); "
            "correlate with potency; show no impact on safety or efficacy."
        ),
        ich_reference="ICH Q6B Section 2.1",
        delta_threshold_pct=3.0,
    ),
    # -----------------------------------------------------------------
    # Potency
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-POT-001",
        category="potency",
        subcategory="bioassay",
        question_template=(
            "Please provide functional correlation data for the "
            "{delta_pct:.1f}% change in potency observed for {attribute_name}."
        ),
        trigger_condition="Potency delta > 10% or outside 80-120% range",
        severity="critical",
        suggested_response_approach=(
            "Cell-based potency assay with dose-response curves; "
            "receptor binding data; demonstrate functional equivalence."
        ),
        ich_reference="ICH Q5E Section 2.3; ICH Q6B Section 2.2",
        delta_threshold_pct=10.0,
    ),
    ReviewerTemplate(
        id="RT-POT-002",
        category="potency",
        subcategory="binding",
        question_template=(
            "What is the impact of the observed quality attribute changes on "
            "target binding affinity for {attribute_name}?"
        ),
        trigger_condition="Binding assay delta > 15% or potency flag",
        severity="high",
        suggested_response_approach=(
            "SPR or ELISA binding kinetics (ka, kd, KD) for pre vs post; "
            "show binding is within historical range."
        ),
        ich_reference="ICH Q5E Section 2.3",
        delta_threshold_pct=15.0,
    ),
    ReviewerTemplate(
        id="RT-POT-003",
        category="potency",
        subcategory="bioassay",
        question_template=(
            "Has the reference standard been re-qualified against the "
            "post-change material for {attribute_name}?"
        ),
        trigger_condition="Potency shift detected; reference standard age > 2 years",
        severity="medium",
        suggested_response_approach=(
            "Provide reference standard qualification history; "
            "bridging study between old and new reference standard."
        ),
        ich_reference="ICH Q6B Section 4",
        delta_threshold_pct=5.0,
    ),
    ReviewerTemplate(
        id="RT-POT-004",
        category="potency",
        subcategory="functional",
        question_template=(
            "Please demonstrate that the Fc effector function is maintained "
            "post-change for {attribute_name}."
        ),
        trigger_condition="Glycosylation or Fc-related attribute change",
        severity="high",
        suggested_response_approach=(
            "ADCC, CDC, or FcRn binding assays comparing pre and post; "
            "correlation with glycan profile changes."
        ),
        ich_reference="ICH Q5E Section 2.3; FDA Biosimilarity Guidance",
        delta_threshold_pct=5.0,
    ),
    # -----------------------------------------------------------------
    # Stability
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-STB-001",
        category="stability",
        subcategory="accelerated",
        question_template=(
            "What accelerated stability data supports the comparability "
            "conclusion for {attribute_name} given the {delta_pct:.1f}% change?"
        ),
        trigger_condition="Any DEFER/INVESTIGATE action on stability attribute",
        severity="critical",
        suggested_response_approach=(
            "Side-by-side accelerated stability (25C/60%RH, 40C/75%RH) for "
            "pre and post at 0, 1, 3, 6 months; show comparable degradation rates."
        ),
        ich_reference="ICH Q5E Section 3; ICH Q5C",
        delta_threshold_pct=2.0,
    ),
    ReviewerTemplate(
        id="RT-STB-002",
        category="stability",
        subcategory="real_time",
        question_template=(
            "Please provide real-time stability data for post-change material "
            "covering at least 6 months for {attribute_name}."
        ),
        trigger_condition="Only accelerated stability available; no real-time data",
        severity="high",
        suggested_response_approach=(
            "Commitment to ongoing real-time stability studies; provide "
            "available data with projected shelf life analysis."
        ),
        ich_reference="ICH Q5E Section 3; ICH Q1A",
        delta_threshold_pct=1.0,
    ),
    ReviewerTemplate(
        id="RT-STB-003",
        category="stability",
        subcategory="degradation",
        question_template=(
            "Is the degradation pathway the same for pre-change and post-change "
            "material based on forced degradation studies for {attribute_name}?"
        ),
        trigger_condition="Stability attribute with concern level major or above",
        severity="high",
        suggested_response_approach=(
            "Forced degradation study comparison (acid, base, oxidation, "
            "thermal, photo); overlay degradation profiles."
        ),
        ich_reference="ICH Q5E Section 3; ICH Q1B",
        delta_threshold_pct=3.0,
    ),
    ReviewerTemplate(
        id="RT-STB-004",
        category="stability",
        subcategory="shelf_life",
        question_template=(
            "What is the proposed shelf life for post-change product and "
            "what data supports it for {attribute_name}?"
        ),
        trigger_condition="Stability delta suggests potential shelf-life impact",
        severity="critical",
        suggested_response_approach=(
            "Statistical shelf-life estimation per ICH Q1E; "
            "demonstrate post-change product meets same expiry."
        ),
        ich_reference="ICH Q5E Section 3; ICH Q1E",
        delta_threshold_pct=2.0,
    ),
    # -----------------------------------------------------------------
    # Safety -- HCP
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-SAF-001",
        category="safety",
        subcategory="HCP",
        question_template=(
            "Please justify the increase in host cell protein levels from "
            "{pre_value:.1f} to {post_value:.1f} ppm for {attribute_name}."
        ),
        trigger_condition="HCP increase > 10 ppm or > 20% relative increase",
        severity="critical",
        suggested_response_approach=(
            "Orthogonal HCP analysis (2D-PAGE, LC-MS/MS); identify specific "
            "HCP species; assess immunogenicity risk."
        ),
        ich_reference="ICH Q6B Section 3.1; ICH Q5E Section 2.4",
        delta_threshold_pct=20.0,
    ),
    ReviewerTemplate(
        id="RT-SAF-002",
        category="safety",
        subcategory="HCP",
        question_template=(
            "Has the HCP ELISA been qualified for post-change process "
            "coverage for {attribute_name}?"
        ),
        trigger_condition="Process change affecting upstream; HCP method suitability",
        severity="high",
        suggested_response_approach=(
            "HCP ELISA coverage/suitability study; demonstrate antibody "
            "reactivity against post-change HCP population."
        ),
        ich_reference="ICH Q6B Section 3.1",
        delta_threshold_pct=10.0,
    ),
    # -----------------------------------------------------------------
    # Safety -- Endotoxin / DNA
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-SAF-003",
        category="safety",
        subcategory="endotoxin",
        question_template=(
            "Please justify the increase in endotoxin levels observed in "
            "post-change material for {attribute_name}."
        ),
        trigger_condition="Endotoxin increase > 0.5 EU/mg or approaching spec limit",
        severity="critical",
        suggested_response_approach=(
            "Root-cause investigation of endotoxin source; demonstrate "
            "levels remain well within specification and pharmacopeial limits."
        ),
        ich_reference="ICH Q6B; USP <85>",
        delta_threshold_pct=30.0,
    ),
    ReviewerTemplate(
        id="RT-SAF-004",
        category="safety",
        subcategory="residual_DNA",
        question_template=(
            "What is the residual DNA clearance for post-change material "
            "and how does it compare to pre-change for {attribute_name}?"
        ),
        trigger_condition="DNA level increase or process change affecting purification",
        severity="high",
        suggested_response_approach=(
            "Quantitative PCR data for residual DNA; demonstrate clearance "
            "within WHO/ICH limits (<10 ng/dose)."
        ),
        ich_reference="ICH Q6B Section 3.1; WHO TRS 878",
        delta_threshold_pct=25.0,
    ),
    # -----------------------------------------------------------------
    # Physicochemical
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-PHY-001",
        category="physicochemical",
        subcategory="glycosylation",
        question_template=(
            "Characterize the glycosylation shift observed in post-change "
            "material for {attribute_name} (delta {delta_pct:.1f}%)."
        ),
        trigger_condition="Glycan profile shift > 5% in any species",
        severity="high",
        suggested_response_approach=(
            "Released N-glycan profiling (HILIC-FLR or CE-LIF); "
            "site-specific glycosylation analysis; correlate with effector function."
        ),
        ich_reference="ICH Q6B Section 2.1; ICH Q5E Section 2.1",
        delta_threshold_pct=5.0,
    ),
    ReviewerTemplate(
        id="RT-PHY-002",
        category="physicochemical",
        subcategory="glycosylation",
        question_template=(
            "What is the impact of the glycosylation change on "
            "FcRn binding and serum half-life for {attribute_name}?"
        ),
        trigger_condition="Galactosylation or sialylation shift > 10%",
        severity="high",
        suggested_response_approach=(
            "FcRn binding assay at pH 6.0 and 7.4; PK modeling if "
            "available; literature support for glycan-PK relationship."
        ),
        ich_reference="ICH Q5E Section 2.3",
        delta_threshold_pct=10.0,
    ),
    ReviewerTemplate(
        id="RT-PHY-003",
        category="physicochemical",
        subcategory="mass_spec",
        question_template=(
            "Please provide intact mass and peptide mapping data comparing "
            "pre-change and post-change material for {attribute_name}."
        ),
        trigger_condition="Identity or primary structure change suspected",
        severity="medium",
        suggested_response_approach=(
            "Intact mass spectrometry; reduced/deglycosylated intact mass; "
            "tryptic peptide mapping with MS/MS confirmation."
        ),
        ich_reference="ICH Q6B Section 2.1",
        delta_threshold_pct=0.1,
    ),
    ReviewerTemplate(
        id="RT-PHY-004",
        category="physicochemical",
        subcategory="higher_order_structure",
        question_template=(
            "What higher-order structure data supports comparability "
            "for {attribute_name}?"
        ),
        trigger_condition="Process change affecting folding conditions or formulation",
        severity="medium",
        suggested_response_approach=(
            "Far-UV CD, DSC, FTIR, or HDX-MS comparing pre and post; "
            "demonstrate no conformational change."
        ),
        ich_reference="ICH Q6B Section 2.1; ICH Q5E",
        delta_threshold_pct=2.0,
    ),
    # -----------------------------------------------------------------
    # Identity
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-IDN-001",
        category="identity",
        subcategory="primary_structure",
        question_template=(
            "Please confirm primary structure identity between pre-change "
            "and post-change material for {attribute_name}."
        ),
        trigger_condition="Identity assay flag or process change affecting expression",
        severity="critical",
        suggested_response_approach=(
            "Peptide mapping with >95% sequence coverage; N-terminal sequencing; "
            "intact mass comparison."
        ),
        ich_reference="ICH Q6B Section 2.1; ICH Q5E Section 2.1",
        delta_threshold_pct=0.1,
    ),
    ReviewerTemplate(
        id="RT-IDN-002",
        category="identity",
        subcategory="post_translational",
        question_template=(
            "Have post-translational modifications been fully characterized "
            "in post-change material for {attribute_name}?"
        ),
        trigger_condition="PTM-related attribute shift detected",
        severity="high",
        suggested_response_approach=(
            "Site-specific PTM analysis (deamidation, oxidation, "
            "isomerization); compare modification rates."
        ),
        ich_reference="ICH Q6B Section 2.1",
        delta_threshold_pct=2.0,
    ),
    # -----------------------------------------------------------------
    # Process Validation
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-PV-001",
        category="process_validation",
        subcategory="lot_count",
        question_template=(
            "How many post-change lots have been manufactured and tested "
            "for {attribute_name}? Please justify the number."
        ),
        trigger_condition="Fewer than 3 post-change lots available",
        severity="high",
        suggested_response_approach=(
            "Provide lot genealogy; justify lot count per FDA process "
            "validation guidance; commit to additional lots if needed."
        ),
        ich_reference="FDA Process Validation Guidance (2011); ICH Q5E",
        delta_threshold_pct=None,
    ),
    ReviewerTemplate(
        id="RT-PV-002",
        category="process_validation",
        subcategory="process_parameters",
        question_template=(
            "What process parameters changed and what is the impact on "
            "product quality for {attribute_name}?"
        ),
        trigger_condition="Process change with quality attribute shift",
        severity="medium",
        suggested_response_approach=(
            "Process parameter comparison table; risk assessment per "
            "ICH Q9; demonstrate no impact on CQAs."
        ),
        ich_reference="ICH Q5E; ICH Q9; ICH Q11",
        delta_threshold_pct=None,
    ),
    # -----------------------------------------------------------------
    # Specifications
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-SPC-001",
        category="specifications",
        subcategory="acceptance_criteria",
        question_template=(
            "Do the current specifications adequately control the post-change "
            "quality profile for {attribute_name}?"
        ),
        trigger_condition="Post-change value approaching specification limit",
        severity="high",
        suggested_response_approach=(
            "Compare post-change values to specification limits; "
            "justify that existing specifications remain appropriate or propose updates."
        ),
        ich_reference="ICH Q6B; ICH Q5E Section 2",
        delta_threshold_pct=None,
    ),
    ReviewerTemplate(
        id="RT-SPC-002",
        category="specifications",
        subcategory="analytical_method",
        question_template=(
            "Has the analytical method for {attribute_name} been validated "
            "or verified for use with post-change material?"
        ),
        trigger_condition="Method suitability concern or matrix change",
        severity="medium",
        suggested_response_approach=(
            "Method validation/verification summary for post-change matrix; "
            "demonstrate accuracy, precision, specificity, linearity."
        ),
        ich_reference="ICH Q2(R1); ICH Q6B",
        delta_threshold_pct=None,
    ),
    # -----------------------------------------------------------------
    # General / Cross-cutting
    # -----------------------------------------------------------------
    ReviewerTemplate(
        id="RT-GEN-001",
        category="general",
        subcategory="lot_selection",
        question_template=(
            "Please justify the lot selection strategy for the comparability "
            "study for {attribute_name}."
        ),
        trigger_condition="Statistical concern about lot representativeness",
        severity="medium",
        suggested_response_approach=(
            "Describe lot selection rationale; show lots are representative "
            "of typical manufacturing; include range and variability data."
        ),
        ich_reference="ICH Q5E",
        delta_threshold_pct=None,
    ),
    ReviewerTemplate(
        id="RT-GEN-002",
        category="general",
        subcategory="statistical_analysis",
        question_template=(
            "What statistical methods were used to evaluate comparability "
            "for {attribute_name} and are they appropriate?"
        ),
        trigger_condition="Statistical significance vs practical significance discrepancy",
        severity="medium",
        suggested_response_approach=(
            "Describe statistical approach (equivalence testing, tolerance "
            "intervals); justify sample size and significance criteria."
        ),
        ich_reference="ICH Q5E; FDA Statistical Approaches to Evaluate Comparability",
        delta_threshold_pct=None,
    ),
]


# =========================================================================
# Lookup helpers
# =========================================================================

_TEMPLATES_BY_CATEGORY: dict[str, list[ReviewerTemplate]] = {}
for _t in REVIEWER_TEMPLATES:
    _TEMPLATES_BY_CATEGORY.setdefault(_t.category, []).append(_t)


def get_templates_by_category(category: str) -> list[ReviewerTemplate]:
    """Return all templates matching a given category."""
    return _TEMPLATES_BY_CATEGORY.get(category, [])


def get_all_templates() -> list[ReviewerTemplate]:
    """Return all reviewer question templates."""
    return list(REVIEWER_TEMPLATES)


def match_templates(
    category: str,
    delta_pct: float,
    concern: str = "none",
    action: str = "",
) -> list[ReviewerTemplate]:
    """Return templates whose trigger conditions match the given context.

    Parameters
    ----------
    category : str
        CMC category (e.g. "purity", "potency").
    delta_pct : float
        Absolute percentage delta for the attribute.
    concern : str
        Concern level ("none", "minor", "major", "critical").
    action : str
        Action recommendation ("PROCEED", "SUPPLEMENT", "INVESTIGATE", "DEFER").

    Returns
    -------
    list[ReviewerTemplate]
        Matching templates, ordered by severity (critical first).
    """
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    abs_delta = abs(delta_pct)
    matched = []

    candidates = _TEMPLATES_BY_CATEGORY.get(category, [])
    # Also include general templates for non-PROCEED actions
    if action not in ("", "PROCEED"):
        candidates = candidates + _TEMPLATES_BY_CATEGORY.get("general", [])

    for t in candidates:
        triggered = False
        # Delta-based trigger
        if t.delta_threshold_pct is not None and abs_delta >= t.delta_threshold_pct:
            triggered = True
        # Concern-based trigger: major/critical always triggers high+ templates
        if concern in ("major", "critical") and t.severity in ("critical", "high"):
            triggered = True
        # Action-based trigger: DEFER/INVESTIGATE triggers all templates for category
        if action in ("DEFER", "INVESTIGATE"):
            triggered = True
        if triggered:
            matched.append(t)

    # Deduplicate by id
    seen = set()
    unique = []
    for t in matched:
        if t.id not in seen:
            seen.add(t.id)
            unique.append(t)

    unique.sort(key=lambda t: severity_order.get(t.severity, 99))
    return unique
