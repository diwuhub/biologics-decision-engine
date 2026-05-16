# Judgment Core Contract

**Step 0A — Freeze Judgment Objects + Contracts + Lifecycle**

This document defines the four core objects, three judgment contracts, five guardrails, the object lifecycle, and what NOT to do. All subsequent refactor steps (0B through 5) must comply with these definitions.

---

## 1. Core Objects

### 1.1 CaseContext (`schemas/case_context.py`)

**Purpose:** Immutable case-level context created at pipeline entry. All downstream modules share this object; none may reconstruct their own context from raw inputs.

**MVP-Required Fields:**

| Field | Type | Semantics |
|---|---|---|
| case_id | str | Unique run identifier (auto-generated) |
| molecule_class | str | e.g., 'mAb', 'ADC', 'bispecific', 'fusion_protein' |
| change_type | str | e.g., 'process_change', 'facility_transfer', 'scale_up' |
| change_description | str | Free-text description of the change |
| lifecycle_stage | str | e.g., 'CMC', 'Phase_III', 'post_approval' |
| target_geography | str | 'US', 'EU', 'JP', 'global' (default: 'global') |
| flagged_attribute_ids | List[str] | Attributes with concern > none after initial scoring |
| flagged_categories | List[str] | Distinct categories containing flagged attributes |
| identified_gaps | List[str] | Evidence gaps identified at input stage |

**Future-Reserved Fields** (optional, NOT required parameters or pipeline contract dependencies):

| Field | Type | Semantics |
|---|---|---|
| molecule_name | Optional[str] | Product name (display/logging only) |
| modality | Optional[str] | e.g., 'injectable' (future geography-specific logic) |
| intended_regulatory_outcome | Optional[str] | Desired conclusion (future counterfactual use) |
| normalized_attribute_ids | Optional[List[str]] | All attribute IDs (future completeness scoring) |
| input_completeness_ratio | Optional[float] | Computed coverage (future abstain trigger) |
| current_action_ceiling | Optional[str] | Max permissible action level (future case-type constraint) |

**Immutability:** Enforced via `__setattr__` and `__delattr__` overrides. Any attempt to modify CaseContext after construction raises `AttributeError`.

### 1.2 RiskCluster (`schemas/risk_cluster.py`)

**Purpose:** The primary judgment atom. Attributes are evidence atoms; clusters are judgment atoms. Every verdict, action recommendation, and reviewer concern traces to a cluster, never directly to an attribute.

**Identity Fields** (frozen at construction):

| Field | Type | Semantics |
|---|---|---|
| cluster_id | str | Unique within this case run |
| cluster_type | str | 'category_risk' / 'cqa_concern' / 'cross_category_gap' / 'single_attribute_critical' |
| dominant_category | str | Primary analytical category |
| affected_attribute_ids | List[str] | Attribute IDs grouped into this cluster |
| contains_cqa | bool | Whether any affected attribute has is_cqa = True |
| base_concern_level | str | Initial concern: 'none' / 'minor' / 'major' / 'critical' |
| cluster_reason_summary | str | Why these attributes form one reviewer-facing risk unit |
| risk_semantics | str | Formal risk semantic label (see enum below) |

**risk_semantics enum:** `assay_gap`, `orthogonal_gap`, `contradiction`, `favorable_shift_requires_rationale`, `trend_requires_monitoring`, `no_precedent_low_confidence`, `cross_geography_divergence`, `pattern_concern_only`, `sufficient_evidence`

**Progressive Fields** (filled by pipeline stages): orthogonal_support_level, functional_support_level, lot_adequacy, contradiction_present, matched_reference_ids, concern_level, base_cluster_score, priority_score, likely_reviewer_concerns, recommended_followup_type, package_blocking

### 1.3 AuthorityContextPack (`schemas/authority_context_pack.py`)

**Purpose:** Structured authority evidence bundle. One pack per cluster + one case-level pack. Provides evidence FACTS only. Must NEVER embed verdict direction.

**Hard Rules:**
- No confidence modifier, support_direction, verdict_implication, or any field that prescribes a conclusion.
- `n_refs_by_conclusion` is DESCRIPTIVE ONLY. It must never be consumed to infer verdict direction, support a proceed/supplement decision, or substitute for conservative_policy evaluation.
- Case-level pack must NOT be the simple union of cluster packs. It must independently summarize package-wide authority posture.

**RefEntry sub-object:** entry_id, title, source, authority_quality_tier, relevance_score, decision_relevance_note

**Fields:** pack_id, scope_level, scope_id, normative_refs, precedent_refs, method_refs, concern_pattern_refs, n_refs_by_type, n_refs_by_conclusion, authority_conflict_flag, temporal_conflict_flag, geography_conflict_flag, authority_sparsity_flag, top_decision_drivers, fallback_flags

### 1.4 PackageDecision (`schemas/package_decision.py`)

**Purpose:** Terminal judgment object. Must answer: what was decided, why, how confident, and what would change it.

**Fields:**

| Field | Type | Semantics |
|---|---|---|
| case_id | str | Links to CaseContext |
| package_verdict | str | proceed / proceed_with_conditions / supplement_required / investigation_required / defer_package |
| confidence | float | 0-1.0 after all conservative adjustments |
| confidence_band | str | 'high' (>0.8) / 'moderate' (0.5-0.8) / 'low' (<0.5) |
| blocking_cluster_ids | List[str] | Clusters that drove verdict to current level |
| supporting_cluster_ids | List[str] | Clusters with strong authority evidence |
| required_followups | List[Dict] | {type, target_cluster_id, rationale} |
| predicted_reviewer_concerns | List[Dict] | {concern_text, source_cluster_id, authority_basis, severity, response_pressure_score} |
| authority_confidence_summary | str | Human-readable authority posture |
| decision_rule_ids | List[str] | IDs from Decision Rule Catalog |
| provenance_chain_ids | List[str] | Links to ProvenanceChain records |
| abstain_flag | bool | True only per ABST rules |
| abstain_reason | str | Which ABST rule triggered |
| next_best_action | str | Single highest-impact action |
| what_would_change_verdict | List[Dict] | Counterfactual entries (Level 1 MVP) |

