# Gold Cases -- Behavioral Truth Set

**Step 0B: Freeze 12 Canonical Gold Cases**

These 12 cases are the behavioral anchors for the Judgment Core Refactor. Every refactor step must demonstrate it moves system behavior closer to these expected outputs. "Code is more complete" is not the bar. "Gold case behavior improves" is the bar.

Gold cases are synthetic or public-domain-based scenarios. Their expected outputs are fixed by human expert judgment. They are behavioral anchors, not test coverage metrics.

---

## GC-01: Normal Sufficient Comparability

**Scenario:** mAb scale-up process change. All CQA within +/-10% delta. Strong ICH Q5E + multiple FDA scale-up precedents. No gaps.

**Tests:** Clean proceed path, Guardrail 3 baseline.

| Field | Expected Value |
|---|---|
| verdict | `proceed` |
| confidence_band | `high` |
| blocking_cluster_count | 0 |
| abstain_flag | `False` |
| key_reviewer_concern | None or minimal ("routine monitoring" level) |
| risk_semantics | `sufficient_evidence` |

---

## GC-02: Orthogonal Method Gap

**Scenario:** mAb facility transfer. Potency: activity assay shows comparable, but no orthogonal method available. ICH Q5E requires orthogonal support for CQA claims.

**Tests:** Orthogonal gap detection, CQA blocking escalation.

| Field | Expected Value |
|---|---|
| verdict | `supplement_required` |
| confidence_band | `moderate` |
| blocking_cluster_count | 1 (potency CQA cluster) |
| abstain_flag | `False` |
| key_reviewer_concern | "Potency CQA lacks orthogonal method support per ICH Q5E" |
| risk_semantics | `orthogonal_gap` |

---

## GC-03: Trending Stability Within Spec

**Scenario:** Post-approval process change. Stability data shows a downward purity trend at 6-month accelerated, still within spec.

**Tests:** Trend detection, conditional proceed path.

| Field | Expected Value |
|---|---|
| verdict | `proceed_with_conditions` |
| confidence_band | `moderate` |
| blocking_cluster_count | 0 |
| abstain_flag | `False` |
| key_reviewer_concern | "Downward purity trend at accelerated stability requires extended monitoring" |
| risk_semantics | `trend_requires_monitoring` |

---

## GC-04: Better-Than-Reference Purity

**Scenario:** Process change reduces aggregates from 1.2% to 0.4%. All other attributes comparable.

**Tests:** Favorable shift handling, immunogenicity rationale requirement.

| Field | Expected Value |
|---|---|
| verdict | `proceed` |
| confidence_band | `high` |
| blocking_cluster_count | 0 |
| abstain_flag | `False` |
| key_reviewer_concern | "Favorable purity shift requires immunogenicity impact rationale" |
| risk_semantics | `favorable_shift_requires_rationale` |

---

## GC-05: Conflicting Methods

**Scenario:** Two potency assays contradict each other. Activity assay: comparable. Cell-based: 25% reduction. No guidance on which to rely on.

**Tests:** Contradiction detection, investigation path, authority conflict.

| Field | Expected Value |
|---|---|
| verdict | `investigation_required` |
| confidence_band | `low` |
| blocking_cluster_count | 1 (potency cluster with contradiction) |
| abstain_flag | `False` |
| key_reviewer_concern | "Potency assays show contradictory results; no guidance on resolution" |
| risk_semantics | `contradiction` |

---

## GC-06: No Precedent, Strong Guideline

**Scenario:** Novel bispecific format, DS formulation change. No direct precedent. Strong ICH Q5E normative basis.

**Tests:** Guardrail 2 -- no precedent must NOT trigger abstain.

| Field | Expected Value |
|---|---|
| verdict | `proceed_with_conditions` |
| confidence_band | `moderate` |
| blocking_cluster_count | 0 |
| abstain_flag | `False` |
| key_reviewer_concern | "No direct precedent for bispecific format; relying on normative basis only" |
| risk_semantics | `no_precedent_low_confidence` |

---

## GC-07: Should Abstain

**Scenario:** Multiple critical attributes with contradictory inter-method data, conflicting US/EU stances, AND no applicable normative guidance for this combination.

**Tests:** Abstain trigger (ABST-001).

| Field | Expected Value |
|---|---|
| verdict | `defer_package` |
| confidence_band | `low` |
| blocking_cluster_count | >= 2 |
| abstain_flag | `True` |
| key_reviewer_concern | "Insufficient authority basis for any defensible judgment; human review required" |
| risk_semantics | `contradiction` (multiple clusters) |

---

## GC-08: Geography Conflict

**Scenario:** mAb process change. Attributes comparable. FDA and EMA have divergent stability acceptance criteria for this molecule class.

**Tests:** Geography divergence handling, human review flag.

| Field | Expected Value |
|---|---|
| verdict | `proceed_with_conditions` |
| confidence_band | `moderate` |
| blocking_cluster_count | 0 |
| abstain_flag | `False` |
| key_reviewer_concern | "Divergent FDA/EMA stability acceptance criteria require geography-specific filing strategy" |
| risk_semantics | `cross_geography_divergence` |

---

## GC-09: Dated Support Dominates

**Scenario:** Established mAb facility transfer. All registry precedents > 10 years old, pre-QbD era. ICH guideline revised since.

**Tests:** Temporal sparsity handling, confidence cap.

| Field | Expected Value |
|---|---|
| verdict | `supplement_required` |
| confidence_band | `moderate` (capped at 0.65) |
| blocking_cluster_count | 0 |
| abstain_flag | `False` |
| key_reviewer_concern | "All supporting precedents are pre-QbD era; current guideline expectations may differ" |
| risk_semantics | `no_precedent_low_confidence` |

---

## GC-10: Concern Pattern Only / Weak Normative

**Scenario:** Process change with only concern_pattern_refs, no normative or precedent refs.

**Tests:** Guardrail 1 -- pattern cannot drive blocking verdict. NOT blocking.

| Field | Expected Value |
|---|---|
| verdict | `proceed_with_conditions` |
| confidence_band | `moderate` |
| blocking_cluster_count | 0 |
| abstain_flag | `False` |
| key_reviewer_concern | "Only concern pattern references available; no normative or precedent support" |
| risk_semantics | `pattern_concern_only` |

---

## GC-11: Hidden Package Insufficiency

**Scenario:** All attributes individually score as "comparable". No single cluster has concern_level >= major. But potency cluster is missing one REQUIRED method type per ICH Q5E, creating a package-level gap that no single attribute surfaces.

**Tests:** Package aggregation must exceed attribute averaging (AGGR rules).

| Field | Expected Value |
|---|---|
| verdict | `supplement_required` |
| confidence_band | `moderate` |
| blocking_cluster_count | 1 (potency cluster with hidden gap) |
| abstain_flag | `False` |
| key_reviewer_concern | "Package-level gap: required potency method type missing per ICH Q5E despite individual comparability" |
| risk_semantics | `assay_gap` |

---

## GC-12: Mixed Weak Evidence, Non-Blocking

**Scenario:** Weak but not absent evidence: some normative refs, one old precedent (historical), no concern pattern alignment. Not pattern-only (see Case 10), but not fully supported.

**Tests:** Conservative policy boundary -- must NOT overreact to mixed weakness (FALL-002).

| Field | Expected Value |
|---|---|
| verdict | `proceed_with_conditions` |
| confidence_band | `moderate` |
| blocking_cluster_count | 0 |
| abstain_flag | `False` |
| key_reviewer_concern | "Mixed evidence strength; some normative support but limited precedent" |
| risk_semantics | `sufficient_evidence` |
