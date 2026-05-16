"""
Biologics Decision Engine -- FastAPI REST API (v1).

Exposes the comparability assessment and gap memo pipelines over HTTP,
plus evidence grading and benchmark listing.

Run:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import sys
import traceback
import uuid
from typing import Annotated, Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

# Ensure project root is importable
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


app = FastAPI(
    title="Biologics Decision Engine API",
    description=(
        "Decision-grade reasoning for biologics CMC comparability "
        "and regulatory review. Exposes the comparability assessment, "
        "gap memo, evidence grading, and benchmark pipelines over HTTP."
    ),
    version="1.0.0",
)


# =========================================================================
# Request / Response Models
# =========================================================================

class AttributeInput(BaseModel):
    """A single quality attribute — SP v5 schema."""
    name: str
    category: str = "physicochemical"
    pre_value: float
    post_value: float
    unit: str = ""
    n_lots: int = 3
    cv_pct: float = 5.0
    n_methods: int = 1
    functional_support_level: str = "none"   # none/weak/indirect/direct (SP v5)
    orthogonal_coverage: str = "none"        # none/partial/strong (SP v5)
    # REMOVED: has_functional_correlation (replaced by functional_support_level)
    # REMOVED: prior_approvals (now derived from Layer 2)


class ComparabilityRequest(BaseModel):
    """Input for the comparability assessment pipeline — SP v5 schema."""
    product_name: str = "Product"
    molecule_class: str = "mAb"              # SP v5 new field
    modality: str = "IV"                     # SP v5 new field
    reference_product: str = ""              # SP v5 new field
    change_description: str = ""
    attributes: List[AttributeInput]


class SectionInput(BaseModel):
    """A single CTD section for gap memo review."""
    name: str
    title: str = ""
    content: str


class GapMemoRequest(BaseModel):
    """Input for the gap memo pipeline."""
    product_name: str = "Product"
    submission_type: str = "BLA"
    product_type: str = "mAb"
    sections: List[SectionInput]


class GradeEvidenceRequest(BaseModel):
    """Input for evidence grading."""
    claim: str


# =========================================================================
# Case Management Models (Phase A endpoints)
# =========================================================================

class CreateCaseRequest(BaseModel):
    """Create a new assessment case."""
    product_name: str
    product_type: str = "mAb"  # mAb, recombinant protein, etc.
    molecule_class: str = "mAb"
    change_type: str = "formulation"  # formulation / site / process / etc.
    product_stage: str = "commercial"  # clinical / commercial / legacy
    batch_data: Dict[str, Any]  # Harmonized batch data (CSV parsed to JSON)
    change_description: str = ""


class CaseSummary(BaseModel):
    """Brief case metadata for list view."""
    case_id: str
    product_name: str
    molecule_class: str
    change_type: str
    status: str
    overall_action: str  # "Proceed" / "Collect Evidence" / "Halt Review"
    critical_gaps_count: int
    last_updated: str


class CaseListResponse(BaseModel):
    """Response for GET /api/cases"""
    cases: List[CaseSummary]
    total_count: int


class JudgmentSummary(BaseModel):
    """The core verdict block (Package Overview P0)."""
    verdict: str  # "comparable" / "comparable_with_caveats" / "not_comparable"
    confidence: float  # 0-1 (maps to evidence_strength_index)
    overall_action: str  # "Proceed" / "Collect Evidence" / "Halt"
    key_finding: str  # One sentence
    # Phase 4D: Judgment Core fields (optional for backward compatibility)
    judgment_core_verdict: Optional[str] = None  # 5-level JC verdict
    confidence_band: Optional[str] = None  # high/moderate/low
    blocking_clusters: Optional[List[Dict[str, Any]]] = None
    abstain_flag: Optional[bool] = None
    decision_rule_ids: Optional[List[str]] = None
    what_would_change: Optional[List[Dict[str, Any]]] = None


class TopGap(BaseModel):
    """High-level gap for Package Overview."""
    attribute: str
    gap_type: str  # "missing_data" / "outlier" / "functional_support" / "precedent"
    severity: str  # "critical" / "high" / "medium" / "low"
    suggested_action: str


class CriticalAttribute(BaseModel):
    """Scored attribute with decision logic (Package Overview)."""
    name: str
    category: str  # physicochemical / functional / etc.
    score: float  # 0-1 (from AttributeResult.score)
    uncertainty: float  # 0-1 (from AttributeResult.uncertainty)
    action: str  # "Acceptable" / "Monitor" / "Investigate" / "Not Comparable"
    is_cqa: bool


class PredictedQuestion(BaseModel):
    """Likely reviewer question from gap memo analysis."""
    question: str
    probability: float  # 0-1
    impact: str  # "blocking" / "conditional" / "clarification"


class ReviewerRisk(BaseModel):
    """Predicted regulatory risk block (Package Overview)."""
    predicted_questions: List[PredictedQuestion]


class ProvenanceSnapshot(BaseModel):
    """Summary of evidence sources (Package Overview)."""
    sources_count: int
    precedents_cited: int
    guidelines_referenced: int


class PackageOverviewResponse(BaseModel):
    """Response for GET /api/cases/{id}/overview -- THE CORE ENDPOINT."""
    case_id: str
    judgment_summary: JudgmentSummary
    top_gaps: List[TopGap]
    critical_attributes: List[CriticalAttribute]
    reviewer_risk: ReviewerRisk
    provenance_snapshot: ProvenanceSnapshot


class AttributeDeepDiveResponse(BaseModel):
    """Response for GET /api/cases/{id}/attributes/{attr_name}."""
    case_id: str
    attribute_name: str
    category: str
    pre_value: float
    post_value: float
    unit: str
    score: float
    uncertainty: float
    is_cqa: bool
    action: str
    reasoning: str  # Full markdown explanation
    spec_position: str  # Where does this sit vs. spec window?
    lot_variability: str  # Summary of pre/post CV
    orthogonal_support: List[str]  # Other methods that support this
    functional_support: str  # Direct / Indirect / None
    precedent_relevance: List[str]  # Prior approvals / benchmark cases
    action_with_reasoning: str  # Full markdown action card


class Gap(BaseModel):
    """Evidence gap inventory."""
    gap_id: str
    attribute: str
    gap_type: str
    severity: str
    why_important: str
    what_to_collect: str
    counterfactual_action_if_filled: str  # Action if gap is closed


class GapInventoryResponse(BaseModel):
    """Response for GET /api/cases/{id}/gaps"""
    case_id: str
    total_gaps: int
    critical_count: int
    high_count: int
    gaps: List[Gap]


class ProvenanceRecord(BaseModel):
    """Detailed provenance for one evidence piece."""
    record_id: str
    attribute: str
    source_type: str  # "prior_approval" / "benchmark" / "guideline" / "data"
    source_name: str
    relevance: float  # 0-1
    summary: str


class ProvenanceDetailResponse(BaseModel):
    """Response for GET /api/cases/{id}/provenance/{record_id}"""
    case_id: str
    record: ProvenanceRecord
    full_citation: str
    link_to_cmc_section: str
    inference_chain: str  # Markdown reasoning


class ExportRequest(BaseModel):
    """Request to export case."""
    format: str = "json"  # json / csv / json_with_reasoning


class ExportResponse(BaseModel):
    """Response for POST /api/cases/{id}/export"""
    case_id: str
    format: str
    filename: str
    size_bytes: int
    download_url: str  # URL to retrieve file (defer streaming to Phase B)


# =========================================================================
# Case Store Dependency
# =========================================================================

from api.models import (
    CaseStore, get_case_store, CaseMetadata, CaseStatus, CaseData,
)


def case_store_dep() -> CaseStore:
    """Dependency injection for case store."""
    return get_case_store()


# =========================================================================
# Endpoints
# =========================================================================

@app.get("/health")
def health():
    """Liveness / readiness check."""
    return {"status": "ok", "version": "1.0.0"}


@app.post("/api/v1/comparability")
def run_comparability(request: ComparabilityRequest):
    """Run the full comparability assessment pipeline.

    Accepts a list of quality attributes with pre/post-change values and
    returns a structured comparability report including verdict, per-attribute
    scores, CQA classification, uncertainty, evidence gaps, and actions.
    """
    try:
        from pipelines.comparability import run_comparability_assessment

        # Convert pydantic models to the dict format the pipeline expects
        pre_change_data = {
            "product_name": request.product_name,
            "molecule_class": request.molecule_class,
            "modality": request.modality,
            "reference_product": request.reference_product,
            "attributes": [
                attr.model_dump() for attr in request.attributes
            ]
        }

        report = run_comparability_assessment(
            pre_change_data=pre_change_data,
            product_name=request.product_name,
            change_description=request.change_description,
        )

        return report.to_dict()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Comparability pipeline error: {exc}",
        )


@app.post("/api/v1/gap-memo")
def run_gap_memo(request: GapMemoRequest):
    """Run the gap memo pipeline.

    Accepts CTD sections and returns a structured gap memo with findings,
    predicted reviewer questions, remediation suggestions, and readiness score.
    """
    try:
        from pipelines.gap_memo import generate_gap_memo

        # Convert pydantic models to the dict format the pipeline expects
        sections = [sec.model_dump() for sec in request.sections]

        memo = generate_gap_memo(
            sections=sections,
            product_type=request.product_type,
            submission_type=request.submission_type,
            product_name=request.product_name,
        )

        return memo.to_dict()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Gap memo pipeline error: {exc}",
        )


@app.post("/api/v1/grade-evidence")
def grade_evidence(request: GradeEvidenceRequest):
    """Grade evidence strength of a scientific claim.

    Returns a grade (strong / moderate / weak / anecdotal) with per-class
    probabilities.
    """
    try:
        from models.claim_evidence_grader import predict

        result = predict(request.claim)
        return {
            "claim": request.claim,
            "grade": result["prediction"],
            "probabilities": result["probabilities"],
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Evidence grading error: {exc}",
        )


@app.get("/api/v1/benchmarks")
def list_benchmarks():
    """List available benchmark cases.

    Returns metadata for each benchmark case file found in the
    benchmarks/cases/ directory.
    """
    cases_dir = os.path.join(PROJECT_ROOT, "benchmarks", "cases")
    if not os.path.isdir(cases_dir):
        return {"benchmarks": [], "count": 0}

    benchmarks = []
    for path in sorted(glob.glob(os.path.join(cases_dir, "*.json"))):
        try:
            with open(path) as f:
                data = json.load(f)
            benchmarks.append({
                "case_id": data.get("case_id", os.path.basename(path).replace(".json", "")),
                "title": data.get("title", ""),
                "category": data.get("category", ""),
                "file": os.path.basename(path),
            })
        except (json.JSONDecodeError, OSError):
            continue

    return {"benchmarks": benchmarks, "count": len(benchmarks)}


# =========================================================================
# Case Management Endpoints (Phase A)
# =========================================================================

@app.post("/api/cases", response_model=Dict[str, Any])
def create_case(
    request: CreateCaseRequest,
    store: Annotated[CaseStore, Depends(case_store_dep)]
) -> Dict[str, Any]:
    """POST /api/cases -- Create new assessment case.

    Accepts product metadata and batch data (CSV or JSON), runs Data Harmonizer
    and Input Validator, stores case in DB, returns case_id and validation status.
    """
    try:
        # Step 1: Input validation
        from modules.input_validator import validate_comparability_input

        validation = validate_comparability_input(request.batch_data)

        # Step 2: Data harmonization
        from modules.data_harmonizer import harmonize_batch_data

        harmonized_data = harmonize_batch_data(request.batch_data)

        # Step 3: Create case metadata
        case_id = str(uuid.uuid4())[:12]
        metadata = CaseMetadata(
            case_id=case_id,
            product_name=request.product_name,
            product_type=request.product_type,
            molecule_class=request.molecule_class,
            change_type=request.change_type,
            product_stage=request.product_stage,
            status=CaseStatus.DATA_LOADED,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            batch_count=len(request.batch_data.get("attributes", [])),
        )

        # Step 4: Store case
        store.create(metadata, harmonized_data)

        # Step 5: Return response
        return {
            "case_id": case_id,
            "status": CaseStatus.DATA_LOADED.value,
            "validation_status": "valid" if validation.valid else "invalid",
            "validation_errors": validation.errors if not validation.valid else [],
            "validation_warnings": validation.warnings,
            "batch_count": metadata.batch_count,
            "created_at": metadata.created_at,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Case creation error: {str(exc)}"
        )


@app.get("/api/cases", response_model=CaseListResponse)
def list_cases(
    store: Annotated[CaseStore, Depends(case_store_dep)]
) -> CaseListResponse:
    """GET /api/cases -- List all cases with status summary."""
    try:
        cases = store.list_all()
        summaries = []

        for metadata in cases:
            case = store.get(metadata.case_id)

            # Infer overall_action from comparability report (if available)
            overall_action = "Assess"
            critical_gaps_count = 0

            if case and case.comparability_report:
                report = case.comparability_report
                verdict = report.get("overall_verdict", "")
                if verdict == "Comparable":
                    overall_action = "Proceed to Submission"
                elif verdict == "Not Comparable":
                    overall_action = "Halt Review"
                else:
                    overall_action = "Collect Evidence"

            if case and case.gap_memo_result:
                critical_gaps_count = len([
                    g for g in case.gap_memo_result.get("gaps", [])
                    if g.get("severity") == "critical"
                ])

            summaries.append(CaseSummary(
                case_id=metadata.case_id,
                product_name=metadata.product_name,
                molecule_class=metadata.molecule_class,
                change_type=metadata.change_type,
                status=metadata.status.value,
                overall_action=overall_action,
                critical_gaps_count=critical_gaps_count,
                last_updated=metadata.updated_at,
            ))

        return CaseListResponse(
            cases=summaries,
            total_count=len(summaries),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Case list error: {str(exc)}"
        )


@app.get("/api/cases/{case_id}/overview", response_model=PackageOverviewResponse)
def get_package_overview(
    case_id: str,
    store: Annotated[CaseStore, Depends(case_store_dep)]
) -> PackageOverviewResponse:
    """GET /api/cases/{id}/overview -- Package Overview (THE CORE ENDPOINT).

    Runs full comparability pipeline (if needed) and aggregates results into 5 blocks:
    1. Judgment Summary (verdict, confidence, action, key finding)
    2. Top Gaps (3-5 critical gaps)
    3. Critical Attributes (scored attributes with action)
    4. Reviewer Risk (predicted questions)
    5. Provenance Snapshot (source counts)
    """
    try:
        case = store.get(case_id)
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        # Step 1: If comparability report doesn't exist, run pipeline
        if not case.comparability_report:
            from pipelines.comparability import run_comparability_assessment

            report = run_comparability_assessment(
                pre_change_data=case.raw_batch_data,
                product_name=case.metadata.product_name,
                change_description="",
            )
            store.update_comparability_report(
                case_id,
                report.to_dict() if hasattr(report, 'to_dict') else report,
            )
            case = store.get(case_id)  # Refresh

        report = case.comparability_report

        # ---- Block 1: Judgment Summary ----
        verdict = report.get("overall_verdict", "Insufficient Evidence")
        # Use evidence_strength_index (the real field name from schemas.py)
        evidence_strength = report.get("evidence_strength_index", 0.0)

        verdict_to_action = {
            "Comparable": "Proceed to Submission",
            "Comparable With Caveats": "Collect Additional Data",
            "Not Comparable": "Halt Review / Redesign Change",
            "Insufficient Evidence": "Request More Data",
        }
        overall_action = verdict_to_action.get(verdict, "Assess")

        n_comparable = report.get("n_comparable", 0)
        n_cqa = report.get("n_cqa", 0)
        n_total = report.get("n_attributes", 1)
        key_finding = f"{n_comparable}/{n_total} attributes comparable, {n_cqa} CQAs reviewed"

        judgment_summary = JudgmentSummary(
            verdict=verdict,
            confidence=min(1.0, evidence_strength),
            overall_action=overall_action,
            key_finding=key_finding,
        )

        # ---- Block 2: Top Gaps ----
        gaps = report.get("evidence_gaps", [])
        top_gaps = []
        for gap in gaps[:5]:
            if isinstance(gap, dict):
                top_gaps.append(TopGap(
                    attribute=gap.get("attribute", "Unknown"),
                    gap_type=gap.get("gap_type", "missing_data"),
                    severity=gap.get("severity", "medium"),
                    suggested_action=gap.get("action", "Collect data"),
                ))
            else:
                # evidence_gaps may be plain strings
                top_gaps.append(TopGap(
                    attribute="Unknown",
                    gap_type="missing_data",
                    severity="medium",
                    suggested_action=str(gap),
                ))

        # ---- Block 3: Critical Attributes ----
        # Real AttributeResult fields: name, category, score (0-1), uncertainty (0-1),
        # concern, action (dict with keys), is_cqa, delta_pct
        critical_attributes = []
        for attr_result in report.get("attribute_results", []):
            if isinstance(attr_result, dict):
                # Use REAL field names from pipelines/schemas.py
                score = attr_result.get("score", -1)
                uncertainty = attr_result.get("uncertainty", 0)
                concern = attr_result.get("concern", "none")

                # Derive action string from the action dict or concern level
                action_dict = attr_result.get("action")
                if isinstance(action_dict, dict):
                    action_str = action_dict.get("recommendation", "Assess")
                elif isinstance(action_dict, str):
                    action_str = action_dict
                else:
                    # Fallback: derive from score (0-1 scale)
                    if score >= 0.8:
                        action_str = "Acceptable"
                    elif score >= 0.6:
                        action_str = "Monitor"
                    elif score >= 0.4:
                        action_str = "Investigate"
                    else:
                        action_str = "Not Comparable"

                critical_attributes.append(CriticalAttribute(
                    name=attr_result.get("name", "Unknown"),
                    category=attr_result.get("category", "physicochemical"),
                    score=score,
                    uncertainty=uncertainty,
                    action=action_str,
                    is_cqa=attr_result.get("is_cqa", False),
                ))

        # ---- Block 4: Reviewer Risk ----
        predicted_questions = []
        if case.gap_memo_result:
            for q in case.gap_memo_result.get("predicted_questions", [])[:3]:
                if isinstance(q, dict):
                    predicted_questions.append(PredictedQuestion(
                        question=q.get("question", ""),
                        probability=q.get("probability", 0.5),
                        impact=q.get("impact", "clarification"),
                    ))

        reviewer_risk = ReviewerRisk(
            predicted_questions=predicted_questions,
        )

        # ---- Block 5: Provenance Snapshot ----
        provenance_chain = report.get("provenance_chain", [])
        provenance_snapshot = ProvenanceSnapshot(
            sources_count=len(provenance_chain) if provenance_chain else (
                len(case.raw_batch_data.get("attributes", [])) if case.raw_batch_data else 0
            ),
            precedents_cited=0,  # To be populated from evidence registry
            guidelines_referenced=0,
        )

        return PackageOverviewResponse(
            case_id=case_id,
            judgment_summary=judgment_summary,
            top_gaps=top_gaps,
            critical_attributes=critical_attributes,
            reviewer_risk=reviewer_risk,
            provenance_snapshot=provenance_snapshot,
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Package overview error: {str(exc)}"
        )


@app.get("/api/cases/{case_id}/attributes/{attr_name}", response_model=AttributeDeepDiveResponse)
def get_attribute_deep_dive(
    case_id: str,
    attr_name: str,
    store: Annotated[CaseStore, Depends(case_store_dep)]
) -> AttributeDeepDiveResponse:
    """GET /api/cases/{id}/attributes/{attr_name} -- Attribute Deep Dive.

    Full attribute reasoning card with pre/post comparison, spec position,
    lot variability, orthogonal support, functional support, precedent
    relevance, and action with reasoning.
    """
    try:
        case = store.get(case_id)
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        if not case.comparability_report:
            raise HTTPException(status_code=404, detail="No assessment run yet")

        report = case.comparability_report

        # Find attribute in results (real field: "name")
        attr = None
        for result in report.get("attribute_results", []):
            if isinstance(result, dict) and result.get("name") == attr_name:
                attr = result
                break

        if not attr:
            raise HTTPException(status_code=404, detail=f"Attribute {attr_name} not found")

        # Use REAL field names from pipelines/schemas.py AttributeResult
        score = attr.get("score", -1)
        uncertainty = attr.get("uncertainty", 0)
        concern = attr.get("concern", "none")
        delta_pct = attr.get("delta_pct", 0)
        detail_text = attr.get("detail", "")

        # Derive action string from action dict
        action_dict = attr.get("action")
        if isinstance(action_dict, dict):
            action_str = action_dict.get("recommendation", "Assess")
            action_reasoning = action_dict.get("reasoning", "")
        elif isinstance(action_dict, str):
            action_str = action_dict
            action_reasoning = ""
        else:
            action_str = "Assess"
            action_reasoning = ""

        # Build reasoning markdown
        reasoning = f"""
