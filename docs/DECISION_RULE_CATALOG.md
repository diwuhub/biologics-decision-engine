# Decision Rule Catalog

**Step 0D: Freeze Decision Rule Catalog**

The judgment authority layer. Every rule has: an ID, scope (cluster/package/concern/abstain), rule statement, allowed inputs, allowed outputs, forbidden effects, and the gold cases it governs.

**Rule Authority Enforcement:** Any judgment logic not traceable to a rule in this catalog is unauthorized. Conservative policy, package aggregation, reviewer concern engine, and cluster builder may ONLY apply logic corresponding to a cataloged rule. Steps 1-5 may NOT introduce new implicit judgment rules in implementation code.

---

## Category 1: Package Aggregation Rules (AGGR)

### AGGR-001: CQA-Weighted Aggregation

| Field | Value |
|---|---|
| Rule ID | AGGR-001 |
| Scope | package |
| Rule Text | Package-level verdict aggregation must weight CQA attributes at 1.5x relative to non-CQA attributes. No simple averaging across all attributes. |
| Allowed Inputs | RiskCluster.base_cluster_score, RiskCluster.contains_cqa, attribute scores |
| Allowed Outputs | PackageDecision.confidence, PackageDecision.package_verdict |
| Forbidden Effects | May not modify RiskCluster fields. May not bypass cluster mediation. |
| Related Gold Cases | GC-01, GC-02, GC-11 |

### AGGR-002: Blocking Cluster Escalation

| Field | Value |
|---|---|
| Rule ID | AGGR-002 |
| Scope | package |
| Rule Text | Any cluster with package_blocking = True forces package verdict to supplement_required or worse, regardless of other cluster scores. |
| Allowed Inputs | RiskCluster.package_blocking, RiskCluster.concern_level |
| Allowed Outputs | PackageDecision.package_verdict, PackageDecision.blocking_cluster_ids |
| Forbidden Effects | May not set package_blocking on clusters (cluster-level only). |
| Related Gold Cases | GC-02, GC-05, GC-07, GC-11 |

### AGGR-003: Package-Level Gap Detection

| Field | Value |
|---|---|
| Rule ID | AGGR-003 |
| Scope | package |
| Rule Text | Package aggregation must detect gaps that no single attribute surfaces. If a CQA cluster is missing a REQUIRED method type (per ICH Q5E), the package must identify this as a blocking gap even if all individual attributes score comparable. |
| Allowed Inputs | RiskCluster.affected_attribute_ids, CaseContext.identified_gaps, cluster method coverage |
| Allowed Outputs | PackageDecision.package_verdict, PackageDecision.blocking_cluster_ids |
| Forbidden Effects | May not override individual attribute scores. May not invent new cluster types. |
| Related Gold Cases | GC-11 |

### AGGR-004: Multi-Cluster Confidence Floor

| Field | Value |
|---|---|
| Rule ID | AGGR-004 |
| Scope | package |
| Rule Text | When 2+ clusters have concern_level >= major, package confidence is floored at 0.4 regardless of individual cluster scores. |
| Allowed Inputs | RiskCluster.concern_level across all clusters |
| Allowed Outputs | PackageDecision.confidence |
| Forbidden Effects | May not modify cluster concern_levels. |
| Related Gold Cases | GC-05, GC-07 |

### AGGR-005: Favorable Shift Package Handling

| Field | Value |
|---|---|
| Rule ID | AGGR-005 |
| Scope | package |
| Rule Text | A favorable shift in one cluster does not offset concerns in another cluster. Package verdict aggregation treats favorable shifts as informational, not as credit against other concerns. |
| Allowed Inputs | RiskCluster.risk_semantics (favorable_shift_requires_rationale), other cluster concern_levels |
| Allowed Outputs | PackageDecision.package_verdict |
| Forbidden Effects | May not use favorable shift to cancel blocking clusters or reduce concern_level of unrelated clusters. |
| Related Gold Cases | GC-04 |

### AGGR-006: Temporal Sparsity Confidence Cap

