# B-4: Label Adjudication Policy

> How does the system decide which labels to trust, and how much?

Every `LabelRecord` has an empty `ground_truth` slot at birth. This policy defines
**four tiers** of adjudication -- rules that fill that slot with increasing levels of
authority. Downstream consumers (model retraining, drift detection, audit export)
use `annotation_source` to weight labels appropriately.

---

## Tier 1: Deterministic Auto-Label

**When the answer is computable from rules alone.**

| Module | Rule | Threshold |
|--------|------|-----------|
| `cqa_selector` | CQA classification by RPN score | RPN >= predefined cutoff in module config |
| `comparability_graph` | Comparability verdict when **all** attribute scores > 0.85 | All attributes > 0.85 => COMPARABLE |
| `ctd_section_classifier` | Section classification by keyword match | Confidence > 0.95 |

**Adjudication rule:**
- `ground_truth` = copy of `prediction`
- `annotation_source` = `"deterministic"`
- `confidence_delta` = `0.0` (by definition -- prediction matches ground truth)
- `annotator` = `"auto_adjudicator_t1"`

**Rationale:** These cases have no ambiguity. A keyword match at 0.98 confidence or
an RPN score above threshold is not a judgment call -- it is arithmetic. Auto-labeling
these frees expert bandwidth for genuinely uncertain cases.

---

## Tier 2: LLM Silver-Label

**When a classifier produces a label with reasonable confidence, but it is not
deterministic.**

| Module | Classifier | Condition |
|--------|-----------|-----------|
| `fda_warning_letters` | TF-IDF warning letter classifier | Classifier confidence >= 0.70 |
| `claim_evidence_grader` | Evidence strength grading | Grader confidence >= 0.70 |
| `policy_signal_classifier` | Policy signal classification | Classifier confidence >= 0.70 |

**Adjudication rule:**
- `ground_truth` = classifier output dict
- `annotation_source` = `"llm_silver"`
- `confidence_delta` = computed distance between prediction and classifier output
- `annotator` = `"auto_adjudicator_t2"`

**Rationale:** Silver labels are useful for bootstrapping training sets. They should
be consumed with awareness of their noise level. Any model trained on silver labels
should track the fraction of silver vs. gold labels and report it.

---

## Tier 3: Reviewer-Confirmed

**When a domain expert accepts, rejects, or modifies a prediction via `FeedbackEvent`.**

| Reviewer Role | Reviews |
|--------------|---------|
| CMC Scientist | Comparability verdicts, CQA classifications |
| Regulatory Strategist | Gap memo findings, regulatory signal interpretations |
| Clinical Pharmacologist | Immunogenicity risk assessments, PK predictions |

**Adjudication rule:**
- `ground_truth` = filled from `FeedbackEvent`:
  - If `action="accept"`: ground_truth = prediction (expert agrees)
  - If `action="modify"`: ground_truth = `modified_value`
  - If `action="reject"`: ground_truth = `{"rejected": true, "reason": event.reason}`
- `annotation_source` = `"expert"`
- `confidence_delta` = computed distance between prediction and ground_truth
- `annotator` = expert identifier from FeedbackEvent

**Rationale:** Expert labels are the primary training signal. The system is designed
to maximize the value of every expert interaction -- one accept/reject click produces
a training pair.

---

## Tier 4: Outcome-Backed Gold Label

**When real-world outcomes provide unambiguous ground truth.**

| Outcome Source | Applies To |
|---------------|-----------|
| Regulatory submission result (approved / CRL / deficiency letter) | Gap memos, regulatory signal predictions |
| Real stability data (accelerated or long-term) | Comparability predictions, degradation forecasts |
| Clinical immunogenicity incidence | ADA risk predictions, immunogenicity scoring |
| Manufacturing deviation records | CQA classifications, process comparability |

**Adjudication rule:**
- `ground_truth` = structured outcome data
- `annotation_source` = `"regulatory_outcome"` or `"experimental"`
- `confidence_delta` = computed distance between prediction and actual outcome
- `annotator` = `"outcome_tracker"` or specific data source identifier

**Rationale:** These are the labels we ultimately optimize for. A prediction that a
biosimilar would receive approval, confirmed by actual FDA approval, is the highest
quality training signal. These labels are rare and delayed but have zero noise.

---

## Tier Priority

When multiple tiers apply to the same record, the **highest tier wins**:

```
Tier 4 (gold) > Tier 3 (expert) > Tier 2 (silver) > Tier 1 (deterministic)
```

A record auto-labeled at Tier 1 can be upgraded to Tier 3 if an expert later
reviews it. The original Tier 1 label is preserved in `metadata.prior_labels`
for audit.

---

## Confidence Delta

`confidence_delta` quantifies how much the ground truth diverges from the prediction:

- `0.0` = perfect agreement (prediction matched ground truth exactly)
- `1.0` = maximum disagreement (prediction was completely wrong)

Computation varies by module output type:
- **Categorical** (e.g., verdict: comparable/not): 0.0 if match, 1.0 if mismatch
- **Numeric** (e.g., score 0-1): `abs(predicted - actual)`
- **Structured** (e.g., dict with multiple fields): average of per-field deltas

High `confidence_delta` records are the most valuable for retraining -- they represent
cases where the model was wrong and can learn the most.

---

## Implementation

- `schemas/adjudicator.py` -- Tier 1 and Tier 2 auto-adjudication functions
- `schemas/label_store.py` -- `auto_adjudicate()` and `get_adjudication_stats()` methods
- Tier 3 is driven by `FeedbackEvent` submission (already in label_schema.py)
- Tier 4 is driven by external outcome data ingestion (future pipeline integration)