# {attr_name} Assessment

## Values
- Pre-change: {attr.get('pre_value')} {attr.get('unit', '')}
- Post-change: {attr.get('post_value')} {attr.get('unit', '')}
- Delta: {delta_pct}%

## Comparability Score: {score}
- Uncertainty: {uncertainty}
- Concern level: {concern}

## Detail
{detail_text}

## Action: {action_str}
{action_reasoning}
"""

        return AttributeDeepDiveResponse(
            case_id=case_id,
            attribute_name=attr_name,
            category=attr.get("category", "physicochemical"),
            pre_value=attr.get("pre_value", 0),
            post_value=attr.get("post_value", 0),
            unit=attr.get("unit", ""),
            score=score,
            uncertainty=uncertainty,
            is_cqa=attr.get("is_cqa", False),
            action=action_str,
            reasoning=reasoning,
            spec_position=f"CQA: {attr.get('cqa_designation', 'N/A')}, Concern: {concern}",
            lot_variability=f"CV: {attr.get('cv_pct', 'N/A')}%",
            orthogonal_support=[],  # Placeholder
            functional_support=attr.get("functional_support_level", "none"),
            precedent_relevance=[],  # Placeholder
            action_with_reasoning=reasoning,
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Attribute deep dive error: {str(exc)}"
        )


@app.get("/api/cases/{case_id}/gaps", response_model=GapInventoryResponse)
def get_gaps(
    case_id: str,
    store: Annotated[CaseStore, Depends(case_store_dep)]
) -> GapInventoryResponse:
    """GET /api/cases/{id}/gaps -- Gap inventory sorted by severity."""
    try:
        case = store.get(case_id)
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        if not case.comparability_report:
            raise HTTPException(status_code=404, detail="No assessment run yet")

        report = case.comparability_report
        gaps_raw = report.get("evidence_gaps", [])

        gaps = []
        critical_count = 0
        high_count = 0

        for idx, gap_raw in enumerate(gaps_raw):
            if not isinstance(gap_raw, dict):
                gap_raw = {"gap": str(gap_raw)}

            severity = gap_raw.get("severity", "medium")
            if severity == "critical":
                critical_count += 1
            elif severity == "high":
                high_count += 1

            gaps.append(Gap(
                gap_id=f"gap_{idx}",
                attribute=gap_raw.get("attribute", "Unknown"),
                gap_type=gap_raw.get("gap_type", "missing_data"),
                severity=severity,
                why_important=gap_raw.get("importance", "To validate comparability"),
                what_to_collect=gap_raw.get("remediation", "Collect additional data"),
                counterfactual_action_if_filled=gap_raw.get("action_if_filled", "Re-assess"),
            ))

        # Sort by severity: critical > high > medium > low
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        gaps.sort(key=lambda g: severity_order.get(g.severity, 4))

        return GapInventoryResponse(
            case_id=case_id,
            total_gaps=len(gaps),
            critical_count=critical_count,
            high_count=high_count,
            gaps=gaps,
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Gap inventory error: {str(exc)}"
        )


@app.get("/api/cases/{case_id}/provenance/{record_id}")
def get_provenance_detail(
    case_id: str,
    record_id: str,
    store: Annotated[CaseStore, Depends(case_store_dep)]
) -> Dict[str, Any]:
    """GET /api/cases/{id}/provenance/{record_id} -- Provenance drill-down.

    Deferred to Phase B (links to evidence registry). Returns placeholder data.
    """
    case = store.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    return {
        "case_id": case_id,
        "record_id": record_id,
        "source_type": "prior_approval",
        "source_name": "BLA-12345 (Similar mAb)",
        "relevance": 0.85,
        "summary": "Prior mAb comparability study in same process",
        "full_citation": "FDA BLA-12345, 2020",
        "link_to_cmc_section": "3.2.S.1",
        "inference_chain": "This prior approval supports the proposed approach.",
    }


@app.post("/api/cases/{case_id}/export", response_model=ExportResponse)
def export_case(
    case_id: str,
    request: ExportRequest,
    store: Annotated[CaseStore, Depends(case_store_dep)]
) -> ExportResponse:
    """POST /api/cases/{id}/export -- Export case.

    Formats: json / csv / json_with_reasoning.
    Deferred to Phase B (actual file download / streaming).
    """
    case = store.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    filename = f"{case_id}_export.{request.format}"

    return ExportResponse(
        case_id=case_id,
        format=request.format,
        filename=filename,
        size_bytes=1024,  # Placeholder
        download_url=f"/api/cases/{case_id}/download/{filename}",
    )


# =========================================================================
# NEW: REG-INTEL-BIOPHARMA Integration Endpoints (Part 3)
# =========================================================================

class WarningLetterClassifyRequest(BaseModel):
    """Input for FDA warning letter classification."""
    text: str = Field(..., description="Warning letter text or summary")
    product_type: str = Field("mAb", description="e.g., mAb, GLP-1, fusion_protein")
    site_type: str = Field("manufacturing", description="e.g., manufacturing, clinical, cmo")


class WarningLetterClassifyResponse(BaseModel):
    """Output from warning letter classifier."""
    primary_category: str
    secondary_categories: List[str]
    severity_score: float  # 0-1
    risk_level: str  # low/medium/high/critical
    key_findings: List[str]
    regulatory_concern_areas: List[str]
    confidence: float


@app.post("/api/v1/warning-letter/classify", response_model=WarningLetterClassifyResponse,
          tags=["experimental"], deprecated=True)
def classify_warning_letter(request: WarningLetterClassifyRequest) -> WarningLetterClassifyResponse:
    """
    [EXPERIMENTAL] Classify FDA warning letter text by regulatory concern area.

    WARNING: This endpoint uses a mock implementation, not the real classifier.
    The real classifier is in modules/regulatory/fda_classifier.py but is not yet
    wired into this API. Do not use for production decisions.
    """
    try:
        # Mock implementation: production version calls reg_intel_biopharma.fda_warning_letters.classify()
        # For now, provide realistic response structure

        text_lower = request.text.lower()

        # Simple heuristic classification based on keywords
        severity_keywords = {
            'critical': ['serious adverse event', 'patient death', 'product recall', 'contamination', 'sterility failure'],
            'high': ['gmp violation', 'data integrity', 'process failure', 'out of specification', 'batch rejection'],
            'medium': ['deviation', 'procedural gap', 'documentation incomplete', 'training deficiency'],
            'low': ['labeling correction', 'minor deviation', 'administrative']
        }

        severity = 'medium'
        for level, keywords in severity_keywords.items():
            if any(kw in text_lower for kw in keywords):
                severity = level
                break

        severity_map = {'low': 0.25, 'medium': 0.5, 'high': 0.75, 'critical': 1.0}

        return WarningLetterClassifyResponse(
            primary_category="GMP_Manufacturing",
            secondary_categories=["Process_Control", "Documentation"],
            severity_score=severity_map.get(severity, 0.5),
            risk_level="high" if severity in ['high', 'critical'] else severity,
            key_findings=[
                "Failure to establish adequate in-process controls",
                "Inadequate deviation investigation",
                "Insufficient batch documentation"
            ],
            regulatory_concern_areas=["manufacturing", "quality_assurance", "data_integrity"],
            confidence=0.78
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Warning letter classification failed: {str(e)}")


class ReviewerPredictRequest(BaseModel):
    """Input for reviewer question prediction."""
    cmc_summary: str = Field(..., description="CMC section summary")
    product_type: str = Field("mAb", description="e.g., mAb, biosimilar, GLP-1")
    change_type: str = Field("formulation", description="e.g., manufacturing, formulation, packaging")
    confidence_in_proposal: float = Field(0.7, description="Applicant confidence 0-1")


class ReviewerPredictResponse(BaseModel):
    """Output from reviewer predictor."""
    likely_question_categories: List[str]
    top_reviewer_questions: List[str]
    estimated_question_probability: float  # 0-1
    recommendation: str
    confidence: float


@app.post("/api/v1/reviewer/predict", response_model=ReviewerPredictResponse,
          tags=["experimental"], deprecated=True)
def predict_reviewer_questions(request: ReviewerPredictRequest) -> ReviewerPredictResponse:
    """
    [EXPERIMENTAL] Predict likely reviewer questions based on CMC package content.

    WARNING: This endpoint uses a mock implementation, not the real predictor.
    The real predictor is in modules/regulatory/reviewer_predictor.py but is not
    yet wired into this API.
    """
    try:
        # Mock implementation: production calls reg_intel_biopharma.reviewer_predictor.predict()

        text_lower = request.cmc_summary.lower()

        # Heuristic: predict questions based on proposal confidence and change magnitude
        base_prob = max(0.1, 1.0 - request.confidence_in_proposal)

        # Identify key risk areas
        risk_areas = []
        if 'process change' in text_lower or 'scale-up' in text_lower:
            risk_areas.append("Process Equivalence")
        if 'aggregation' in text_lower or 'stability' in text_lower:
            risk_areas.append("Product Stability")
        if 'impurity' in text_lower or 'related substance' in text_lower:
            risk_areas.append("Impurity Control")
        if 'analytical' in text_lower or 'method' in text_lower:
            risk_areas.append("Analytical Validation")

        if not risk_areas:
            risk_areas = ["CMC Completeness", "Quality Specifications"]

        return ReviewerPredictResponse(
            likely_question_categories=risk_areas,
            top_reviewer_questions=[
                "Have you demonstrated equivalence of the analytical method across both processes?",
                "Please provide side-by-side comparison of product-related impurity profiles.",
                "What is the justification for the proposed specification limit?",
                "Can you provide additional stability data under accelerated conditions?"
            ],
            estimated_question_probability=min(0.95, base_prob + 0.3),
            recommendation="Proactively address impurity control and analytical equivalence in CMC narrative.",
            confidence=0.72
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reviewer prediction failed: {str(e)}")


class StabilityPredictRequest(BaseModel):
    """Input for stability trend analysis."""
    timepoints: List[float] = Field(..., description="Time points (months)")
    assay_values: List[float] = Field(..., description="Potency/assay values")
    test_condition: str = Field("25C/60RH", description="e.g., 25C/60RH, 40C/75RH")
    specification_limit: float = Field(95.0, description="Specification lower limit")
    shelf_life_months: int = Field(24, description="Proposed shelf life")


class StabilityPredictResponse(BaseModel):
    """Output from stability prediction."""
    trend_analysis: str  # stable/declining/concerning
    arrhenius_slope: Optional[float]
    acceleration_factor: Optional[float]
    predicted_value_at_expiry: Optional[float]
    oos_risk: str  # low/medium/high
    oos_probability: float  # 0-1
    recommendation: str
    confidence: float


@app.post("/api/v1/stability/predict", response_model=StabilityPredictResponse,
          tags=["experimental"], deprecated=True)
def predict_stability_trend(request: StabilityPredictRequest) -> StabilityPredictResponse:
    """
    [EXPERIMENTAL] Analyze stability trends and predict OOS probability.

    WARNING: This endpoint uses a mock implementation with inline statistics,
    not the real Arrhenius model from ProteLoop's stability_trend evaluator.
    """
    try:
        # Mock implementation: production calls reg_intel_biopharma.stability_trendbot.analyze()

        import statistics

        if len(request.timepoints) < 2:
            raise HTTPException(status_code=400, detail="At least 2 timepoints required")

        # Simple linear regression on timepoints vs assay values
        n = len(request.timepoints)
        x_mean = statistics.mean(request.timepoints)
        y_mean = statistics.mean(request.assay_values)

        numerator = sum((request.timepoints[i] - x_mean) * (request.assay_values[i] - y_mean) for i in range(n))
        denominator = sum((request.timepoints[i] - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            slope = 0.0
        else:
            slope = numerator / denominator

        intercept = y_mean - slope * x_mean

        # Predict value at proposed shelf life
        predicted_value = intercept + slope * request.shelf_life_months

        # Assess trend
        if slope > -0.1:
            trend = "stable"
            risk = "low"
        elif slope > -0.5:
            trend = "declining"
            risk = "medium"
        else:
            trend = "concerning"
            risk = "high"

        # OOS probability: how likely to fall below spec
        oos_prob = 0.05 if risk == 'low' else 0.25 if risk == 'medium' else 0.65

        return StabilityPredictResponse(
            trend_analysis=trend,
            arrhenius_slope=slope,
            acceleration_factor=1.5 if "40C" in request.test_condition else 1.0,
            predicted_value_at_expiry=max(request.specification_limit, predicted_value),
            oos_risk=risk,
            oos_probability=oos_prob,
            recommendation="Extend real-time stability testing if declining trend observed." if trend != 'stable' else "Shelf life justified by current data.",
            confidence=0.81
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stability prediction failed: {str(e)}")


# =========================================================================
# END REG-INTEL-BIOPHARMA Integration Endpoints
# =========================================================================
