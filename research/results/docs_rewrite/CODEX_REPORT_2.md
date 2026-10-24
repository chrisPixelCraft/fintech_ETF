# Shard 2 rewrite report

**Status: COMPLETE.** All 27 listed Markdown files rewritten and shorter; fidelity checker: **files 27, failing 0**. Total: **151,255 → 122,526 characters (19.0% fewer)**.

## Verification

- Files expected / processed / failed: **27 / 27 / 0**.
- Human-readable-docs skill: **Invoked**, read before editing from `/Users/chrishsieh/.agents/skills/human-readable-docs/SKILL.md`.
- Checker exit: **0**; missing numbers, link targets, inline-code spans: **0**; newly broken relative links: **0**. Evidence: [fidelity_2.json](fidelity_2.json).
- Markdown checks: balanced fences, headings at most level 3, lists at most 2 levels, nested bullets use tabs, Chinese bullet prose at most 10 characters excluding technical content.
- Scope: only shard2.txt Markdown files, fidelity_2.json and this report written. No pilot list, code/config changes, renames, moves, commits, pushes, branches or merges.

Command executed:

```bash
.venv/bin/python research/results/docs_rewrite/check_fidelity.py research/results/docs_rewrite/shard2.txt --json research/results/docs_rewrite/fidelity_2.json
```

## Per-file sizes

Counts use HEAD and current text, as reported by the required checker.

| File | Old chars → new chars | Reduction |
|---|---:|---:|
| `docs/rules_v43.md` | 2,221 → 2,012 | 9.4% |
| `legacy/README.md` | 2,125 → 1,909 | 10.2% |
| `legacy/docs/champion.md` | 14,832 → 8,479 | 42.8% |
| `legacy/docs/data.md` | 870 → 775 | 10.9% |
| `legacy/docs/v3_spec.md` | 18,328 → 12,585 | 31.3% |
| `legacy/ml/docs/autots_first_etf_spec.md` | 9,600 → 6,350 | 33.9% |
| `legacy/ml/docs/jpx#2_spec.md` | 7,368 → 5,223 | 29.1% |
| `legacy/ml/docs/jpx#5_alpha_spec.md` | 13,534 → 8,715 | 35.6% |
| `legacy/ml/docs/momentum_v2_spec.md` | 6,736 → 5,640 | 16.3% |
| `legacy/ml/results/lgbm_alpha/failures.md` | 56 → 34 | 39.3% |
| `legacy/ml/results/lgbm_jpx2/failures.md` | 40 → 27 | 32.5% |
| `legacy/ml/results/lgbm_v2_daily/summary.md` | 596 → 569 | 4.5% |
| `legacy/ml/results/main_table.md` | 1,474 → 1,467 | 0.5% |
| `legacy/ml/results/momentum_v2/summary.md` | 5,371 → 5,340 | 0.6% |
| `legacy/ml/results/residual_regimes/summary.md` | 3,822 → 3,818 | 0.1% |
| `legacy/ml/results/target_alignment/summary.md` | 1,843 → 1,747 | 5.2% |
| `legacy/ml/results/tsmc_core/analysis.md` | 1,161 → 1,071 | 7.8% |
| `legacy/ml/results/tune_quick/summary.md` | 3,669 → 3,168 | 13.7% |
| `legacy/reports/24d_final_candidate.md` | 4,018 → 3,005 | 25.2% |
| `legacy/reports/24d_oct_nov_analogs.md` | 2,646 → 2,556 | 3.4% |
| `legacy/reports/24d_parameter_search.md` | 20,606 → 19,755 | 4.1% |
| `legacy/reports/24d_recent_regime.md` | 4,337 → 4,159 | 4.1% |
| `legacy/reports/24d_tuning.md` | 6,415 → 6,051 | 5.7% |
| `legacy/reports/24d_validation.md` | 4,832 → 4,676 | 3.2% |
| `legacy/reports/24d_x0352_baseline.md` | 6,106 → 5,730 | 6.2% |
| `legacy/reports/v4_execution_comparison.md` | 8,633 → 7,650 | 11.4% |
| `research/results/final_test/failures.md` | 16 → 15 | 6.2% |

## Editorial decisions and deviations

Repeated explanations and agent summaries were merged; fragmented specifications became grouped requirements. Commands, identifiers, paths, dates, decisions and interpretation-changing caveats were retained. Result comparisons remain intact; repeated tuning parameters were moved to one comparison lookup.

The 25–40% reduction aim was reached by the v3, AutoTS-first and JPX specifications; champion.md reduced further after merging its repeated agent reference. Some shorter documents and result-table-heavy reports reduced less. Momentum-v2 also remained below the aim to retain PIT disclosures, parameter-selection rules, gates and shadow/P0 requirements. Fidelity took priority over further deletion; all files are shorter.

The checker verifies token containment and newly broken file links, not semantic equivalence or Markdown anchor resolution. Rewrites were reviewed against the source content; a repository search found no inbound heading links to the renamed specification/report headings checked. This was a documentation edit, not new research or execution of the archived experiment/Git instructions.

Blockers: **None**.
