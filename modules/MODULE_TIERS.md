# Module Tiers

> Last updated: 2026-04-10

## Core (used in primary decision pipelines)

These modules are exercised by the comparability, gap memo, and/or submission readiness pipelines.
Changes here require full test + benchmark validation.

| Module | Pipeline | LoC | Role |
|--------|----------|-----|------|
| action_recommender | comparability | 583 | 5-level action taxonomy (PROCEED → DEFER) |
| biosimilar_uncertainty | comparability | 261 | Per-attribute residual uncertainty scoring |
| comparability_graph | comparability | 451 | NetworkX-based attribute scoring with tolerances |
| cqa_selector | comparability | 274 | ICH Q8/Q9 CQA classification (RPN scoring) |
| ctd_reviewer | gap_memo, readiness | 1435 | CTD Module 3 section analysis |
| data_harmonizer | comparability | 833 | Unit normalization, field mapping |
| evidence_closure | comparability | 627 | Evidence gap identification |

## Supporting (used in benchmarks or tests, not in primary pipeline flow)

These modules have real logic and test coverage but are not in the primary decision path.

| Module | LoC | Status |
|--------|-----|--------|
| ptm_attribution | 417 | Rules-based PTM impact analysis. Tested. Not wired into pipelines. |
| nam_readiness | 220 | NAM qualification scoring. Used by READY-* benchmarks (7 cases). |
| regulatory | 1423 | FDA classifier, reviewer predictor, EMA parser. Absorbed from reg-intel. Not yet wired into pipelines. |

## Experimental (stub, demo-only, or aspirational)

These modules exist but are not part of the honest MVP scope.
They should NOT be counted in portfolio module totals.

| Module | LoC | Why experimental |
|--------|-----|-----------------|
| admissibility_engine | 485 | Overlaps with action_recommender; no pipeline integration |
| lifecycle_memory | 345 | Cross-repo evidence linking — no actual cross-repo connections exist |
| medical_writing | 233 | Trace.py stub only |
| translational_evidence | 387 | Hardcoded TARGET_DISEASE_DB demo data |
