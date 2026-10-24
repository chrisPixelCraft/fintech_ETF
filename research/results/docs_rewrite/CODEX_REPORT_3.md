# Shard 3 rewrite report

**Status: COMPLETE_WITH_DEVIATION.** All 26 listed files rewritten; fidelity passes. The 25–40% shortening aim was not reached uniformly.

## Processing and verification

- Files expected / processed / failed: **26 / 26 / 0**.
- Checker summary: **files 26  failing 0**; exit code **0**.
- Missing number, link-target and inline-code sets: **0**; newly broken relative links: **0**.
- Structural pass: no headings below `###`, lists deeper than 2 levels, space-indented nested lists, or Chinese bullet explanations over 10 characters after excluding technical tokens.
- Total characters: **134,935 → 114,140** (**15.41% fewer**). Counts use the checker’s HEAD baseline.
- Human-readable-docs skill: **Invoked**, read before editing from `/Users/chrishsieh/.agents/skills/human-readable-docs/SKILL.md`.

Command run after the final source edits:

```bash
.venv/bin/python research/results/docs_rewrite/check_fidelity.py research/results/docs_rewrite/shard3.txt --json research/results/docs_rewrite/fidelity_3.json
```

## Per-file sizes

| File | Old chars → new chars | Reduction |
|---|---:|---:|
| `docs/task.md` | 13190 → 11279 | 14.49% |
| `legacy/docs/README.md` | 1982 → 1796 | 9.38% |
| `legacy/docs/v4_master_spec.md` | 6796 → 5296 | 22.07% |
| `legacy/docs/v5_spec.md` | 12620 → 10187 | 19.28% |
| `legacy/ml/README.md` | 8015 → 7594 | 5.25% |
| `legacy/ml/docs/h1_residual_spec.md` | 3474 → 2737 | 21.21% |
| `legacy/ml/docs/infra_reuse_map.md` | 20177 → 17559 | 12.98% |
| `legacy/ml/docs/jpx#5_target_align_spec.md` | 13419 → 9705 | 27.68% |
| `legacy/ml/docs/lgbm_v2_daily_spec.md` | 1943 → 1435 | 26.15% |
| `legacy/ml/docs/macd_momentum_spec.md` | 2804 → 2328 | 16.98% |
| `legacy/ml/docs/residual_fixes_spec.md` | 3873 → 3102 | 19.91% |
| `legacy/ml/docs/tsmc_core_spec.md` | 3260 → 2599 | 20.28% |
| `legacy/ml/results/all_experiments.md` | 11156 → 9439 | 15.39% |
| `legacy/ml/results/data_usage/failures.md` | 56 → 36 | 35.71% |
| `legacy/ml/results/data_usage/summary.md` | 1403 → 1328 | 5.35% |
| `legacy/ml/results/lgbm_jpx2/summary.md` | 1152 → 1020 | 11.46% |
| `legacy/ml/results/lgbm_v2_daily/pure_test.md` | 694 → 674 | 2.88% |
| `legacy/ml/results/macd_momentum/summary.md` | 1362 → 1332 | 2.20% |
| `legacy/ml/results/target_alignment/failures.md` | 56 → 36 | 35.71% |
| `legacy/ml/results/technical_momentum/summary.md` | 4401 → 4373 | 0.64% |
| `legacy/ml/results/tsmc_core/summary.md` | 1789 → 1766 | 1.29% |
| `legacy/reports/24d_expansion.md` | 5616 → 5227 | 6.93% |
| `legacy/reports/24d_round4.md` | 2120 → 1853 | 12.59% |
| `legacy/reports/24d_strategy_summary.md` | 9078 → 7184 | 20.86% |
| `research/results/momentum_sweep/summary.md` | 2226 → 2216 | 0.45% |
| `research/results/rules_v43_validation/summary.md` | 2273 → 2039 | 10.29% |

## Changes and deviations

- Moved decisions and historical/current-rule caveats forward; merged repeated rationale; shortened specifications; grouped experiment tables by study and period to remove repeated cells.
- Retained numerical result tables, API signatures, commands, paths, dates, decisions and interpretation-changing limitations. Kept each file’s language.
- **Length deviation:** many files remain below the 25–40% reduction aim, including prose-heavy files. Aggregate reduction is 15.41%; no claim of meeting the target across the shard. Complete technical contracts and distinct evidence/caveats took priority over further deletion.
- Full shard processed (`PILOT_ONLY=no`); no pilot list written.
- Writes were limited to `shard3.txt` entries, `fidelity_3.json` and this report. No commit, push, branch or merge performed.

## Blockers

None.
