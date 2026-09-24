#!/usr/bin/env python3
"""V5 final report: apply docs/v5_spec.md §7 gates to the frozen study and answer docs/champion.md §7.

Refuses to run unless <output>/verification.json is PASS for the current manifest.
Writes reports/v5_final.md and <output>/final_decision.json; writes configs/v5_final.json
only when the frozen preferred candidate is STABLE_CANDIDATE (submission stays BLOCK_SUBMISSION).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.v5_run import (EPS, canonical, clean, final_gates, paired, read_json, rel, repo, sha256,
                            summarize, write_json)

BOOTSTRAP_DRAWS, BOOTSTRAP_SEED = 2000, 20261026
FAMILY_NAMES = {'A': 'momentum', 'B': 'ensemble', 'C': 'rank', 'E': 'direct', 'V3': 'frozen V3'}
LIMITATIONS = [
    'Survivorship bias: the 2026-07-31 universe is applied back to 2010. Absolute returns are inflated; paired '
    'comparisons are less affected. Momentum tilts are likely the most exposed.',
    'Unrecorded capital reductions: 387 adjacent-session moves exceed the price limit (V4 audit). These distort '
    'returns for every family.',
    'Market impact at NT$1B is not modelled beyond official-average fills.',
    'Monthly episodes overlap by about 3 sessions; uncertainty uses a block bootstrap by calendar quarter, not '
    'independent-sample intervals.',
    'The holdout_retrospective split is not pristine (V3 was tuned on it; V4 viewed Jan/Jul windows). It can reject, '
    'not certify.',
]


def require_verification(output):
    path = output / 'verification.json'
    if not path.exists():
        raise SystemExit('Refusing to report: verification.json missing (run scripts/v5_verify.py)')
    verification = read_json(path)
    if verification.get('status') != 'PASS':
        raise SystemExit('Refusing to report: verification status is ' + str(verification.get('status')))
    if verification.get('manifest_sha256') != sha256(output / 'manifest.json'):
        raise SystemExit('Refusing to report: verification does not match the current manifest')
    if verification.get('freeze_sha256') != sha256(output / 'freeze.json'):
        raise SystemExit('Refusing to report: verification does not match the current freeze')
    return verification


def quarter(start):
    t = pd.Timestamp(start)
    return f'{t.year}Q{(t.month - 1) // 3 + 1}'


def block_bootstrap(deltas, quarters, statistic=np.median, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED):
    """Resample calendar quarters with replacement; returns the (5%, 95%) interval of the statistic."""
    deltas, quarters = np.asarray(deltas, float), np.asarray(quarters)
    if len(deltas) < 2:
        return (float('nan'), float('nan'))
    blocks = [deltas[quarters == q] for q in sorted(set(quarters))]
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(draws):
        pick = rng.integers(0, len(blocks), len(blocks))
        sample = np.concatenate([blocks[i] for i in pick])
        values.append(statistic(sample))
    return (float(np.quantile(values, .05)), float(np.quantile(values, .95)))


def paired_with_ci(records, a, b, splits):
    frame = pd.DataFrame(records)
    p = paired(frame, a, b, splits)
    starts = frame.drop_duplicates('episode').set_index('episode').start
    left = frame.loc[frame.candidate.eq(a)].set_index('episode').episode_return
    right = frame.loc[frame.candidate.eq(b)].set_index('episode').episode_return
    d = np.array([float(left[e]) - float(right[e]) for e in p['episodes']])
    q = [quarter(starts[e]) for e in p['episodes']]
    p['ci90_median_delta'] = block_bootstrap(d, q)
    p.pop('episodes')
    return p


def pooled_contrast(records, pairs, splits):
    """Median paired delta pooled over matched (to, from) candidate pairs, quarter-block CI."""
    frame = pd.DataFrame(records)
    frame = frame.loc[frame.split.isin(splits)]
    idx = frame.set_index(['candidate', 'episode'])
    deltas, quarters = [], []
    for to, frm in pairs:
        p = paired(frame, to, frm)
        for e in p['episodes']:
            deltas.append(float(idx.at[(to, e), 'episode_return']) - float(idx.at[(frm, e), 'episode_return']))
            quarters.append(quarter(idx.at[(to, e), 'start']))
    d = np.asarray(deltas)
    return dict(pairs=len(pairs), n=len(d), median_delta=float(np.median(d)) if len(d) else float('nan'),
                mean_delta=float(d.mean()) if len(d) else float('nan'),
                win_rate=float((d > 0).mean()) if len(d) else float('nan'),
                ci90_median_delta=block_bootstrap(d, quarters))


def matched_pairs(candidates, representative, family, axis, frm, to):
    by_id = {c['id']: c for c in candidates}
    out = []
    for c in candidates:
        if c['family'] != family or c['axes'].get(axis) != to:
            continue
        for o in candidates:
            if o['family'] == family and o['axes'].get(axis) == frm and all(
                    o['axes'].get(k) == c['axes'].get(k) for k in c['axes'] if k != axis):
                out.append((representative.get(c['id'], c['id']), representative.get(o['id'], o['id'])))
    del by_id
    return out


def pct(x):
    return 'n/a' if x is None or x != x else f'{100 * x:+.2f}pp'


def ret(x):
    return 'n/a' if x is None or x != x else f'{100 * x:.2f}%'


def ci(pair):
    lo, hi = pair
    return 'n/a' if lo != lo else f'[{100 * lo:+.2f}, {100 * hi:+.2f}]'


def rate(x):
    return 'n/a' if x is None or x != x else f'{100 * x:.0f}%'


def num(x, fmt):
    return 'n/a' if x is None or x != x else format(x, fmt)


def contrast_line(label, c):
    return (f'| {label} | {c["pairs"]} | {c["n"]} | {pct(c["median_delta"])} | {ci(c["ci90_median_delta"])} | '
            f'{pct(c["mean_delta"])} | {rate(c["win_rate"])} |')


def design_questions(records, candidates, representative, s1, s2):
    dev = ['development']
    head = '| Contrast (to vs from) | Matched pairs | Paired episodes | Median Δ | 90% CI (quarter blocks) | Mean Δ | Win rate |\n|---|---|---|---|---|---|---|'
    q = {}

    def axis(family, name, frm, to):
        return pooled_contrast(records, matched_pairs(candidates, representative, family, name, frm, to), dev)
    winners = {f: s['winner'] for f, s in s1.items()}
    q[1] = ('What should the prediction target be?',
            [(f'{f} horizon 20 vs 10', axis(f, 'horizon', 10, 20)) for f in ('B', 'C', 'E')],
            'The `remaining`-horizon ablation was not run: score tables are shared across overlapping episodes '
            '(spec §3.2), so a per-episode remaining horizon has no single score per decision date.')
    q[2] = ('Exact return or relative rank?',
            [('C winner (P(top quintile)) vs B winner (E[r])', pooled_contrast(records, [(winners['C'], winners['B'])], dev)),
             ('E winner (direct utility) vs B winner (E[r])', pooled_contrast(records, [(winners['E'], winners['B'])], dev))], '')
    q[3] = ('Should the model output confidence?',
            [('B score P x confidence vs P', axis('B', 'score', 'point', 'point_conf'))], '')
    q[4] = ('Should multiple signals require agreement?',
            [('C score p x agreement vs p', axis('C', 'score', 'p', 'p_agree')),
             ('B winner (adaptive ensemble) vs A winner (fixed blend)', pooled_contrast(records, [(winners['B'], winners['A'])], dev))], '')
    q[5] = ('How much historical data should be used?',
            [('B weighting window 250 vs 60', axis('B', 'window', 60, 250)),
             ('C training window 750 vs 250', axis('C', 'train_window', 250, 750)),
             ('E training window 750 vs 250', axis('E', 'train_window', 250, 750))], '')
    sep, reg = [], []
    for f, r in sorted(s2.items()):
        for t in r['treatments']:
            label = f'{f}: {t["method"]}{" + regime" if t["regime"] else ""} vs plain winner'
            c = pooled_contrast(records, [(t['candidate'], r['winner'])], dev)
            (reg if t['method'] == 'topn_equal' else sep).append((label + (' (kept)' if t['kept'] else ''), c))
    q[6] = ('Should stock selection and weight allocation be separated?', sep, '')
    q[7] = ('Should market regime explicitly affect the portfolio?', reg,
            'Regime rule (predeclared in config/v5_grids.json): risk_off when D-1 breadth < 40% and D-1 market-vol '
            'percentile >= 70%; risk_off raises cash to 10% and cuts voluntary replacements to zero.')
    lines = []
    for number in range(1, 8):
        title, rows, note = q[number]
        lines += [f'### {number}. {title}', '', 'Development split, paired on both-complete episodes.', '', head]
        lines += [contrast_line(label, c) for label, c in rows]
        if note:
            lines += ['', note]
        lines.append('')
    return lines, {k: [(label, c) for label, c in v[1]] for k, v in q.items()}


def split_table(records, ids, split):
    frame = pd.DataFrame(records)
    summary = summarize(frame.loc[frame.split.eq(split) & frame.candidate.isin(ids)])
    lines = ['| Candidate | Family | Complete / attempted | PASS / attempted | Median | P25 | P10 | Worst | Mean | Max MDD | Turnover | Cost (NT$) | ODD_LOT-assumption episodes |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in summary.to_dict('records'):
        lines.append(f"| {r['candidate']} | {FAMILY_NAMES.get(r['family'], r['family'])} | {r['complete']}/{r['attempted']} | "
                     f"{r['measured']}/{r['attempted']} | {ret(r['median'])} | {ret(r['p25'])} | {ret(r['p10'])} | "
                     f"{ret(r['worst'])} | {ret(r['mean'])} | {ret(r['mdd_max'])} | "
                     f"{num(r['turnover'], '.2f')} | {num(r['cost'], ',.0f')} | {r['assumption_odd_lot_episodes']} |")
    return lines


def build(output):
    output = repo(output)
    verification = require_verification(output)
    freeze = read_json(output / 'freeze.json')
    records = pd.read_csv(output / 'all_episodes.csv').replace({np.nan: None}).to_dict('records')
    s1_all = read_json(output / 's1_selection.json')
    s1, representative = s1_all['selection'], s1_all['representative']
    s2 = read_json(output / 's2_selection.json')
    candidates = read_json(output / 'candidates.json')
    outcome, gates = final_gates(records, freeze)
    preferred = freeze.get('preferred')
    baseline = freeze['baseline_id']
    frozen_ids = [c['id'] for c in freeze['candidates'].values()]

    lines = ['# V5 Final Report', '',
             f'- **Outcome: `{outcome}`**',
             f'- Preferred candidate at freeze: `{preferred}`' if preferred else '- No candidate passed the pre-holdout gates at freeze',
             '- Submission status: `BLOCK_SUBMISSION` (live D-Plan path not certified)',
             f'- Evidence: `{rel(output)}` (verification `{verification["status"]}`, manifest sha256 `{verification["manifest_sha256"][:16]}…`)',
             '- Spec: `docs/v5_spec.md` (predeclared). Gates are applied exactly as written in §7.', '',
             '## §7 gates for the frozen family candidates', '',
             '| Candidate | Val feasible | V3 median Δ | P25 vs V3−1pp | P10 vs V3−1.5pp | Complexity earned | Stable | Val+holdout feasible | Holdout Δ ≥ −0.5pp | STABLE |',
             '|---|---|---|---|---|---|---|---|---|---|']
    yes = lambda b: 'yes' if b else 'no'
    for cid in frozen_ids:
        g, pre = gates[cid], freeze['gates'][cid]
        v3 = pre['v3']
        lines.append(f"| {cid}{' (preferred)' if cid == preferred else ''} | {yes(pre['feasible_validation'])} | "
                     f"{pct(v3['median_delta'])} (n={v3['n']}) | {yes(pre['v3_p25'])} | {yes(pre['v3_p10'])} | "
                     f"{yes(pre['complexity_earned'])} | {yes(pre['stable'])} | {yes(g['feasible'])} | "
                     f"{yes(g['holdout_not_reversed'])} ({pct(g['holdout']['median_delta'])}) | {yes(g['stable_candidate'])} |")
    lines += ['', 'Only the preferred candidate can become `STABLE_CANDIDATE`; it cannot be swapped after holdout.', '',
              '## Alpha vs compliance', '', '| Candidate | alpha_status | compliance_status |', '|---|---|---|']
    for cid in frozen_ids:
        g, pre = gates[cid], freeze['gates'][cid]
        alpha = 'PROMISING' if pre['v3_median'] and pre['v3_p25'] and pre['v3_p10'] and g['holdout_not_reversed'] else 'REJECTED'
        compliance = 'PASS_MEASURED' if g['feasible'] else 'FAIL_MEASURED'
        lines.append(f'| {cid} | {alpha} | {compliance} |')
    for split in ('development', 'validation', 'holdout_retrospective', 'diagnostic_seasonal', 'diagnostic_recent'):
        lines += ['', f'## Results — {split}', '']
        if split.startswith('diagnostic'):
            lines += ['Diagnostic only: never used to select a candidate.', '']
        lines += split_table(records, [*frozen_ids, baseline], split)
    lines += ['', '## Paired evidence vs A0_V3 (both-complete episodes, 90% CI by calendar-quarter block bootstrap)', '',
              '| Candidate | Split | n | Median Δ | 90% CI | P25 Δ | P10 Δ | Win rate |', '|---|---|---|---|---|---|---|---|']
    for cid in frozen_ids:
        for split in ('development', 'validation', 'holdout_retrospective'):
            p = paired_with_ci(records, cid, baseline, [split])
            lines.append(f"| {cid} | {split} | {p['n']} | {pct(p['median_delta'])} | {ci(p['ci90_median_delta'])} | "
                         f"{pct(p['p25_delta'])} | {pct(p['p10_delta'])} | "
                         f"{rate(p['win_rate'])} |")
    lines += ['', '## S1 winners and one-axis neighbourhoods (development)', '']
    for family, s in sorted(s1.items()):
        st = s['stability']
        lines += [f"- **{family} ({FAMILY_NAMES[family]})**: winner `{s['winner']}`; stability {'PASS' if st['passed'] else 'FAIL'} "
                  f"({st['distinct']} distinct neighbours)"]
        for n in st['neighbours']:
            lines.append(f"  - `{n['candidate']}` ({n['axis']} = {n['value']}): median {ret(n['median'])}, gap {pct(n['median_gap'])}, "
                         f"feasible {yes(n['feasible'])}{', behavioural duplicate' if n['duplicate'] else ''}")
    lines += ['', '## S2 portfolio-layer treatments (development, paired vs plain winner)', '',
              '| Family | Treatment | Method | Regime | n | Median Δ | P25 Δ | Kept |', '|---|---|---|---|---|---|---|---|']
    for family, r in sorted(s2.items()):
        for t in r['treatments']:
            p = t['paired']
            lines.append(f"| {family} | {t['candidate']} | {t['method']} | {yes(t['regime'])} | {p['n']} | "
                         f"{pct(p['median_delta'])} | {pct(p['p25_delta'])} | {yes(t['kept'])} |")
        lines.append(f"| {family} | **adopted** | `{r['adopted']}` | | | | | |")
    q_lines, q_data = design_questions(records, candidates, representative, s1, s2)
    lines += ['', '## Design questions (docs/champion.md §7)', '', *q_lines]
    duplicates = {k: v for k, v in representative.items() if k != v}
    if duplicates:
        lines += ['## Behavioural duplicates', '', 'Declared candidates that produced identical behaviour were run or '
                  'counted once (spec §1):', '', *[f'- `{k}` = `{v}`' for k, v in sorted(duplicates.items())], '']
    lines += ['## Known limitations', '', *[f'- {x}' for x in LIMITATIONS], '']
    report = ROOT / 'reports/v5_final.md'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('\n'.join(lines) + '\n')

    decision = dict(outcome=outcome, preferred=preferred, gates=gates, submission_status='BLOCK_SUBMISSION',
                    freeze_sha256=sha256(output / 'freeze.json'), manifest_sha256=verification['manifest_sha256'],
                    report=rel(report), design_questions={str(k): [dict(label=l, **c) for l, c in v] for k, v in q_data.items()})
    write_json(output / 'final_decision.json', decision)
    final = ROOT / 'configs/v5_final.json'
    if outcome == 'STABLE_CANDIDATE':
        config = next(c for c in freeze['candidates'].values() if c['id'] == preferred)
        write_json(final, dict(outcome=outcome, candidate=config, candidate_sha256=canonical(config),
                               submission_status='BLOCK_SUBMISSION', study=rel(output),
                               freeze_sha256=decision['freeze_sha256'], manifest_sha256=decision['manifest_sha256'],
                               grids='config/v5_grids.json', spec='docs/v5_spec.md'))
    elif final.exists():
        raise SystemExit('Outcome is NO_V5_WINNER but configs/v5_final.json exists from another run; remove it deliberately')
    return decision


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', default='outputs/v5/study')
    args = parser.parse_args(argv)
    decision = build(args.output)
    print(json.dumps(clean(dict(outcome=decision['outcome'], preferred=decision['preferred'], report=decision['report']))))


if __name__ == '__main__':
    main()
