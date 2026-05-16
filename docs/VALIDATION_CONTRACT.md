# Validation Contract

**Step 0C: Freeze Validation Rules**

This document defines Must-Pass (MP), Partial-Match (PM), and Soft-Check (SC) criteria for each refactor step. Without these, verification only checks that code runs, not that judgment improved.

---

## Validation Tiers

- **Must-Pass (MP):** Exact match required. Any deviation is a blocking failure.
- **Partial-Match Allowed (PM):** Directionally correct required. Wording, exact confidence value, or secondary field may vary within defined tolerance.
- **Soft Check (SC):** Monitored but does not block step completion. For fields still under active development.

---

## Step 0A: Freeze Judgment Objects

| Tier | Criterion | Test Reference |
|---|---|---|
| MP | Four schema files exist and import without errors | `TestStep0A.test_mp_schema_files_exist_and_import` |
| MP | CaseContext is immutable after creation | `TestStep0A.test_mp_case_context_immutable` |
| MP | `build_risk_clusters()` produces clusters with non-empty `cluster_reason_summary` and `risk_semantics` for all 12 gold cases | `TestStep0A.test_mp_cluster_builder_all_gold_cases` |
| PM | Cluster types match expected types in Gold Cases 01-07, 11, 12 | `TestStep0A.test_pm_cluster_types_gold_cases` |
| PM | `risk_semantics` values match expected semantics for Gold Cases 02, 04, 05, 10, 11, 12 | `TestStep0A.test_pm_risk_semantics_gold_cases` |

---

## Step 0B / 0B.1: Gold Cases + Baseline

| Tier | Criterion | Test Reference |
|---|---|---|
| MP | All 12 gold case fixtures exist and parse without errors | `TestStep0B.test_mp_all_fixtures_exist_and_parse` |
| MP | Baseline outputs recorded for all 12 cases before any code changes | `TestBaselineExists.test_baseline_json_exists` |
| PM | All 12 gold cases have `expected_top_reviewer_concern` populated with non-generic text | `TestStep0B.test_mp_expected_concerns_populated` |

---

## Step 0D: Decision Rule Catalog

| Tier | Criterion | Test Reference |
|---|---|---|
| MP | Rule catalog contains at least 20 rules across all 7 categories | `TestStep0D.test_mp_rule_catalog_minimum_rules` |
| MP | Every rule has: Rule ID, scope, rule text, allowed inputs, forbidden effects, related gold cases | `TestStep0D.test_mp_rule_fields` |
| MP | Every `rule_id` referenced in `decision_rule_ids` exists in `rule_catalog.yaml` | `TestRuleCatalogCrossReference.test_all_decision_rule_ids_exist_in_catalog` |
| PM | Conservative policy rules reference specific gold cases from Step 0B | `TestStep0D.test_pm_rules_reference_gold_cases` |

---

## Step 1: Unify Decision-Facing Facts and Rules

| Tier | Criterion |
|---|---|
| MP | No Python dict precedent data in any import path of `comparability.py` |
| MP | Registry entry count >= 80 migrated precedents |
| MP | All 5 vocabulary types present in registry (precedent, taxonomy, concern vocabulary, authority tier semantics, follow-up hint semantics) |
| PM | Top 50 entries have `applicable_categories` populated |

---

## Step 2: Cluster-Aware Matcher

| Tier | Criterion |
|---|---|
| MP | `match_for_clusters()` returns one distinct `AuthorityContextPack` per cluster |
| MP | Every `top_decision_driver` has a non-empty `decision_relevance_note` |
| MP | Case-level pack is NOT the union of cluster packs |
| PM | Gold Cases 01 and 06 produce packs with correct sparsity/conflict flags |
| PM | Gold Case 05 produces `authority_conflict_flag = True` in potency cluster pack |

---

## Step 3: Two-Stage Conservative Policy

| Tier | Criterion |
|---|---|
| MP | Cluster-level policy does NOT modify `PackageDecision.confidence` |
| MP | Package-level policy does NOT modify `RiskCluster.concern_level` |
| MP | Gold Case 06 produces `abstain_flag = False` (Guardrail 2 / FALL-001) |
| MP | Gold Case 10 produces no blocking cluster (Guardrail 1 / GUARD-001) |
| PM | Gold Case 05 produces `confidence < 0.5` |
| SC | Running cluster-level then package-level on Gold Case 09 does not produce `confidence < 0.3` (anti-double-penalization) |

---

## Step 4: Reviewer Concern Engine

| Tier | Criterion |
|---|---|
| MP | `ReviewerConcernResult.cluster_priority_updates` is non-empty for cases with `risk_semantics != sufficient_evidence` |
| MP | Gold Cases 01/04 produce fewer and lower-severity concerns than Gold Cases 02/05 |
| MP | Gold Case 07 contributes to `abstain_flag = True` via `confidence_impact` |
| MP | Step 4 does NOT produce a new verdict category not already defined in `PackageVerdict` enum (Guardrail 4) |
| PM | Different cluster profiles produce meaningfully different concern text |

---

## Step 5: Authority Semantics Enrichment

| Tier | Criterion |
|---|---|
| MP | `top_decision_drivers` across all 12 gold cases change for >= 8/12 cases after enrichment |
| MP | All entries appearing as `top_decision_drivers` in any gold case have `authority_quality_tier` populated |
| PM | Gold Case 01 top drivers rated `primary` or `strong_secondary` |
| PM | Gold Case 06 top drivers include at least one `primary` tier (ICH) entry |
