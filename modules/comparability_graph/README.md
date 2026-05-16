# Comparability Evidence Graph

## Purpose
Structured evidence system for lot-to-lot biologics comparison.
Takes raw analytical measurements, builds an evidence graph, and generates a verdict with confidence and provenance.

## Demo To Production Path

| Step | Type | Deliverable | Est. Effort |
|------|------|------------|-------------|
| 1 | Schema | JSON schema (DONE) | Done |
| 2 | Backend | Graph builder + simple scoring | 3-4h |
| 3 | Backend | Thermal stability exemplar case | 2h |
| 4 | Demo | Jupyter notebook walkthrough | 2h |
| 5 | Backend | Multi-attribute scoring | 4-6h |
| 6 | Frontend | Streamlit viewer (Phase 3) | 4h |
| 7 | QA | Benchmark + milestone-qa audit | 2h |

## CLI Usage (after build)
```bash
python -m modules.comparability_graph.engine --input cases/thermal_stability.json --output verdict.json
python -m modules.comparability_graph.report --input verdict.json --output report.md
```

## Schema
See `schemas/comparability_graph.schema.json` for the full evidence graph schema.

### Key Concepts
- **Lots**: Manufacturing lots being compared (pre-change vs post-change, or originator vs biosimilar)
- **Attributes**: Quality attributes measured (purity, potency, stability, etc.)
- **Measurements**: Raw analytical data per lot per attribute
- **Edges**: Relationships between attributes (correlates_with, impacts, caused_by, predicts, contradicts)
- **Verdict**: Overall comparability decision with confidence and residual uncertainties