| Field | Value |
|---|---|
| Rule ID | AGGR-006 |
| Scope | package |
| Rule Text | When all supporting authority evidence has temporal_status = historical (pre-QbD or > 10 years), package confidence is capped at 0.65 and verdict cannot be proceed (must be at minimum proceed_with_conditions or supplement_required). |
| Allowed Inputs | AuthorityContextPack.temporal_conflict_flag, ref temporal_status |
| Allowed Outputs | PackageDecision.confidence, PackageDecision.package_verdict |
| Forbidden Effects | May not modify cluster fields. May not lower confidence below the cap floor. |
| Related Gold Cases | GC-09 |

---

## Category 2: Cluster Escalation Rules (CLUST)

### CLUST-001: CQA Major Escalation

| Field | Value |
|---|---|
| Rule ID | CLUST-001 |
| Scope | cluster |
| Rule Text | CQA attributes with concern_level >= major must be escalated to a dedicated cqa_concern cluster, separate from the primary category_risk cluster. |
| Allowed Inputs | Attribute concern_level, is_cqa flag |
| Allowed Outputs | New RiskCluster with cluster_type = cqa_concern |
| Forbidden Effects | May not modify the original category_risk cluster's attributes. |
| Related Gold Cases | GC-02, GC-05 |

### CLUST-002: Single Attribute Critical Isolation

| Field | Value |
|---|---|
| Rule ID | CLUST-002 |
| Scope | cluster |
| Rule Text | Any attribute with concern_level = critical must form its own single_attribute_critical cluster, regardless of category or CQA status. |
| Allowed Inputs | Attribute concern_level |
| Allowed Outputs | New RiskCluster with cluster_type = single_attribute_critical |
| Forbidden Effects | May not leave critical attributes in category_risk clusters. |
| Related Gold Cases | GC-05, GC-07 |

### CLUST-003: Cross-Category Gap Formation

| Field | Value |
|---|---|
| Rule ID | CLUST-003 |
| Scope | cluster |
| Rule Text | When 2+ analytical categories share the same gap type, form an additional cross_category_gap cluster spanning the affected attributes. |
| Allowed Inputs | Attribute gaps, category assignments |
| Allowed Outputs | New RiskCluster with cluster_type = cross_category_gap |
| Forbidden Effects | May not remove attributes from their primary clusters. |
| Related Gold Cases | GC-06, GC-09 |

### CLUST-004: Contradiction Package-Blocking

| Field | Value |
|---|---|
| Rule ID | CLUST-004 |
| Scope | cluster |
| Rule Text | Any cluster with risk_semantics = contradiction must have package_blocking = True pending conflict resolution. Contradiction means methods within the same analytical category produce conflicting comparability conclusions. |
| Allowed Inputs | RiskCluster.risk_semantics, attribute contradiction_present flags |
| Allowed Outputs | RiskCluster.package_blocking = True |
| Forbidden Effects | May not set package_blocking on clusters without contradiction evidence. |
| Related Gold Cases | GC-05, GC-07 |

---

## Category 3: No-Precedent Fallback Rules (FALL)

### FALL-001: No Precedent Does Not Trigger Abstain

| Field | Value |
|---|---|
| Rule ID | FALL-001 |
| Scope | package |
| Rule Text | Missing precedent triggers confidence downgrade + normative reasoning + possible human review. It NEVER triggers default abstain. The system must make a judgment using available normative guidance. (Guardrail 2) |
| Allowed Inputs | AuthorityContextPack.authority_sparsity_flag, precedent_refs count |
| Allowed Outputs | PackageDecision.confidence (downgraded), PackageDecision.required_followups |
| Forbidden Effects | MUST NOT set abstain_flag = True solely due to missing precedent. |
| Related Gold Cases | GC-06, GC-09 |

### FALL-002: Mixed Weak Evidence Non-Escalation

| Field | Value |
|---|---|
| Rule ID | FALL-002 |
| Scope | package |
| Rule Text | When evidence is weak but not absent (some normative refs, limited precedent, no pattern alignment), the system must not overreact. Verdict should be proceed_with_conditions, not supplement_required or worse. Mixed weak evidence does not constitute a blocking gap. |
| Allowed Inputs | AuthorityContextPack ref counts, authority_sparsity_flag |
| Allowed Outputs | PackageDecision.package_verdict (proceed_with_conditions), PackageDecision.confidence |
| Forbidden Effects | MUST NOT escalate to supplement_required or investigation_required purely due to evidence weakness. MUST NOT set package_blocking. |
| Related Gold Cases | GC-12 |

