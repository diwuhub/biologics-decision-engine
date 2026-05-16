# Rule Addition Process

**Step 0D Deliverable O: Protocol for Proposing and Approving New Catalog Rules**

This document prevents ad-hoc rule invention during Steps 1-5. Any new judgment logic must follow this process before it can be referenced in code.

---

## Why This Process Exists

The Decision Rule Catalog (GUARD-005) requires that any logic changing `cluster.concern_level`, `package_blocking`, `package_verdict`, `confidence`, or `abstain_flag` must reference an existing catalog rule. Without a formal addition process, developers will either:

1. Add rules informally (defeating auditability), or
2. Implement judgment logic without rules (violating GUARD-005)

---

## Process Steps

### Step 1: Identify the Need

- A developer encounters a situation where existing rules do not cover required judgment logic
- Document: (a) what judgment change is needed, (b) which gold case(s) expose the gap, (c) why existing rules are insufficient

### Step 2: Draft the Rule

Create a rule entry with ALL required fields:

```yaml
- rule_id: "{CATEGORY}-{NNN}"
  category: "{AGGR|CLUST|FALL|GUARD|ABST|GEOG|SHIFT}"
  scope: "{cluster|package|concern|abstain}"
  rule_text: "Clear statement of the rule"
  allowed_inputs:
    - "List of inputs this rule may consume"
  allowed_outputs:
    - "List of outputs this rule may produce"
  forbidden_effects:
    - "What this rule MUST NOT do"
  related_gold_cases: ["GC-XX"]
```

### Step 3: Review Checklist

Before approval, verify:

- [ ] Rule ID follows naming convention (`{CATEGORY}-{NNN}`)
- [ ] Rule ID is unique (not already in catalog)
- [ ] Scope is correct (cluster/package/concern/abstain)
- [ ] Rule text is specific enough to be testable
- [ ] Allowed inputs are explicitly listed
- [ ] Forbidden effects are explicitly stated
- [ ] At least one gold case is referenced
- [ ] Rule does not conflict with existing guardrails (GUARD-001 through GUARD-005)
- [ ] Rule does not duplicate an existing rule's coverage

### Step 4: Add to Catalog

1. Add the rule to `docs/DECISION_RULE_CATALOG.md` under the appropriate category
2. Add the rule to `config/rule_catalog.yaml` in the same position
3. Run `python3 -m pytest tests/validate_gold_cases.py -k TestRuleCatalogCrossReference -v` to verify integrity
4. Update any affected gold case expected outputs if the new rule changes expected behavior

### Step 5: Reference in Code

Only after Steps 1-4 are complete:

1. Implement the judgment logic in the appropriate module
2. Add the rule_id to `decision_rule_ids` when the rule is applied
3. Add a comment citing the rule_id at the implementation site

---

## Rule ID Numbering Convention

| Category | Prefix | Current Max | Next Available |
|---|---|---|---|
| Package Aggregation | AGGR- | 006 | 007 |
| Cluster Escalation | CLUST- | 004 | 005 |
| No-Precedent Fallback | FALL- | 003 | 004 |
| Guardrails | GUARD- | 005 | 006 |
| Abstain Triggers | ABST- | 002 | 003 |
| Geography Divergence | GEOG- | 002 | 003 |
| Verdict Shift | SHIFT- | 002 | 003 |

---

## What Is NOT a New Rule

The following do NOT require a new catalog rule:

- Bug fixes that correct existing rule implementation
- Performance optimizations that do not change judgment outputs
- UI/display changes that do not affect verdict, confidence, or blocking
- Adding new gold cases (these test existing rules, not add new ones)
- Enriching authority data (Step 5) without changing judgment logic
