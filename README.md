# biologics-decision-engine

> Rule-based CMC decision support for biopharma analytical scientists: document classification, CQA extraction, evidence gap analysis, and submission readiness assessment — no cloud services or LLMs required.

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg) ![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-green.svg)

> **For decision support only. Not regulatory advice. Verify all findings with source documents and a qualified regulatory professional.**

## Key results

| Metric | Value |
|--------|-------|
| Test suite | 600+ tests, all passing |
| Benchmark accuracy | 20/20 cases (100%) on public regulatory PDFs |
| Document types | Characterization, Stability, Analytical Method, Comparability |
| Standards coverage | ICH Q6B, Q1A/Q5C, Q2(R2), Q5E |
| CQA extraction | Three-state logic (Present / Uncertain / Confirmed Absent) |
| Comparability actions | 5-level (PROCEED / MONITOR / SUPPLEMENT / INVESTIGATE / DEFER) |
| Real-document benchmarks | NISTmAb SP 260-237, Xbonzy EPAR, ICH Q14, Darzalex EPAR |

## What it does

- **Classifies documents** by type (Characterization, Stability, Analytical Method, Comparability) with confidence scoring
- **Extracts key CQAs** (HMW%, main charge peak%, afucosylation%, potency) from PDF/DOCX tables and text using three-state logic
- **Assesses section coverage** against ICH Q6B, Q1A/Q5C, and Q2(R2) guidelines
- **Identifies evidence gaps** and extraction uncertainties with severity ratings
- **Predicts reviewer questions** with CRITICAL / MAJOR / MINOR severity badges
- **Evaluates spec compliance** for extracted CQA values against defined limits
- **Runs lot-aware extraction** (prefers RM lots over PS for reference standard documents)
- **Generates exportable reports** (DOCX for comparability, text summary for other types, CSV attribute tables)
- **Full comparability pipeline** for pre/post analytical data with 5-level action recommendations

Core reasoning is rule-based and deterministic. Optional LLM-assisted extraction fallback available when `ANTHROPIC_API_KEY` is set (used only when rule-based extraction fails; clearly logged in audit trail).

For a deployment-focused view of system architecture, run path, validation evidence, failure modes, and demo script, see [docs/DEPLOYMENT_ARCHITECTURE.md](docs/DEPLOYMENT_ARCHITECTURE.md).

## What it does NOT do

- Does **not** integrate with live FDA/EMA databases
- Does **not** write regulatory submissions (analysis only, not drafting)
- Does **not** replace regulatory expert review
- Does **not** handle scanned PDFs requiring OCR
- Does **not** perform statistical modeling beyond simple linear extrapolation
- Does **not** store documents persistently — single-session analysis only

## Quick start

```bash
pip install -r requirements.txt
streamlit run ui/app.py
```

Then open http://localhost:8501. Upload a DOCX or PDF regulatory document, review the automated extraction, and view the decision panel with verdict cards, evidence gaps, and predicted reviewer questions.

## Supported document types

| Type | What it assesses | Key standards |
|------|-----------------|---------------|
| Characterization | ICH Q6B section coverage, CQA extraction, three-state evidence model | ICH Q6B |
| Stability | Shelf-life support, storage conditions, OOS/OOT events, trend concerns | ICH Q1A / Q5C |
| Analytical Method | ICH Q2(R2) validation study completeness, method parameters | ICH Q2(R2) / Q14 |
| Comparability | Pre/post attribute comparison, CQA scoring, evidence gap analysis | ICH Q5E |

## Reproduction

```bash
# Full test suite
python3 -m pytest tests/ -q

# Benchmarks (20 cases, 100% accuracy)
python3 benchmarks/run_benchmarks.py

# QA agent (5 capabilities, value correctness probe)
python3 qa/run_qa.py
```

Real-document benchmarks in `benchmarks/real_documents/` use 5 public regulatory PDFs (NISTmAb SP 260-237, Xbonzy EPAR, ICH Q14, NISTmAb RM 8671 Certificate, Darzalex EPAR).

## Citation

```bibtex
@software{wu2026biologicsdecisionengine,
  author = {Wu, Di},
  title  = {biologics-decision-engine: Rule-Based CMC Decision Support for Biopharma},
  year   = {2026},
  url    = {https://github.com/diwuhub/biologics-decision-engine}
}
```

## References

- ICH Q6B — Specifications: Test Procedures and Acceptance Criteria for Biotechnological/Biological Products
- ICH Q1A(R2) / Q5C — Stability Testing
- ICH Q2(R2) / Q14 — Analytical Procedure Development and Validation
- ICH Q5E — Comparability of Biotechnological/Biological Products

## License

MIT. See [LICENSE](LICENSE).