### FALL-003: Normative Fallback Authority

| Field | Value |
|---|---|
| Rule ID | FALL-003 |
| Scope | package |
| Rule Text | When no precedent is available, ICH guidelines serve as primary authority source. The system must articulate its judgment basis using normative references and flag the lack of precedent as a confidence modifier, not a verdict modifier. |
| Allowed Inputs | AuthorityContextPack.normative_refs, precedent_refs |
| Allowed Outputs | PackageDecision.authority_confidence_summary, PackageDecision.confidence |
| Forbidden Effects | May not treat normative-only authority as grounds for escalation beyond proceed_with_conditions (absent other concerns). |
| Related Gold Cases | GC-06, GC-12 |

---

## Category 4: Guardrail Rules (GUARD)

### GUARD-001: Concern Pattern Cannot Block

| Field | Value |
|---|---|
| Rule ID | GUARD-001 |
| Scope | cluster, package |
| Rule Text | Concern pattern references alone CANNOT set package_blocking = True or escalate verdict. They may raise attention, lower confidence, or suggest follow-up only. |
| Allowed Inputs | AuthorityContextPack.concern_pattern_refs |
| Allowed Outputs | Confidence adjustment (minor), follow-up suggestions |
| Forbidden Effects | MUST NOT set package_blocking = True. MUST NOT escalate verdict based solely on concern patterns. |
| Related Gold Cases | GC-10 |

### GUARD-002: No Precedent Does Not Mean No Judgment

| Field | Value |
|---|---|
| Rule ID | GUARD-002 |
| Scope | package |
| Rule Text | No precedent does not equal no judgment. Missing precedent triggers confidence downgrade + normative reasoning + possible human review. Never triggers default abstain. |
| Allowed Inputs | AuthorityContextPack.authority_sparsity_flag |
| Allowed Outputs | Confidence downgrade, human review flag |
| Forbidden Effects | MUST NOT set abstain_flag = True due to missing precedent alone. |
| Related Gold Cases | GC-06, GC-09 |

### GUARD-003: No Double Penalization

| Field | Value |
|---|---|
| Rule ID | GUARD-003 |
| Scope | package |
| Rule Text | Package-level adjustment must account for what cluster-level already adjusted. The same gap or concern cannot reduce confidence twice -- once at cluster level and again at package level. |
| Allowed Inputs | RiskCluster.concern_level (already adjusted), PackageDecision.confidence |
| Allowed Outputs | PackageDecision.confidence |
| Forbidden Effects | MUST NOT apply the same penalty at both cluster and package level. |
| Related Gold Cases | GC-01, GC-09 |

### GUARD-004: Concern Engine Boundary

| Field | Value |
|---|---|
| Rule ID | GUARD-004 |
| Scope | concern |
| Rule Text | Reviewer concern engine may modify confidence and follow-up priority, but may NOT invent new verdict categories or blocking logic outside the rule catalog. |
| Allowed Inputs | Preliminary PackageDecision, ReviewerConcerns |
| Allowed Outputs | PackageDecision.confidence (within bounds), cluster priority_score |
| Forbidden Effects | MUST NOT create new verdict categories. MUST NOT set package_blocking outside cataloged rules. |
| Related Gold Cases | GC-04, GC-10 |

### GUARD-005: Rule Authority Enforcement

| Field | Value |
|---|---|
| Rule ID | GUARD-005 |
| Scope | package, cluster, concern |
| Rule Text | Any logic in Steps 1-5 that changes cluster.concern_level, package_blocking, package_verdict, confidence, or abstain_flag MUST reference an existing rule from this catalog. No new implicit judgment rules may be introduced in implementation code. |
| Allowed Inputs | All judgment-affecting inputs |
| Allowed Outputs | All judgment-affecting outputs |
| Forbidden Effects | Any judgment change without a catalog rule reference is unauthorized. |
| Related Gold Cases | GC-01 through GC-12 |

---

## Category 5: Abstain Trigger Rules (ABST)

### ABST-001: Multi-Signal Abstain

