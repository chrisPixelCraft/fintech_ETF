# Shard 1 documentation rewrite

**Status: COMPLETE; fidelity PASS.** Processed all 25 files. Total: 134,822 → 118,701 characters (12.0% shorter). The 25–40% shortening aim was not reached uniformly.

## Scope and outcome

- Files expected / processed / failed: **25 / 25 / 0**.
- Human-readable-docs skill: **Invoked**, read before Markdown edits from `/Users/chrishsieh/.agents/skills/human-readable-docs/SKILL.md`.
- Reviewed the three approved pilot files; retained their approved structure. Rewrote the remaining 22 files.
- Moved result decisions forward, shortened explanations and workflows, and merged repeated conditions. Preserved result tables, formulas, execution boundaries and interpretive caveats.
- Writes were limited to shard1.txt members, `fidelity_1.json`, and this report. No files renamed or moved; no commits, pushes, branches or merges.

## Verification

```bash
.venv/bin/python research/results/docs_rewrite/check_fidelity.py research/results/docs_rewrite/shard1.txt --json research/results/docs_rewrite/fidelity_1.json
```

Exit code **0**; final output: `files 25  failing 0`.

- Missing numbers / link targets / inline-code spans: **0**.
- Newly broken relative links: **0**. Existing broken links are excluded by the checker.
- Structure scan: no headings deeper than `###`, space-indented nested bullets or lists deeper than two levels outside fenced code.
- Reviewed wording against the task inputs for decisions, conditions, commands and caveats; the token checker alone does not establish semantic equivalence.
- No executable code changed; no strategy experiments or unit suites were run.

## Per-file size

Old counts are the committed HEAD baseline used by the mandatory checker, including the already-approved pilot reductions. Counts include Markdown syntax, code and tables.

| File | Old chars → new chars | Reduction |
|---|---:|---:|
| `docs/how_to_run.md` | 3,335 → 3,218 | 3.5% |
| `docs/production_spec.md` | 11,004 → 10,577 | 3.9% |
| `docs/strategy.md` | 5,659 → 5,441 | 3.9% |
| `legacy/docs/rules.md` | 876 → 789 | 9.9% |
| `legacy/docs/v2_double_check_rules.md` | 7,289 → 6,878 | 5.6% |
| `legacy/docs/v3_tuning.md` | 9,615 → 6,962 | 27.6% |
| `legacy/docs/v4_execution_validation_spec.md` | 7,518 → 6,248 | 16.9% |
| `legacy/ml/docs/autots_internals.md` | 26,664 → 20,839 | 21.8% |
| `legacy/ml/docs/final_test_plan.md` | 1,852 → 1,549 | 16.4% |
| `legacy/ml/docs/hybrid_spec.md` | 4,140 → 3,921 | 5.3% |
| `legacy/ml/docs/technical_momentum_spec.md` | 5,363 → 4,900 | 8.6% |
| `legacy/ml/results/h1_residual/summary.md` | 3,745 → 3,707 | 1.0% |
| `legacy/ml/results/hybrid/summary.md` | 1,028 → 900 | 12.5% |
| `legacy/ml/results/lgbm_alpha/summary.md` | 1,382 → 1,347 | 2.5% |
| `legacy/ml/results/mom25_report/summary.md` | 717 → 670 | 6.6% |
| `legacy/ml/results/residual_fixes/summary.md` | 2,570 → 2,275 | 11.5% |
| `legacy/reports/24d_failure_analysis.md` | 4,840 → 3,690 | 23.8% |
| `legacy/reports/24d_holdout.md` | 3,069 → 2,607 | 15.1% |
| `legacy/reports/24d_round3.md` | 6,048 → 5,641 | 6.7% |
| `legacy/reports/comparison.md` | 2,561 → 2,405 | 6.1% |
| `research/execution_proxy_calibration.md` | 2,014 → 1,808 | 10.2% |
| `research/results/final_test/summary.md` | 14,605 → 14,553 | 0.4% |
| `research/results/production_replay/summary_holdout.md` | 388 → 331 | 14.7% |
| `research/results/recent24_strategies/summary.md` | 3,642 → 3,422 | 6.0% |
| `research/results/rules_v43_validation/baseline.md` | 4,898 → 4,023 | 17.9% |

## Deviations and blockers

- Shortening target: partial. Dense tables, formulas, protected identifiers and caveats limit whole-file reductions; the approved pilot was retained. Most documents remain below a 25% whole-file reduction. No claim that every prose section reached 25–40% is made.
- The first checker run found two missing number forms in `research/results/recent24_strategies/summary.md`; restored their original context and reran successfully.
- No scope deviations or blockers. This report overwrites the shard 1 pilot report.
