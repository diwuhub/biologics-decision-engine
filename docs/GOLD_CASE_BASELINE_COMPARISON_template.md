# Gold Case Baseline Comparison -- Step {N}

**Generated:** {timestamp}
**Baseline:** docs/GOLD_CASE_BASELINE.json
**Post-Step Output:** docs/GOLD_CASE_BASELINE_COMPARISON_step{N}.json

---

## Summary

| Metric | Baseline | Post-Step {N} | Delta |
|---|---|---|---|
| Verdict match rate | {baseline_verdict_match} / 12 | {post_verdict_match} / 12 | {delta_verdict} |
| Confidence band match rate | {baseline_conf_match} / 12 | {post_conf_match} / 12 | {delta_conf} |
| Blocking cluster match rate | {baseline_block_match} / 12 | {post_block_match} / 12 | {delta_block} |
| Abstain flag match rate | {baseline_abstain_match} / 12 | {post_abstain_match} / 12 | {delta_abstain} |

---

## Per-Case Detail

| Case | Pre-Refactor Verdict | Post-Step Verdict | Expected Verdict | Verdict Match? |
|---|---|---|---|---|
| GC-01 | {gc01_pre} | {gc01_post} | proceed | {gc01_match} |
| GC-02 | {gc02_pre} | {gc02_post} | supplement_required | {gc02_match} |
| GC-03 | {gc03_pre} | {gc03_post} | proceed_with_conditions | {gc03_match} |
| GC-04 | {gc04_pre} | {gc04_post} | proceed | {gc04_match} |
| GC-05 | {gc05_pre} | {gc05_post} | investigation_required | {gc05_match} |
| GC-06 | {gc06_pre} | {gc06_post} | proceed_with_conditions | {gc06_match} |
| GC-07 | {gc07_pre} | {gc07_post} | defer_package | {gc07_match} |
| GC-08 | {gc08_pre} | {gc08_post} | proceed_with_conditions | {gc08_match} |
| GC-09 | {gc09_pre} | {gc09_post} | supplement_required | {gc09_match} |
| GC-10 | {gc10_pre} | {gc10_post} | proceed_with_conditions | {gc10_match} |
| GC-11 | {gc11_pre} | {gc11_post} | supplement_required | {gc11_match} |
| GC-12 | {gc12_pre} | {gc12_post} | proceed_with_conditions | {gc12_match} |

---

## Confidence Band Detail

| Case | Pre-Refactor Band | Post-Step Band | Expected Band | Match? |
|---|---|---|---|---|
| GC-01 | {gc01_pre_conf} | {gc01_post_conf} | high | {gc01_conf_match} |
| GC-02 | {gc02_pre_conf} | {gc02_post_conf} | moderate | {gc02_conf_match} |
| GC-03 | {gc03_pre_conf} | {gc03_post_conf} | moderate | {gc03_conf_match} |
| GC-04 | {gc04_pre_conf} | {gc04_post_conf} | high | {gc04_conf_match} |
| GC-05 | {gc05_pre_conf} | {gc05_post_conf} | low | {gc05_conf_match} |
| GC-06 | {gc06_pre_conf} | {gc06_post_conf} | moderate | {gc06_conf_match} |
| GC-07 | {gc07_pre_conf} | {gc07_post_conf} | low | {gc07_conf_match} |
| GC-08 | {gc08_pre_conf} | {gc08_post_conf} | moderate | {gc08_conf_match} |
| GC-09 | {gc09_pre_conf} | {gc09_post_conf} | moderate | {gc09_conf_match} |
| GC-10 | {gc10_pre_conf} | {gc10_post_conf} | moderate | {gc10_conf_match} |
| GC-11 | {gc11_pre_conf} | {gc11_post_conf} | moderate | {gc11_conf_match} |
| GC-12 | {gc12_pre_conf} | {gc12_post_conf} | moderate | {gc12_conf_match} |

---

## Blocking Cluster Detail

| Case | Pre-Refactor Blocking | Post-Step Blocking | Expected Blocking | Match? |
|---|---|---|---|---|
| GC-01 | {gc01_pre_block} | {gc01_post_block} | 0 | {gc01_block_match} |
| GC-02 | {gc02_pre_block} | {gc02_post_block} | 1 | {gc02_block_match} |
| GC-05 | {gc05_pre_block} | {gc05_post_block} | 1 | {gc05_block_match} |
| GC-07 | {gc07_pre_block} | {gc07_post_block} | >=2 | {gc07_block_match} |
| GC-11 | {gc11_pre_block} | {gc11_post_block} | 1 | {gc11_block_match} |

---

## Critical Behavioral Checks

| Check | Expected | Pre-Refactor | Post-Step |
|---|---|---|---|
| GC-07 abstains | True | {gc07_pre_abstain} | {gc07_post_abstain} |
| GC-10 NOT blocking | 0 blocking | {gc10_pre_block} | {gc10_post_block} |
| GC-06 NOT abstain | False | {gc06_pre_abstain} | {gc06_post_abstain} |
| GC-11 detects hidden gap | 1 blocking | {gc11_pre_block} | {gc11_post_block} |
| GC-12 NOT overreact | proceed_with_conditions | {gc12_pre_verdict} | {gc12_post_verdict} |