---

## 2. Judgment Contracts

### Contract 1: Cluster-First Judgment

No package verdict, priority ranking, or reviewer concern may be triggered directly by a single attribute score. All must be mediated by RiskCluster.

### Contract 2: Authority-Conditioned Judgment

Every high-value output (verdict confidence, cluster priority, reviewer concerns, next-best-action, abstain flag) must consume AuthorityContextPack. Matching is decision core, not enrichment.

### Contract 3: Counterfactual-and-Provenance Judgment

Every package-level judgment must answer three questions:
1. **Why this conclusion?** via `decision_rule_ids` + `provenance_chain_ids`
2. **What authority supports it?** via `authority_confidence_summary`
3. **What would change it?** via `what_would_change_verdict` Level 1

---

## 3. System-Wide Guardrails

| ID | Rule |
|---|---|
| GUARD-001 | Concern pattern refs alone CANNOT set `package_blocking = True` or escalate verdict. They may raise attention, lower confidence, or suggest follow-up only. |
| GUARD-002 | No precedent does not equal no judgment. Missing precedent triggers confidence downgrade + normative reasoning + possible human review. Never triggers default abstain. |
| GUARD-003 | No double penalization. Package-level adjustment accounts for what cluster-level already adjusted. Same gap cannot reduce confidence twice. |
| GUARD-004 | Reviewer concern engine may modify confidence and follow-up priority, but may NOT invent new verdict categories or blocking logic outside the rule catalog. |
| GUARD-005 | `n_refs_by_conclusion` in AuthorityContextPack is descriptive only. No judgment call may be derived directly from this field. |

---

## 4. Object Lifecycle

### Phase 1: Construction (Pipeline Entry)

1. **CaseContext** is created from validated pipeline inputs. It becomes **immutable** immediately after `__init__` completes. All downstream modules receive the same CaseContext instance.

### Phase 2: Cluster Formation (Cluster Builder Service)

2. `build_risk_clusters(case_context, attribute_results)` produces a `List[RiskCluster]` with all identity fields and `base_cluster_score` populated. Cluster formation follows the documented policy:
   - One `category_risk` per analytical category
   - CQA escalation for CQA attributes with concern >= major
   - `single_attribute_critical` for concern = critical
   - `cross_category_gap` for 2+ categories sharing gap type
   - `risk_semantics` assigned from cluster_type + base_concern + contains_cqa + gaps

### Phase 3: Authority Matching (Step 2 — Matcher)

3. Matcher produces one **AuthorityContextPack** per cluster + one independent case-level pack. Progressive fields filled: `orthogonal_support_level`, `functional_support_level`, `lot_adequacy`, `contradiction_present`, `matched_reference_ids`.

### Phase 4: Conservative Policy (Step 3 — Two-Stage)

4. **Cluster-level policy:** adjusts `concern_level`, `package_blocking` on each RiskCluster. References CLUST, FALL, GUARD rules. Must NOT touch PackageDecision fields.
5. **Package-level policy:** constructs PackageDecision. Consumes already-adjusted cluster fields. References AGGR, ABST, GEOG, SHIFT rules. Must NOT touch RiskCluster fields.

### Phase 5: Reviewer Concern Engine (Step 4)

6. Generates `ReviewerConcern` entries. May modify `PackageDecision.confidence` and cluster `priority_score` within bounds set by SHIFT-002 and GUARD-004. Populates `likely_reviewer_concerns`, `recommended_followup_type`.

### Phase 6: Terminal Output

7. **PackageDecision** is the terminal artifact. It must contain: verdict, confidence, blocking/supporting clusters, follow-ups, reviewer concerns, authority summary, decision rule IDs, provenance chain IDs, abstain status, next-best-action, and counterfactual entries.

---

## 5. What NOT to Do

- **Do not** redesign UI before Steps 0-4 are stable.
- **Do not** expand reference coverage before judgment semantics, gold behaviors, and rule catalog are frozen.
- **Do not** merge repos, build graph DB, or repackage reg-intel as an independent service.
- **Do not** add future-reserved fields as required `__init__` parameters or pipeline contract dependencies.
- **Do not** use `n_refs_by_conclusion` to infer verdict direction or make judgment calls.
- **Do not** construct case-level AuthorityContextPack as a simple union of cluster packs.
- **Do not** allow package-level policy to modify RiskCluster fields or cluster-level policy to modify PackageDecision fields.
- **Do not** trigger abstain based solely on missing precedent (GUARD-002).
- **Do not** allow concern pattern refs alone to block a package (GUARD-001).
- **Do not** introduce new implicit judgment rules in implementation code without first adding them to the Decision Rule Catalog (GUARD-005 / RULE-AUTHORITY).

---

## 6. Cluster Formation Policy Reference

| Policy | Trigger | Cluster Type |
|---|---|---|
| Primary | One per analytical category | `category_risk` |
| CQA Escalation | CQA attribute + concern >= major | `cqa_concern` |
| Single-Attribute Critical | concern = critical | `single_attribute_critical` |
| Cross-Category Gap | 2+ categories share gap type | `cross_category_gap` |

`risk_semantics` is derived from `cluster_type` + `base_concern_level` + `contains_cqa` + `identified_gaps`.