| Field | Value |
|---|---|
| Rule ID | ABST-001 |
| Scope | package |
| Rule Text | Abstain (defer_package + abstain_flag = True) is triggered ONLY when ALL of the following are present simultaneously: (1) multiple critical attributes with contradictory inter-method data, (2) conflicting regulatory stances (US vs EU), AND (3) no applicable normative guidance for this combination. No single condition triggers abstain alone. |
| Allowed Inputs | RiskCluster contradiction flags, AuthorityContextPack geography_conflict_flag, authority_sparsity_flag, normative_refs |
| Allowed Outputs | PackageDecision.abstain_flag = True, PackageDecision.package_verdict = defer_package |
| Forbidden Effects | MUST NOT trigger abstain from any single condition alone. |
| Related Gold Cases | GC-07 |

### ABST-002: Abstain Requires Explanation

| Field | Value |
|---|---|
| Rule ID | ABST-002 |
| Scope | package |
| Rule Text | When abstain_flag = True, abstain_reason must be populated with a specific explanation citing which conditions of ABST-001 are met. Generic "insufficient data" is not acceptable. |
| Allowed Inputs | ABST-001 trigger conditions |
| Allowed Outputs | PackageDecision.abstain_reason (specific text) |
| Forbidden Effects | May not leave abstain_reason empty when abstain_flag = True. |
| Related Gold Cases | GC-07 |

---

## Category 6: Geography Divergence Rules (GEOG)

### GEOG-001: Geography Conflict Detection

| Field | Value |
|---|---|
| Rule ID | GEOG-001 |
| Scope | cluster, package |
| Rule Text | When target_geography = global and AuthorityContextPack detects divergent acceptance criteria between FDA and EMA for any CQA cluster, set geography_conflict_flag = True and route to human review. |
| Allowed Inputs | CaseContext.target_geography, AuthorityContextPack.geography_conflict_flag |
| Allowed Outputs | Geography conflict flag, human review routing, required_followups |
| Forbidden Effects | Geography conflict alone does not trigger abstain (see ABST-001 for combined trigger). |
| Related Gold Cases | GC-08 |

### GEOG-002: Geography-Conditioned Verdict

| Field | Value |
|---|---|
| Rule ID | GEOG-002 |
| Scope | package |
| Rule Text | When geography_conflict_flag = True but all attributes are individually comparable, verdict should be proceed_with_conditions (not supplement_required), with a required follow-up for geography-specific filing strategy. |
| Allowed Inputs | Geography_conflict_flag, attribute comparability scores |
| Allowed Outputs | PackageDecision.package_verdict = proceed_with_conditions, required_followups |
| Forbidden Effects | MUST NOT escalate to supplement_required solely due to geography divergence when attributes are comparable. |
| Related Gold Cases | GC-08 |

---

## Category 7: Verdict Shift Rules (SHIFT)

### SHIFT-001: Favorable Shift Rationale Requirement

| Field | Value |
|---|---|
| Rule ID | SHIFT-001 |
| Scope | cluster, package |
| Rule Text | When a CQA attribute shows a favorable shift (better-than-reference), the cluster risk_semantics must be set to favorable_shift_requires_rationale. The system must flag the need for immunogenicity impact rationale. A favorable shift does not automatically upgrade verdict. |
| Allowed Inputs | Attribute delta indicating improvement, is_cqa flag |
| Allowed Outputs | RiskCluster.risk_semantics = favorable_shift_requires_rationale, reviewer concern for rationale |
| Forbidden Effects | May not auto-upgrade verdict. May not suppress the rationale requirement. |
| Related Gold Cases | GC-04 |

### SHIFT-002: Concern-Driven Confidence Shift

| Field | Value |
|---|---|
| Rule ID | SHIFT-002 |
| Scope | concern |
| Rule Text | ReviewerConcerns with affects_verdict_confidence = True may reduce PackageDecision.confidence by at most 0.15 per concern and 0.30 total across all concerns. Each such concern must cite applied_rule_id from this catalog. |
| Allowed Inputs | ReviewerConcern.response_pressure_score, applied_rule_id |
| Allowed Outputs | PackageDecision.confidence (reduced within bounds) |
| Forbidden Effects | MUST NOT reduce confidence by more than 0.30 total. MUST NOT modify confidence without applied_rule_id. |
| Related Gold Cases | GC-05, GC-07, GC-09 |
