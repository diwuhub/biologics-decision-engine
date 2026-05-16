# Cross-Document Intelligence Bridge -- Architecture Specification

**SP v5 Priority P4** | Interface specification only (no full implementation)

> Per SP v5 Section 7.1 and Guardrail #2: "Phase 1 = CSV. Phase 2 (raw
> documents) = separate product generation."

This directory defines the **interface contracts** for Phase 2 document
intelligence without building the full extraction pipeline.

---

## 4-Layer Architecture

The biologics-decision-engine is organized in four layers:

```
Layer 4: Connectors (specs/)
    Raw input -> structured attributes
    Phase 1: CSVBridge (csv_adapter.py)
    Phase 2: DocumentBridge (future)
         |
         v
Layer 1: Decision Workflow (pipelines/)
    comparability.py orchestrates the assessment
    Consumes {"attributes": [...]} dict
         |
         v
Layer 2: Evidence Services (services/, modules/)
    regulatory_evidence.py   -- precedent search, reviewer questions
    cqa_selector/            -- CQA classification
    comparability_graph/     -- attribute scoring
    biosimilar_uncertainty/  -- uncertainty quantification
    evidence_closure/        -- gap analysis
    action_recommender/      -- action recommendations
         |
         v
Layer 3: Evidence Registry (evidence_registry/)
    YAML-based typed store of regulatory guidelines,
    precedents, and method standards
```

### Data flow

```
CSV or Document(s)
      |
      v
  BridgeOrchestrator.ingest()  -- Layer 4
      |  returns {"attributes": [...]}
      v
  run_comparability_assessment()  -- Layer 1
      |  calls Layer 2 modules in sequence
      v
  ComparabilityReport  -- structured output
```

---

## What Phase 1 (CSV) covers

- Structured CSV input with pre-change and post-change values per attribute
- Column mapping and normalization via `CSVDocumentParser`
- Direct pass-through to `run_comparability_assessment` via `CSVBridge`
- All downstream modules (CQA selection, scoring, uncertainty, closure, actions)

**Files:**
- `specs/csv_adapter.py` -- `CSVBridge`, `CSVDocumentParser`, `CSVAttributeExtractor`

---

## What Phase 2 (documents) would add

Phase 2 introduces raw regulatory document parsing. The interfaces in
`cross_document_bridge.py` define the contracts:

| Interface                  | Purpose                                             |
|----------------------------|-----------------------------------------------------|
| `DocumentParser`           | Parse PDF/DOCX/scanned images into structured pages |
| `TableExtractor`           | Extract tables from CTD Module 3 documents          |
| `AttributeExtractor`       | Map table cells to typed `ExtractedAttribute` objects|
| `CrossDocumentReconciler`  | Detect contradictions across multiple documents     |
| `BridgeOrchestrator`       | Compose all above into a single `ingest()` call     |

### Example Phase 2 document types
- CTD Module 3.2.S (Drug Substance) and 3.2.P (Drug Product) sections
- ICH Q5E comparability protocols
- Certificates of Analysis (CoA)
- Stability reports (ICH Q5C)
- Batch manufacturing records

---

## How the bridge interfaces connect the layers

The key contract is `BridgeOrchestrator.ingest()`. It accepts file paths and
returns the exact dict schema that `run_comparability_assessment` expects:

```python
{
    "attributes": [
        {
            "name": "SEC Purity (Main Peak)",
            "category": "purity",
            "pre_value": 98.5,
            "post_value": 97.8,
            "unit": "%",
            "n_lots": 5,
            "cv_pct": 1.2,
            ...
        },
        ...
    ],
    "molecule_class": "mAb",
    "modality": "IV",
}
```

Phase 1 (`CSVBridge`) reads this directly from CSV columns.
Phase 2 would extract it from document tables via OCR/NLP, then reconcile
across sources before producing the same dict.

---

## What would need to be implemented for Phase 2

1. **PDF/DOCX parser** implementing `DocumentParser`
   - Libraries: PyMuPDF, pdfplumber, python-docx, or cloud OCR APIs
   - Must handle scanned documents (OCR fallback)

2. **Table extraction** implementing `TableExtractor`
   - CTD tables follow predictable structures (ICH M4Q format)
   - Need to handle merged cells, multi-page tables, footnotes

3. **Attribute extraction** implementing `AttributeExtractor`
   - Map column headers to attribute names using domain dictionary
   - Classify values into categories (physicochemical, purity, potency, etc.)
   - Assign confidence scores based on extraction quality

4. **Cross-document reconciliation** implementing `CrossDocumentReconciler`
   - Match attributes by name across documents
   - Detect value contradictions (e.g., CoA says 98.5% but report says 97.8%)
   - Apply resolution rules (prefer CoA over summary table, flag for review)

5. **Integration** via a concrete `BridgeOrchestrator` subclass
   - Wire up parser -> table extractor -> attribute extractor -> reconciler
   - Return pipeline-ready dict

6. **Validation and confidence thresholds**
   - Minimum extraction confidence to include an attribute
   - Human-in-the-loop review for low-confidence extractions
   - Audit trail linking every value to source page and table
