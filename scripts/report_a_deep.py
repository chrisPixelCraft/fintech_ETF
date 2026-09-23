"""Audited A-only development report; no strategy execution or selection changes.

Run after BOTH independent audits pass. Derived statistics are conditional on
the already selected development curves, never unseen-market certification.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
TRACKS = ('historical_pit', 'official_ex_post')
TRACK_NAMES = {'historical_pit': '歷史股票池（重建時點假設）',
               'official_ex_post': '正式名單事後情境（成分股前視）'}
EN_TRACKS = {'historical_pit': 'Reconstructed historical pool',
             'official_ex_post': 'Official 2026 pool | membership lookahead'}
MODELS = ('return_winner', 'risk_controlled', 'strict_winner', 'incumbent', 'v1_matched', '0050')
NAMES = dict(return_winner='報酬最佳', risk_controlled='回撤受限最佳',
             strict_winner='strict 最佳', incumbent='原 A 基準',
             v1_matched='固定 v1', **{'0050': '0050'})
COLORS = dict(return_winner='#007f86', risk_controlled='#e68a00', strict_winner='#7b4ab5',
              incumbent='#2366b0', v1_matched='#7f8790', **{'0050': '#bd425b'})
FIELDS = ('target_count', 'replacement_margin', 'max_replacements_per_day',
          'volatility_spike_ratio', 'one_day_chase_return', 'volume_low', 'volume_high',
          'four_hour_mode', 'return_short', 'return_long', 'ema_fast', 'ema_slow',
          'macd_fast', 'macd_slow', 'macd_signal', 'momentum_weight', 'long_return_fraction')
GROUPS = {'return_pair': ('return_short', 'return_long'), 'ema_pair': ('ema_fast', 'ema_slow'),
          'macd_tuple': ('macd_fast', 'macd_slow', 'macd_signal')}
AXIS_NAMES = {'target_count': '目標持股數', 'replacement_margin': '普通換股分差',
              'max_replacements_per_day': '普通換股上限', 'volatility_spike_ratio': '波動暴增倍數',
              'one_day_chase_return': '單日追高上限', 'volume_low': '相對量下限',
              'volume_high': '相對量上限', 'four_hour_mode': '4H 方向確認',
              'return_pair': '報酬短／長窗口', 'ema_pair': 'EMA 快／慢窗口',
              'macd_tuple': 'MACD 快／慢／訊號', 'momentum_weight': '動能總權重 m',
              'long_return_fraction': '長期報酬占動能 q'}
PERIODS = [('2025H1', '2025-01-01', '2025-06-30'),
           ('2025H2', '2025-07-01', '2025-12-31'),
           ('2026H1', '2026-01-01', '2026-06-30'),
           ('2026Q3_partial', '2026-07-01', '2026-09-21')]
BOOTSTRAP_SEED = 20260922


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class Sources:
    def __init__(self):
        self.hashes = {}

    def remember(self, path, expected=None):
        path = Path(path).resolve()
        actual = sha(path)
        if expected is not None:
            require(actual == expected, 'Audited input changed: ' + str(path))
        key = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        self.hashes[key] = actual
        return path

    def json(self, path):
        return json.loads(self.remember(path).read_text())

    def csv(self, path, **kwargs):
        return pd.read_csv(self.remember(path), **kwargs)


def gate(source_root, sources):
    """Fail before writing anything unless both complete audited tracks agree."""
    audits = {}
    for track in TRACKS:
        path = source_root / track / 'audit.json'
        require(path.exists(), 'Report blocked: missing independent audit for ' + track)
        audits[track] = sources.json(path)
        require(audits[track].get('status') == 'PASS', 'Report blocked: audit did not PASS for ' + track)
    studies = []
    for track, audit in audits.items():
        folder = source_root / track
        require(audit['track'] == track, 'Wrong audit identity')
        sources.remember(ROOT / 'scripts/audit_a_deep.py', audit['auditor_sha256'])
        for relative, digest in audit['artifact_hashes'].items():
            sources.remember(folder / relative, digest)
        prefix = sources.json(folder / 'physical_prefix_audit.json')
        require(prefix == audit.get('physical_prefix_audit'), 'Physical-prefix evidence differs from audit')
        require(prefix.get('status') in ('PASS', 'NOT_APPLICABLE_NO_ELIGIBLE_WINNER'),
                'Physical-prefix audit did not pass')
        if prefix['status'] == 'PASS':
            expected_cutoff = '2025-12-31' if track == 'historical_pit' else '2026-06-30'
            require(prefix['cutoff'] == expected_cutoff
                    and prefix['future_daily_and_four_hour_rows_physically_removed'] is True
                    and prefix['interpretation'] == 'TEMPORAL_INVARIANCE_NOT_UNSEEN_OR_PRIOR_KNOWN_SELECTION',
                    'Unexpected physical-prefix scope')
        manifest = sources.json(folder / 'manifest.json')
        require(manifest.get('outputs_complete') is True, 'Incomplete study: ' + track)
        require(manifest['selection_scope'] == 'EX_POST_DEVELOPMENT', 'Unexpected study interpretation')
        study = manifest['study']
        count = study['exploration_trials'] + study['local_trials_per_track']
        require(count == audit['trial_count'] == manifest['completed_trials'] == manifest['expected_trials'],
                'Count mismatch: ' + track)
        require(audit['unique_effective_trials'] == count, 'Effective candidate duplication')
        for relative, digest in manifest['hashes'].items():
            sources.remember(ROOT / relative, digest)
        # The auditor hashes each receipt; verify its transitive payload too.
        receipts = [p for p in audit['artifact_hashes'] if p.startswith('trials/') and p.endswith('/receipt.json')]
        require(len(receipts) == count, 'Incomplete audited receipts')
        for relative in receipts:
            receipt_path = folder / relative
            for filename, digest in sources.json(receipt_path).items():
                sources.remember(receipt_path.parent / filename, digest)
        studies.append(study)
    require(studies[0] == studies[1], 'Track study specifications differ')
    return studies[0], audits


def fmt_number(value):
    return f'{float(value):.6g}'


def axis_value(params, axis):
    if axis in GROUPS:
        return '/'.join(fmt_number(params[k]) for k in GROUPS[axis])
    value = params[axis]
    return value if isinstance(value, str) else fmt_number(value)


def allowed_values(study, axis, extended=False):
    values = list(study['spaces'].get(axis, ['strict', 'coverage_only']))
    if extended:
        values += study['refinement']['boundary_extensions'].get(axis, [])
    return values


def value_label(value):
    if isinstance(value, (tuple, list)):
        return '/'.join(fmt_number(v) for v in value)
    return value if isinstance(value, str) else fmt_number(value)


def finite(frame, columns, label):
    require(np.isfinite(frame[list(columns)].to_numpy(float)).all(), 'Nonfinite ' + label)


def segment(values):
    values = np.asarray(values, dtype=float)
    require(len(values) >= 2 and np.isfinite(values).all() and (values > 0).all(), 'Invalid segment NAV')
    return float(values[-1] / values[0] - 1), float(-(values / np.maximum.accumulate(values) - 1).min())


def periodic_rows(equity, initial, track, model):
    """Month/period start includes the immediately preceding close; no resets."""
    eq = equity.copy()
    eq['date'] = pd.to_datetime(eq.date)
    dates = eq.date
    monthly, periods = [], []
    for month, group in eq.groupby(dates.dt.to_period('M')):
        first, last = int(group.index[0]), int(group.index[-1])
        row = dict(track=track, model=model, month=str(month), sessions=len(group),
                   start=str(group.date.iloc[0].date()), end=str(group.date.iloc[-1].date()),
                   partial_month=str(month) == '2026-09')
        for column, prefix in [('economic_nav', 'economic'), ('nav', 'book')]:
            previous = initial if first == 0 else eq[column].iloc[first - 1]
            ret, mdd = segment(np.r_[previous, eq[column].iloc[first:last + 1]])
            row[prefix + '_return'], row[prefix + '_max_drawdown'] = ret, mdd
        monthly.append(row)
    for label, start, end in PERIODS:
        selected = eq[(dates >= start) & (dates <= end)]
        require(len(selected) > 0, 'Empty development period ' + label)
        first, last = int(selected.index[0]), int(selected.index[-1])
        previous = initial if first == 0 else eq.economic_nav.iloc[first - 1]
        ret, mdd = segment(np.r_[previous, eq.economic_nav.iloc[first:last + 1]])
        periods.append(dict(track=track, model=model, period=label, sessions=len(selected),
                            start=str(selected.date.iloc[0].date()), end=str(selected.date.iloc[-1].date()),
                            economic_return=ret, economic_max_drawdown=mdd,
                            interpretation='WITHIN_DEVELOPMENT_CONTINUOUS_LEDGER'))
    return monthly, periods


def bootstrap_pair(candidate, incumbent, draws=1000, block=20, seed=BOOTSTRAP_SEED):
    """Paired circular moving blocks of RETURNS; no strategy or selection rerun."""
    candidate, incumbent = np.asarray(candidate), np.asarray(incumbent)
    require(candidate.shape == incumbent.shape and candidate.ndim == 1, 'Unpaired bootstrap returns')
    n = len(candidate)
    require(n >= block and np.isfinite(candidate).all() and np.isfinite(incumbent).all(), 'Invalid bootstrap inputs')
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(draws, math.ceil(n / block)))
    indices = ((starts[:, :, None] + np.arange(block)) % n).reshape(draws, -1)[:, :n]
    delta = (candidate[indices] - incumbent[indices]).mean(axis=1) * 10000
    return dict(sessions=n, draws=draws, block_sessions=block, seed=seed,
                observed_mean_daily_excess_bps=float((candidate - incumbent).mean() * 10000),
                ci95_low_bps=float(np.quantile(delta, .025)), ci95_high_bps=float(np.quantile(delta, .975)),
                fraction_draws_positive=float((delta > 0).mean()),
                scope='CONDITIONAL_FIXED_SELECTED_CURVES_NOT_POST_SELECTION_ADJUSTED',
                method='PAIRED_CIRCULAR_MOVING_BLOCK_RETURN_RESAMPLING')


def winner_of(rows):
    eligible = rows[rows.measured_hard_breach_days.eq(0)]
    if eligible.empty:
        return None
    return eligible.sort_values(['economic_total_return', 'economic_max_drawdown', 'candidate_id'],
                                ascending=[False, True, True]).iloc[0]


def diagnostics(trials, declared, study, track, selected):
    axes = list(study['ofat'])
    indexed = trials.set_index('candidate_id')
    anchors = {t['anchor']: t['candidate_id'] for t in declared if t['phase'] == 'incumbent'}
    ofat = []
    for t in declared:
        if t['phase'] != 'ofat':
            continue
        row, base = indexed.loc[t['candidate_id']], indexed.loc[anchors[t['anchor']]]
        ofat.append(dict(track=track, candidate_id=t['candidate_id'], anchor=t['anchor'],
                         anchor_candidate_id=base.name, axis=t['varied'],
                         value=axis_value(t['params'], t['varied']),
                         anchor_value=axis_value(base, t['varied']),
                         conditional_active=t['varied'] != 'replacement_margin' or t['params']['max_replacements_per_day'] > 0,
                         return_delta_pp=100 * (row.economic_total_return - base.economic_total_return),
                         mdd_delta_pp=100 * (row.economic_max_drawdown - base.economic_max_drawdown),
                         costs_delta_ntd=float(row.costs - base.costs),
                         turnover_delta=float(row.turnover_two_way - base.turnover_two_way),
                         infeasible_days_delta=int(row.infeasible_executed_days - base.infeasible_executed_days),
                         hard_days=int(row.measured_hard_breach_days),
                         anchor_hard_days=int(base.measured_hard_breach_days),
                         both_zero_hard=row.measured_hard_breach_days == base.measured_hard_breach_days == 0))
    sensitivity = []
    for axis in axes:
        work = trials.copy()
        work['axis_value'] = [axis_value(row, axis) for _, row in work.iterrows()]
        work['conditional_active'] = ~(work.max_replacements_per_day.eq(0)) if axis == 'replacement_margin' else True
        for (value, active), group in work.groupby(['axis_value', 'conditional_active'], sort=False):
            eligible = group[group.measured_hard_breach_days.eq(0)]
            sensitivity.append(dict(track=track, axis=axis, value=value, conditional_active=bool(active),
                trials=len(group), eligible_trials=len(eligible),
                return_median=float(eligible.economic_total_return.median()) if len(eligible) else np.nan,
                return_min=float(eligible.economic_total_return.min()) if len(eligible) else np.nan,
                return_max=float(eligible.economic_total_return.max()) if len(eligible) else np.nan,
                mdd_median=float(eligible.economic_max_drawdown.median()) if len(eligible) else np.nan,
                costs_median_ntd=float(eligible.costs.median()) if len(eligible) else np.nan,
                turnover_median=float(eligible.turnover_two_way.median()) if len(eligible) else np.nan,
                infeasible_days_median=float(eligible.infeasible_executed_days.median()) if len(eligible) else np.nan,
                interpretation='MARGINAL_ASSOCIATION_CONFOUNDED_BY_JOINT_AND_ADAPTIVE_SEARCH'))
    pairs = []
    for pair_id, group in trials[trials.phase.eq('sobol')].groupby('pair_id'):
        require(len(group) == 2 and set(group.four_hour_mode) == {'strict', 'coverage_only'}, 'Unpaired Sobol trial')
        strict = group[group.four_hour_mode.eq('strict')].iloc[0]
        coverage = group[group.four_hour_mode.eq('coverage_only')].iloc[0]
        for field in FIELDS:
            if field != 'four_hour_mode':
                require(strict[field] == coverage[field], 'Confounded 4H pair: ' + field)
        pairs.append(dict(track=track, pair_id=pair_id, search_seed=int(strict.search_seed),
            strict_candidate=strict.candidate_id, coverage_candidate=coverage.candidate_id,
            strict_return=float(strict.economic_total_return), coverage_return=float(coverage.economic_total_return),
            return_delta_pp=100 * (coverage.economic_total_return - strict.economic_total_return),
            mdd_delta_pp=100 * (coverage.economic_max_drawdown - strict.economic_max_drawdown),
            strict_hard_days=int(strict.measured_hard_breach_days), coverage_hard_days=int(coverage.measured_hard_breach_days),
            both_zero_hard=strict.measured_hard_breach_days == coverage.measured_hard_breach_days == 0))
    seed_best = []
    for seed in study['search_seeds']:
        seed_rows = trials[trials.phase.eq('sobol') & trials.search_seed.eq(seed)]
        for mode in ['all', 'strict', 'coverage_only']:
            group = seed_rows if mode == 'all' else seed_rows[seed_rows.four_hour_mode.eq(mode)]
            win = winner_of(group)
            seed_best.append(dict(track=track, search_seed=seed, four_hour_mode=mode,
                trials=len(group), eligible_trials=int(group.measured_hard_breach_days.eq(0).sum()),
                candidate_id=None if win is None else win.candidate_id,
                economic_total_return=np.nan if win is None else win.economic_total_return,
                economic_max_drawdown=np.nan if win is None else win.economic_max_drawdown,
                **({field: win[field] for field in FIELDS if field != 'four_hour_mode'} if win is not None else {}),
                selected_four_hour_mode=None if win is None else win.four_hour_mode,
                interpretation='SEARCH_VARIANCE_TWO_SEEDS_NOT_RETURN_SAMPLING_VARIANCE'))
    boundaries = []
    for role, selection in selected.items():
        cid = selection['candidate_id']
        if cid is None:
            continue
        row = indexed.loc[cid]
        for axis in axes:
            value = axis_value(row, axis)
            values = [value_label(v) for v in allowed_values(study, axis, True)]
            active = axis != 'replacement_margin' or row.max_replacements_per_day > 0
            categorical = axis == 'four_hour_mode'
            edge = 'INACTIVE' if not active else 'CATEGORICAL' if categorical else 'LOWER' if value == values[0] else 'UPPER' if value == values[-1] else 'INTERIOR'
            values_explore = [value_label(v) for v in allowed_values(study, axis)]
            actually_tried = {axis_value(r, axis) for _, r in trials.iterrows()}
            boundaries.append(dict(track=track, model=role, candidate_id=cid, axis=axis, value=value,
                declared_lower=values[0], declared_upper=values[-1], edge=edge,
                extended_value=value not in values_explore, conditional_active=bool(active),
                declared_levels=len(values), observed_levels=len(actually_tried),
                declared_lower_observed=values[0] in actually_tried, declared_upper_observed=values[-1] in actually_tried,
                interpretation='FINITE_DECLARED_GRID_NOT_GLOBAL_OPTIMUM'))
    return {name: pd.DataFrame(rows) for name, rows in [('ofat_effects', ofat), ('axis_sensitivity', sensitivity),
            ('paired_4h', pairs), ('seed_best', seed_best), ('boundary_checks', boundaries)]}


def table_md(frame):
    def row(values):
        return '| ' + ' | '.join(str(v).replace('|', '\\|').replace('\n', ' ') for v in values) + ' |'
    return '\n'.join([row(frame.columns), row(['---'] * len(frame.columns)),
                      *[row(values) for values in frame.fillna('').astype(str).values]])


def pct(value):
    return '—' if pd.isna(value) else f'{value:.2%}'


def comparison_display(frame):
    return pd.DataFrame([{'模型': NAMES[r.model], '參數': '' if pd.isna(getattr(r, 'candidate_id', '')) else getattr(r, 'candidate_id', ''),
        '扣成本報酬': pct(r.economic_total_return), '應收 MDD': pct(r.economic_max_drawdown),
        '帳面 MDD': pct(r.max_drawdown), '硬性違規日': '不適用' if r.model == '0050' else int(r.measured_hard_breach_days),
        '雙向換手': f'{r.turnover_two_way:.2f}×', '費稅百萬': f'{r.costs / 1e6:.2f}',
        '交易天數': int(r.trade_days), '成交筆數': int(r.trade_count)} for r in frame.itertuples()])


def inventory(study):
    rows = []
    for axis in study['ofat']:
        rows.append(dict(axis=axis, name=AXIS_NAMES[axis],
            raw_fields=', '.join(GROUPS.get(axis, (axis,))),
            exploration_values=', '.join(value_label(v) for v in allowed_values(study, axis)),
            local_extensions=', '.join(value_label(v) for v in study['refinement']['boundary_extensions'].get(axis, [])) or '無',
            effective_when='普通換股上限 > 0' if axis == 'replacement_margin' else '所有候選',
            role='科學比較' if axis == 'four_hour_mode' else '條件配套' if axis == 'replacement_margin' else '可調配套'))
    return pd.DataFrame(rows)


def save_figure(fig, destination):
    fig.savefig(destination, dpi=160, bbox_inches='tight')
    plt.close(fig)


def figures(data, asset_root):
    track = data['track']; folder = asset_root / track; folder.mkdir(exist_ok=True)
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                        'figure.facecolor': 'white', 'axes.titleweight': 'semibold'})
    title = EN_TRACKS[track] + '\n2025-01-02 to 2026-09-21 | development evidence; September partial'
    nav = data['nav']; dates = pd.to_datetime(nav.date)
    fig, axes = plt.subplots(2, 1, figsize=(12.5, 8), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
    for model in data['models']:
        values = nav[model].to_numpy(float)
        axes[0].plot(dates, values / data['initial'], label=model, color=COLORS[model],
                     lw=2 if model == 'return_winner' else 1.4, linestyle='--' if model == 'incumbent' else '-')
        axes[1].plot(dates, 100 * (values / np.maximum.accumulate(values) - 1), color=COLORS[model], lw=1.2)
    axes[0].set_ylabel('Economic NAV / initial capital'); axes[1].set_ylabel('Drawdown (%)')
    axes[0].legend(ncol=3, frameon=False, loc='upper left')
    for ax in axes: ax.grid(alpha=.15)
    fig.suptitle(title, fontsize=13); fig.tight_layout(); save_figure(fig, folder / 'nav_drawdown.png')
    fig, axes = plt.subplots(1, 2, figsize=(14, 10.5))
    for ax, metric, label, cmap in [(axes[0], 'economic_return', 'Monthly economic return (%)', 'RdYlGn'),
                                   (axes[1], 'economic_max_drawdown', 'Within-month economic MDD (%)', 'YlOrRd')]:
        pivot = data['monthly'].pivot(index='month', columns='model', values=metric).reindex(columns=data['models']) * 100
        vals = pivot.to_numpy(); lim = float(np.max(abs(vals)))
        ax.imshow(vals, cmap=cmap, aspect='auto', vmin=-lim if metric == 'economic_return' else 0, vmax=lim)
        ax.set_xticks(range(len(pivot.columns)), pivot.columns, rotation=40, ha='right')
        ax.set_yticks(range(len(pivot)), [m + '*' if m == '2026-09' else m for m in pivot.index]); ax.set_title(label)
        for i in range(len(pivot)):
            for j in range(len(pivot.columns)):
                ax.text(j, i, f'{vals[i,j]:.1f}', ha='center', va='center', fontsize=8.5)
    fig.suptitle(title + '\n* September through 21st. Each month includes prior month-end NAV.', fontsize=12)
    fig.tight_layout(); save_figure(fig, folder / 'monthly_return_mdd.png')
    ofat = data['ofat_effects']; axes_order = list(data['study']['ofat'])
    fig, ax = plt.subplots(figsize=(12, 7))
    anchor_colors = {'anchor_p052': '#007f86', 'anchor_p049': '#c56c18'}
    for j, axis in enumerate(axes_order):
        for k, anchor in enumerate(anchor_colors):
            rows = ofat[(ofat.axis == axis) & (ofat.anchor == anchor)]
            for _, row in rows.iterrows():
                ax.scatter(row.return_delta_pp, j + (k - .5) * .24, marker='o' if row.both_zero_hard else 'x',
                           color=anchor_colors[anchor], alpha=.75, s=35)
    for anchor, color in anchor_colors.items(): ax.scatter([], [], color=color, label=anchor)
    ax.scatter([], [], marker='x', color='#555', label='at least one nonzero hard day')
    ax.axvline(0, color='#777', lw=1); ax.set_yticks(range(len(axes_order)), axes_order); ax.invert_yaxis()
    ax.set_xlabel('Net total return difference from its own fixed anchor (percentage points)')
    ax.legend(frameon=False, loc='lower right'); ax.grid(axis='x', alpha=.15)
    ax.set_title(title + '\nOFAT: one declared axis changed; separate anchors, no averaging across anchors', fontsize=12)
    save_figure(fig, folder / 'ofat_effects.png')
    fig, panels = plt.subplots(1, 3, figsize=(16, 7), sharey=True)
    for ax, field, scale, xlabel in [(panels[0], 'mdd_delta_pp', 1, 'MDD difference (pp)'),
            (panels[1], 'costs_delta_ntd', 1e-6, 'Cost difference (NTD million)'),
            (panels[2], 'infeasible_days_delta', 1, 'Infeasible-day difference')]:
        for j, axis in enumerate(axes_order):
            for k, anchor in enumerate(anchor_colors):
                rows = ofat[(ofat.axis == axis) & (ofat.anchor == anchor)]
                for _, row in rows.iterrows():
                    ax.scatter(row[field] * scale, j + (k - .5) * .24, marker='o' if row.both_zero_hard else 'x',
                               color=anchor_colors[anchor], alpha=.75, s=25)
        ax.axvline(0, color='#777', lw=1); ax.set_xlabel(xlabel); ax.grid(axis='x', alpha=.15)
    panels[0].set_yticks(range(len(axes_order)), axes_order); panels[0].invert_yaxis()
    fig.suptitle(title + '\nOFAT tradeoffs relative to own anchor | teal=p052, orange=p049', fontsize=12)
    fig.tight_layout(); save_figure(fig, folder / 'ofat_tradeoffs.png')
    sensitivity = data['axis_sensitivity']; fig, axes = plt.subplots(5, 3, figsize=(16, 18))
    for ax, axis in zip(axes.flat, axes_order):
        rows = sensitivity[(sensitivity.axis == axis) & sensitivity.conditional_active & sensitivity.eligible_trials.gt(0)]
        order = [value_label(v) for v in allowed_values(data['study'], axis, True)]
        rows = rows.assign(sort=rows.value.map(lambda v: order.index(v) if v in order else len(order))).sort_values('sort')
        x = np.arange(len(rows)); vals = rows.return_median.to_numpy(float) * 100
        ax.vlines(x, rows.return_min * 100, rows.return_max * 100, color='#bccbd5', lw=3)
        ax.scatter(x, vals, color='#087f8c', s=24)
        ax.set_xticks(x, rows.value, rotation=55, ha='right', fontsize=8)
        for i, (_, row) in enumerate(rows.iterrows()): ax.annotate(str(int(row.eligible_trials)), (i, row.return_max * 100), xytext=(0, 3), textcoords='offset points', ha='center', fontsize=7)
        ax.set_title(axis, fontsize=10); ax.grid(axis='y', alpha=.15); ax.set_ylabel('Net return (%)', fontsize=8)
    for ax in list(axes.flat)[len(axes_order):]: ax.set_visible(False)
    fig.suptitle(title + '\nJoint-search marginal associations, NOT isolated causal effects\nEligible trials only: point=median; range=min/max; labels=count. Inactive margins excluded.', fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, .94)); save_figure(fig, folder / 'axis_sensitivity.png')
    pairs = data['paired_4h']; fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for i, seed in enumerate(data['study']['search_seeds']):
        rows = pairs[pairs.search_seed.eq(seed)].reset_index(drop=True)
        axes[0].scatter(rows.return_delta_pp, rows.mdd_delta_pp, label=str(seed), alpha=.8, marker=['o', '^'][i])
        invalid = rows[~rows.both_zero_hard]
        axes[0].scatter(invalid.return_delta_pp, invalid.mdd_delta_pp, facecolors='none', edgecolors='#111', s=100)
        values = np.sort(rows.return_delta_pp)
        axes[1].step(values, np.arange(1, len(values) + 1) / len(values), where='post', label=str(seed))
    axes[0].set_xlabel('Coverage minus strict: return (pp)'); axes[0].set_ylabel('Coverage minus strict: MDD (pp)')
    axes[1].set_xlabel('Coverage minus strict: return (pp)'); axes[1].set_ylabel('Empirical cumulative fraction')
    for ax in axes: ax.axvline(0, color='#777', lw=1); ax.grid(alpha=.15); ax.legend(frameon=False)
    axes[0].axhline(0, color='#777', lw=1)
    fig.suptitle(title + '\nPaired 4H direction test | rings: at least one nonzero hard day', fontsize=12)
    fig.tight_layout(); save_figure(fig, folder / 'paired_4h.png')
    trials = data['trials']; fig, ax = plt.subplots(figsize=(11, 6))
    for phase, group in trials.groupby('phase'):
        valid = group[group.measured_hard_breach_days.eq(0)]
        ax.scatter(valid.economic_max_drawdown * 100, valid.economic_total_return * 100, label=phase, s=25, alpha=.65)
    invalid = trials[trials.measured_hard_breach_days.gt(0)]
    ax.scatter(invalid.economic_max_drawdown * 100, invalid.economic_total_return * 100, marker='x', color='#b9bec3', label='not zero-hard', s=22)
    for j, role in enumerate(('return_winner', 'risk_controlled', 'strict_winner', 'incumbent')):
        selected = data['comparison'][data['comparison'].model.eq(role)]
        if selected.empty: continue
        row = selected.iloc[0]
        ax.scatter(row.economic_max_drawdown * 100, row.economic_total_return * 100, color=COLORS[role], s=110, edgecolor='#111')
        ax.annotate(role + ' ' + str(row.candidate_id), (row.economic_max_drawdown * 100, row.economic_total_return * 100), xytext=(7, 5 + j * 11), textcoords='offset points', fontsize=8)
    ax.set_xlabel('Economic MDD (%)'); ax.set_ylabel('Net total return (%)'); ax.grid(alpha=.15)
    ax.legend(frameon=False, fontsize=9); ax.set_title(title + '\nSearch outcomes; only zero-measured-hard candidates eligible', fontsize=12)
    save_figure(fig, folder / 'search_outcomes.png')


def load_track(track, source_root, study, audit, sources):
    folder = source_root / track
    trials = sources.csv(folder / 'trial_summary.csv')
    require(len(trials) == audit['trial_count'] and trials.status.eq('COMPLETE').all()
            and not trials.candidate_id.duplicated().any(), 'Candidate summary not complete')
    finite(trials, ['economic_total_return', 'economic_max_drawdown', 'measured_hard_breach_days', *[k for k in FIELDS if k != 'four_hour_mode']], 'candidate metrics/parameters')
    refinement = sources.json(folder / 'refinement_candidates.json')
    declared = study['candidates'] + refinement['candidates']
    require(set(trials.candidate_id) == {t['candidate_id'] for t in declared}, 'Candidate identity mismatch')
    selections = sources.json(folder / 'selection.json')
    comparison = sources.csv(folder / 'comparison.csv')
    models = [m for m in MODELS if m in set(comparison.model)]
    absent = {role for role, value in selections.items() if value['candidate_id'] is None}
    require(set(comparison.model) == set(MODELS) - absent, 'Incomplete comparison role coverage')
    comparison = comparison.set_index('model').loc[models].reset_index()
    finite(comparison, ['economic_total_return', 'economic_max_drawdown'], 'comparison')
    nav = sources.csv(folder / 'nav_comparison.csv')
    require(set(nav.columns) == {'date', *models} and not nav.date.duplicated().any(), 'Malformed NAV table')
    initial = float(sources.json(folder / 'input_snapshot/config/strategy_v2.json')['initial_cash'])
    monthly, periods, bootstrap, equities, selected_parameters = [], [], [], {}, []
    for model in models:
        eq = sources.csv(folder / 'final' / model / 'equity.csv')
        require(len(eq) == 417 and eq.date.tolist() == nav.date.iloc[1:].tolist(), 'Final calendar mismatch')
        require(np.isclose(nav[model].iloc[0], initial), 'Missing initial NAV')
        require(np.allclose(nav[model].iloc[1:], eq.economic_nav, rtol=0, atol=1e-5), 'NAV plot differs from audited ledger')
        equities[model] = eq
        months, period = periodic_rows(eq, initial, track, model)
        require(len(months) == 21, 'Missing monthly intervals')
        monthly.extend(months); periods.extend(period)
        config = sources.json(folder / 'final' / model / 'config.json')
        if model in selections:
            selected_parameters.append(dict(track=track, model=model, candidate_id=selections[model]['candidate_id'], **config['tuning_params']))
    incumbent_nav = np.r_[initial, equities['incumbent'].economic_nav]
    incumbent = incumbent_nav[1:] / incumbent_nav[:-1] - 1
    for model in ('return_winner', 'risk_controlled', 'strict_winner'):
        if model not in equities: continue
        values = np.r_[initial, equities[model].economic_nav]
        candidate = values[1:] / values[:-1] - 1
        bootstrap.append(dict(track=track, model=model, candidate_id=selections[model]['candidate_id'],
                              **bootstrap_pair(candidate, incumbent)))
    diagnostics_data = diagnostics(trials, declared, study, track, selections)
    require(len(diagnostics_data['paired_4h']) == audit['paired_four_hour_comparisons'], 'Paired sample count mismatch')
    monthly_frame = pd.DataFrame(monthly)
    saved_monthly = sources.csv(folder / 'monthly_comparison.csv').set_index(['model', 'month'])
    recalculated = monthly_frame.set_index(['model', 'month'])
    require(set(saved_monthly.index) == set(recalculated.index), 'Monthly coverage differs from audited source')
    for metric in ('economic_return', 'economic_max_drawdown'):
        require(np.allclose(saved_monthly[metric], recalculated.loc[saved_monthly.index, metric], rtol=0, atol=1e-12),
                'Derived monthly metric differs from audited source: ' + metric)
    # Distinct effective settings need not imply distinct realized trade paths.
    signatures = {tuple(sources.hashes[str((folder / 'trials' / cid / f'{table}.csv').relative_to(ROOT))]
                        for table in ('orders', 'trades', 'equity')) for cid in trials.candidate_id}
    return dict(track=track, trials=trials.assign(track=track), comparison=comparison.assign(track=track),
                monthly=monthly_frame, periods=pd.DataFrame(periods), bootstrap=pd.DataFrame(bootstrap),
                selected_parameters=pd.DataFrame(selected_parameters), selections=selections, models=models,
                nav=nav, initial=initial, study=study, audit=audit, unique_realized_artifact_paths=len(signatures), **diagnostics_data)


class Document:
    """Render identical semantic sections to Markdown and standalone offline HTML."""
    def __init__(self):
        self.md, self.html = [], []

    def heading(self, text, level=2):
        self.md.append('#' * level + ' ' + text)
        self.html.append(f'<h{level}>' + html.escape(text) + f'</h{level}>')

    def paragraph(self, text, kind=''):
        self.md.append(text)
        self.html.append(f'<p class="{kind}">' + html.escape(text) + '</p>')

    def table(self, frame):
        self.md.append(table_md(frame))
        self.html.append('<div class="scroll">' + frame.fillna('').to_html(index=False, escape=True, border=0) + '</div>')

    def image(self, path, caption):
        self.md.append(f'![{caption}]({path})')
        self.html.append(f'<figure><a href="{path}"><img loading="lazy" src="{path}" alt="{html.escape(caption)}"></a><figcaption>{html.escape(caption)}</figcaption></figure>')

    def links(self, links):
        self.md.append(' · '.join(f'[{label}]({url})' for label, url in links))
        self.html.append('<p class="links">' + ' · '.join(f'<a href="{html.escape(url)}">{html.escape(label)}</a>' for label, url in links) + '</p>')


def render_report(study, data, assets_name, source_root):
    doc = Document()
    total = sum(len(d['trials']) for d in data.values())
    doc.heading('A 策略深入調參｜兩個股票池分開評估', 1)
    doc.paragraph(f'已完成 {total} 次正式候選回測：每軌 {study["exploration_trials"]} 組探索與 {study["local_trials_per_track"]} 組局部深化。兩軌均通過獨立帳務、選優、設定與來源稽核；這是已觀察開發期間的結果，不能當作未見樣本外驗證。', 'lead')
    doc.paragraph('回測涵蓋 2025-01-02 至 2026-09-21，共 417 個完整交易日，初始本金 10 億元。2026 年 9 月只計至 21 日；研究凍結時 9/22 仍在交易中，因此不混入未完成當日行情。所有主表使用扣成本、含應收股息的 economic NAV。')
    doc.paragraph('選擇順序是：全期零已量測硬性違規 → 最高扣成本報酬 → 較低回撤 → 候選 ID。回撤受限版另要求全期 MDD 不高於同軌原 A；strict 版另要求 4H 方向偏多。這些都是全期事後選擇，不是未來風險保證。', 'callout')
    doc.paragraph('Active Share 缺少可驗證的歷史 ETF 持倉，仍為 UNKNOWN／BLOCK_SUBMISSION。零已量測硬性違規不等於正式合規；本報告不解除正式送單限制。', 'warning')
    doc.links([('實驗規格', '../docs/a_deep_protocol.md'), ('資料與規則限制', '../docs/tuning_2nd_data_readiness.md'),
               ('全部候選 CSV', assets_name + '/all_trials.csv'), ('產物與來源 SHA-256', assets_name + '/provenance.json')])
    doc.heading('先分清楚兩個問題')
    doc.table(pd.DataFrame([{'路徑': TRACK_NAMES['historical_pit'], '成分股資訊': '固定 2024 年底 150 檔；known-at 為 12/31 19:30 重建假設', '可支持的解讀': '此假設下的歷史開發回測'},
                            {'路徑': TRACK_NAMES['official_ex_post'], '成分股資訊': '使用 2026 年 9 月公布的正式 150 檔，套回 2025 年', '可支持的解讀': '含成分股前視的事後情境'}]))
    doc.paragraph('兩軌分開選參，不以兩個股票池的勝負作方法因果比較。交易訊號與固定股數使用當時已完成資料、次日 VWAP 只供成交結算；這項時間順序不會消除全期選參偏誤、歷史資料修訂風險或正式名單的成分股前視。')
    doc.heading('參數清單與實際搜尋範圍')
    inv = inventory(study)
    doc.table(inv.rename(columns={'name': '參數', 'exploration_values': '探索取值', 'local_extensions': '局部延伸', 'effective_when': '生效條件'})[['參數', '探索取值', '局部延伸', '生效條件']])
    doc.paragraph('共 17 個平面設定欄位、13 個搜尋軸。報酬與 EMA 的兩個窗口、MACD 的三個窗口均以成組選项搜尋，沒有窮舉所有整數組合。普通換股上限為 0 時，換股分差不生效，有效維度降為 12 軸；風控退出與現金／權重修正仍可能交易。有效設定已去重，包括浮點數等價評分權重。')
    doc.paragraph('m 是動能總權重，q 是長期報酬占動能的比例：短期=m×(1−q)、長期=m×q、量能=(1−m)×0.4，MACD／趨勢／長期趨勢各=(1−m)×0.2。strict 與 coverage_only 使用相同 4H 資料與 50 根棒準備門檻，後者僅移除偏多方向要求；它不是完全不需 4H 資料。')
    doc.paragraph('新權重參數化可精確重播原 p052／p049 兩個錨點，但未完整包含舊 slow 與 balanced 評分家族的精確權重；本輪只搜尋宣告的新離散格點。Sobol 是 12 個配套軸，加上第 13 個 4H 成對組別，不能稱為 17 維 Sobol。')
    doc.paragraph('固定：20–30 檔、一般股 10%／台積電 25% 上限、現金非負且 <25%、現金 guard 12% 與 headroom 2%、價格預算 0.9／1.1、1,000 股整張、手續費 0.1425%、賣出稅 0.3%。日線長期 EMA100／200、200 日暖機與 4H EMA20／MACD12/26/9 不調整。A 不使用板塊或美股評分權重。')
    phases = pd.concat([d['trials'][['track', 'phase']] for d in data.values()]).groupby(['track', 'phase']).size().rename('次數').reset_index()
    doc.table(phases.rename(columns={'track': '路徑', 'phase': '階段'}))
    doc.paragraph('探索包含兩軌原 A 設定作錨點、單軸探測及兩個 seed 的配對 Sobol。每軌兩個 seed 各 32 對 strict／coverage_only；局部階段由同一開發期的合格結果挑父組，再調整 1–3 個鄰近軸。局部候選屬自適應事後搜尋，不能拿它們重排較早歷史，宣稱當時已選到。')
    doc.links([('17 欄設定與分組軸 CSV', assets_name + '/parameter_inventory.csv'), ('四個 A 最終版本參數 CSV', assets_name + '/selected_parameters.csv')])
    for track, d in data.items():
        doc.heading(TRACK_NAMES[track])
        comp = d['comparison']; audit = d['audit']; selected = d['selections']
        incumbent = comp[comp.model.eq('incumbent')].iloc[0]
        winner = comp[comp.model.eq('return_winner')]
        if len(winner):
            win = winner.iloc[0]
            doc.paragraph(f'報酬最佳為 {win.candidate_id}：扣成本報酬 {pct(win.economic_total_return)}，economic MDD {pct(win.economic_max_drawdown)}。相較同軌原 A，報酬差 {100 * (win.economic_total_return - incumbent.economic_total_return):+.2f} 個百分點、MDD 差 {100 * (win.economic_max_drawdown - incumbent.economic_max_drawdown):+.2f} 個百分點。這是本次有限搜尋中的最佳開發結果。', 'lead')
        else:
            doc.paragraph('此軌沒有零已量測硬性違規的合格報酬最佳，維持 NO_ELIGIBLE_WINNER。', 'warning')
        doc.paragraph(f'已獨立重建 {audit["trial_count"]} 組候選帳務，其中 {audit["zero_hard_eligible"]} 組全期零已量測硬性違規。最大獨立帳務重建誤差 {audit["max_all_trial_ledger_error"]:.3g} 元。')
        prefix = audit['physical_prefix_audit']
        if prefix['status'] == 'PASS':
            doc.paragraph(f'實際截斷資料驗證：對本軌報酬最佳 {prefix["candidate_id"]}，刪除 {prefix["cutoff"]} 之後的日線與 4H 資料、重建指標並重跑，通過 {prefix["sessions"]} 個交易日、{prefix["trades"]} 筆成交的比較。日期、股票、固定股數與持股數量一致；economic NAV 最大 CSV 數值誤差為 {prefix["max_economic_nav_csv_error"]:.3g} 元。')
            if track == 'official_ex_post':
                doc.paragraph('正式名單軌採 2026-06-30 截斷，因完整 150 檔中最晚上市者在 2026 年 4 月才出現；這保留原引擎的全名單歷史資料存在檢查。未把該軌縮成較少股票，也沒有宣稱它通過 2025 年底截斷。')
            doc.paragraph('這項檢查只支持固定已選設定的交易時序一致性，不代表所有候選都做過實際截斷重跑，也不能證明該最佳設定在截斷當時可知。截斷期末會結算應收股息，因此期末比較 economic NAV；更早的帳面餘額另外核對。全期選參與正式名單前視限制仍在。')
        else:
            doc.paragraph('沒有合格報酬最佳，因此實際截斷重跑不適用；不宣稱此項時序驗證通過。')
        doc.paragraph(f'按 orders／trades／equity 三份 CSV 的逐位元組雜湊，本軌有 {d["unique_realized_artifact_paths"]} 組不同的已實現檔案路徑。這個嚴格相等計數不把不同設定自動視為不同交易證據，也不把微小數值差異視為經濟上顯著差異。')
        doc.table(comparison_display(comp))
        doc.paragraph('表中 MDD 均以正數表示損失幅度。0050 是單一 ETF 比較基準，持股檔數等策略競賽規則不適用；固定 v1 沿用舊規劃器，與新 A 的差異混合了規則執行器與額外調參，不能歸因為訊號方法單獨勝出。')
        parameters = d['selected_parameters'].set_index('model')
        param_table = pd.DataFrame({'參數': list(AXIS_NAMES.values()), **{NAMES[role]: [axis_value(parameters.loc[role], axis) for axis in AXIS_NAMES] for role in parameters.index}})
        doc.table(param_table)
        doc.image(f'{assets_name}/{track}/nav_drawdown.png', '六方淨值與回撤；相同設定可重疊成相同曲線')
        doc.image(f'{assets_name}/{track}/monthly_return_mdd.png', '21 個月報酬與月內回撤；9 月為部分月份')
        doc.paragraph('月報酬以前一月底至當月底計；月內 MDD 的高點從前一月底 NAV 開始，不沿用更早的高點。帳面 NAV 在研究期末才收到股息現金，economic NAV 在除息時列入應收；全期末值相同，期中回撤與月報可能不同。本研究沒有每個月重新開帳。')
        constraints = []
        for r in comp[comp.model.ne('0050')].itertuples():
            constraints.append({'模型': NAMES[r.model], '持股範圍': f'{int(r.holdings_min)}–{int(r.holdings_max)}',
                '現金比例': pct(r.cash_ratio_min) + '–' + pct(r.cash_ratio_max),
                '不可行執行日': int(r.infeasible_executed_days), '價格界限警示': int(r.execution_price_bound_breaches),
                '超過日量10%筆數': int(r.trades_above_10pct_daily_volume),
                '超過日量100%筆數': int(r.trades_above_100pct_daily_volume)})
        doc.table(pd.DataFrame(constraints))
        doc.paragraph('現金 guard、普通換股、強制修正與股數取整會交互影響結果。不可行規劃／未成交與已實現硬性違規是不同欄位，不能互相抵銷。全額 VWAP 成交、無市場衝擊與價格上下界仍是模擬假設；超過全天成交量的交易無法當作可實際執行的績效。')
        doc.image(f'{assets_name}/{track}/search_outcomes.png', '全部候選的報酬與回撤；不合格候選仍保留')
        doc.image(f'{assets_name}/{track}/ofat_effects.png', '單軸探測相對自身錨點的效果')
        doc.image(f'{assets_name}/{track}/ofat_tradeoffs.png', '單軸探測的回撤、成本與不可行日代價')
        doc.paragraph('OFAT 每次只改一個宣告軸，回報與自身錨點的差值；不把兩個錨點混成一個平均基準。叉號含不合格候選，用來展示代價，不得列為可選模型。下圖聯合搜尋的分組中位數則有其他參數與自適應挑選混雜，只能描述關聯。')
        doc.image(f'{assets_name}/{track}/axis_sensitivity.png', '各軸的聯合搜尋關聯；點為中位數、線為範圍、數字為合格筆數')
        pairs = d['paired_4h']; pair_rows = []
        for seed, g in pairs.groupby('search_seed'):
            both = g[g.both_zero_hard]
            for scope, subset in [('全部配對', g), ('兩者均零硬性違規', both)]:
                pair_rows.append({'seed': int(seed), '範圍': scope, '對數': len(subset),
                    '報酬差中位pp': f'{subset.return_delta_pp.median():+.2f}' if len(subset) else '—',
                    '回撤差中位pp': f'{subset.mdd_delta_pp.median():+.2f}' if len(subset) else '—',
                    'coverage報酬較高比例': pct((subset.return_delta_pp > 0).mean()) if len(subset) else '—'})
        doc.table(pd.DataFrame(pair_rows))
        doc.image(f'{assets_name}/{track}/paired_4h.png', '差值方向為 coverage_only 減 strict；回撤差為正代表回撤變大')
        doc.paragraph('這個配對比較固定其餘設定，因而能辨識本資料／執行器內移除 4H 方向門檻的效果。全部配對與雙方合格子集同時列出，避免隱藏規則失敗；雙方合格子集仍是條件化分析。兩個 seed 是搜尋設計差異，不是同一市場的兩次獨立抽樣。')
        seed_table = d['seed_best'].copy()
        seed_table['economic_total_return'] = seed_table.economic_total_return.map(pct)
        seed_table['economic_max_drawdown'] = seed_table.economic_max_drawdown.map(pct)
        doc.table(seed_table[['search_seed', 'four_hour_mode', 'trials', 'eligible_trials', 'candidate_id', 'economic_total_return', 'economic_max_drawdown']].rename(columns={'search_seed':'seed', 'four_hour_mode':'4H範圍', 'trials':'次數', 'eligible_trials':'合格數', 'candidate_id':'最佳', 'economic_total_return':'報酬', 'economic_max_drawdown':'MDD'}))
        edges = d['boundary_checks']; hits = edges[edges.edge.isin(['LOWER', 'UPPER'])]
        doc.table(hits[['model', 'candidate_id', 'axis', 'value', 'edge', 'declared_lower', 'declared_upper']].rename(columns={'model':'模型', 'candidate_id':'參數', 'axis':'邊界軸', 'value':'值', 'edge':'位置', 'declared_lower':'下界', 'declared_upper':'上界'}))
        doc.paragraph('邊界表使用預先宣告的探索加局部延伸範圍；組合窗口按宣告順序排列，4H 類別不算數值邊界。選中邊界只代表尚不能排除更外側設定更好，不能當成全域最佳或任意擴大搜尋的理由；完整 CSV 亦記錄哪些宣告端點實際未被抽到。')
        period = d['periods'].copy(); period['economic_return'] = period.economic_return.map(pct); period['economic_max_drawdown'] = period.economic_max_drawdown.map(pct)
        doc.table(period[['period', 'model', 'sessions', 'economic_return', 'economic_max_drawdown']].rename(columns={'period':'開發段', 'model':'模型', 'sessions':'交易日', 'economic_return':'段報酬', 'economic_max_drawdown':'段內MDD'}))
        doc.paragraph('分段是同一連續持倉帳本的切片，每段起點用前一日 NAV，沒有重新初始化持股。這些日期都已參與選參，只能檢查表現是否集中於少數開發段，不能重新命名為樣本外或未見驗證。')
        bs = d['bootstrap'].copy()
        doc.table(pd.DataFrame([{'模型': NAMES[r.model], '每日平均超額bp': f'{r.observed_mean_daily_excess_bps:+.3f}',
             '條件式95%區間bp': f'[{r.ci95_low_bps:+.3f}, {r.ci95_high_bps:+.3f}]',
             '重抽樣差值>0比例': pct(r.fraction_draws_positive)} for r in bs.itertuples()]))
        doc.paragraph('上述為相對同軌原 A 的配對 circular moving-block bootstrap：20 交易日區塊、1,000 次、固定 seed 20260922，重抽每日報酬差；bp=0.01 個百分點。它只量化已選曲線在此重抽樣規則下的不確定性，沒有重跑交易或重新選參，沒有校正多次搜尋與事後選擇偏誤。正值比例不是 p 值；區間不代表未來報酬區間，也不證明顯著優勢。')
        relative = '../' + str((source_root / track).relative_to(ROOT))
        doc.links([('獨立帳務／來源稽核', relative + '/audit.json'), ('凍結執行清單', relative + '/manifest.json'),
                   ('實際截斷資料驗證', relative + '/physical_prefix_audit.json'),
                   ('事後選擇紀錄', relative + '/selection.json'), ('全期月表 CSV', assets_name + '/monthly.csv'),
                   ('單軸 CSV', assets_name + '/ofat_effects.csv'), ('配對 4H CSV', assets_name + '/paired_4h.csv')])
    doc.heading('全部候選與可追溯性')
    doc.paragraph(f'HTML 下方提供 {total} 筆候選的搜尋、股票池／階段／4H／合格條件篩選及點擊欄名排序。每筆保留完整 17 欄參數、父組、seed、規則結果、成本與交易量；CSV 保留全部原始摘要欄位。候選不因違規或績效不佳而從表中消失。')
    doc.links([(label, assets_name + '/' + filename) for label, filename in [
        ('完整候選 CSV', 'all_trials.csv'), ('六方比較 CSV', 'comparison.csv'), ('21 月完整表', 'monthly.csv'),
        ('開發分段 CSV', 'period_robustness.csv'), ('各軸關聯 CSV', 'axis_sensitivity.csv'),
        ('各 seed 最佳 CSV', 'seed_best.csv'), ('邊界 CSV', 'boundary_checks.csv'), ('區塊抽樣 CSV', 'bootstrap.csv')]])
    doc.heading('哪些結論仍不能成立')
    doc.paragraph('本輪只支持「在固定資料與規劃器、有限宣告搜尋中，這些候選的開發結果與已量測規則通過獨立重建」。它不支持正式參賽合規、可實盤複製、未來超額報酬、全域最佳或 A 方法必然優於 v1。反覆使用同一段歷史增加選擇偏誤；下一次有資訊價值的檢驗應保留未參與此次開發的新資料及固定採用規則。')
    doc.paragraph('本輪採用 Tuning Playbook 的變數分類、配對搜尋預算、單軸診斷與邊界檢查原則；該原則是實驗設計參考，不是台股交易有效性的證據。')
    doc.links([('Google Research Tuning Playbook', 'https://github.com/google-research/tuning_playbook'),
               ('參數與研究限制稽核', '../docs/a_deep_parameter_audit.md'),
               ('公開固定策略入口', '../v2_A_deep_best.py'),
               ('腳本', '../scripts/report_a_deep.py')])
    return doc


CSS = """
:root{font-family:Inter,-apple-system,BlinkMacSystemFont,"Noto Sans TC","PingFang TC",sans-serif;color:#1d2f3f;background:#f2f5f7;font-size:16px;line-height:1.7}
*{box-sizing:border-box}body{margin:0}main{max-width:1380px;margin:auto;background:#fff;padding:45px 54px}h1{font-size:2.15rem;line-height:1.35;max-width:900px}h2{margin-top:3rem;border-top:1px solid #d6e0e6;padding-top:1.6rem;font-size:1.5rem}p{max-width:1100px}a{color:#006d83}.lead{font-size:1.12rem;font-weight:600}.callout,.warning{padding:18px 22px;border-left:5px solid #007f86;background:#edf7f7}.warning{border-color:#bb711d;background:#fff6e7}.scroll{overflow:auto;margin:22px 0;border:1px solid #dbe3e9;border-radius:7px}table{border-collapse:collapse;width:100%;font-size:.87rem;line-height:1.5}th,td{padding:10px 12px;text-align:left;vertical-align:top;border-bottom:1px solid #e2e8ed}th{background:#eaf0f4;white-space:nowrap}tr:nth-child(even){background:#fafcfd}figure{margin:28px 0}figure img{width:100%;height:auto;border:1px solid #e4eaee}figcaption{color:#566775;font-size:.9rem}button,input,select{font:inherit;padding:8px 10px;border:1px solid #aebdc7;border-radius:5px;background:#fff}.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:center;padding:16px;background:#edf3f6;position:sticky;top:0;z-index:3}.filters input{min-width:260px;flex:1}#trialscroll{max-height:680px;overflow:auto}#trials th{position:sticky;top:0;z-index:1;cursor:pointer;user-select:none}#trials td{white-space:nowrap}#trials td:first-child,#trials th:first-child{position:sticky;left:0;background:#edf3f6;z-index:2}#trials th:first-child{z-index:4}#match-count{font-weight:600}footer{font-size:.85rem;color:#657887;padding-top:25px}.links{line-height:2.2}@media(max-width:800px){main{padding:24px 18px}h1{font-size:1.65rem}table{font-size:.78rem}th,td{padding:8px}h2{font-size:1.25rem}}@media print{main{max-width:none;padding:0}.filters{position:static}#trialscroll{max-height:none}figure,table{break-inside:avoid}h2{break-before:auto}}
"""


def interactive_table(trials):
    columns = ['track', 'candidate_id', 'phase', 'economic_total_return', 'economic_max_drawdown',
        'measured_hard_breach_days', 'trade_count', 'trade_days', 'turnover_two_way', 'costs',
        'infeasible_executed_days', 'cash_ratio_min', 'cash_ratio_max', 'anchor', 'parent', 'search_seed', 'pair_id', *FIELDS]
    labels = dict(track='股票池', candidate_id='候選', phase='階段', economic_total_return='報酬 %',
        economic_max_drawdown='MDD %', measured_hard_breach_days='硬性違規日', trade_count='成交筆數',
        trade_days='交易日', turnover_two_way='雙向換手', costs='費稅 NTD', infeasible_executed_days='不可行日',
        cash_ratio_min='最小現金 %', cash_ratio_max='最大現金 %')
    rows = []
    percent = {'economic_total_return', 'economic_max_drawdown', 'cash_ratio_min', 'cash_ratio_max'}
    for _, row in trials.iterrows():
        cells = []
        for col in columns:
            value = row[col] if col in row else ''
            if pd.isna(value): value = ''
            numeric = isinstance(value, (int, float, np.number))
            shown = f'{value * 100:.3f}' if col in percent else f'{value:,.0f}' if col == 'costs' else fmt_number(value) if numeric else str(value)
            cells.append(f'<td data-sort="{html.escape(str(value))}">{html.escape(shown)}</td>')
        attrs = ' '.join(f'data-{key}="{html.escape(str(value))}"' for key, value in {
            'track':row.track, 'phase':row.phase, 'mode':row.four_hour_mode,
            'eligible':'yes' if row.measured_hard_breach_days == 0 else 'no'}.items())
        rows.append(f'<tr {attrs}>' + ''.join(cells) + '</tr>')
    controls = '<h2>完整候選搜尋表</h2><div class="filters"><input id="search" aria-label="搜尋所有候選欄位" placeholder="搜尋候選、參數、seed…">'
    for key, label, values in [('track', '股票池', list(TRACKS)), ('phase', '階段', sorted(trials.phase.unique())),
                               ('mode', '4H 模式', ['strict', 'coverage_only']), ('eligible', '零已量測硬性違規', ['yes', 'no'])]:
        controls += f'<label>{label} <select id="filter-{key}"><option value="">全部</option>'
        controls += ''.join(f'<option value="{html.escape(v)}">{html.escape(v)}</option>' for v in values) + '</select></label>'
    controls += '<button id="reset" type="button">清除篩選</button><span id="match-count"></span></div>'
    header = ''.join(f'<th scope="col" tabindex="0" aria-sort="none">{html.escape(labels.get(c,c))}</th>' for c in columns)
    return controls + '<div class="scroll" id="trialscroll"><table id="trials"><thead><tr>' + header + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'


JS = """
const body=document.querySelector('#trials tbody'),rows=[...body.rows],search=document.querySelector('#search'),keys=['track','phase','mode','eligible'];
function filterRows(){const q=search.value.toLowerCase().trim();let n=0;rows.forEach(r=>{const match=(!q||r.textContent.toLowerCase().includes(q))&&keys.every(k=>{const v=document.querySelector('#filter-'+k).value;return !v||r.dataset[k]===v});r.hidden=!match;if(match)n++});document.querySelector('#match-count').textContent=n+' / '+rows.length+' 筆'}
search.addEventListener('input',filterRows);keys.forEach(k=>document.querySelector('#filter-'+k).addEventListener('change',filterRows));document.querySelector('#reset').addEventListener('click',()=>{search.value='';keys.forEach(k=>document.querySelector('#filter-'+k).value='');filterRows()});
[...document.querySelectorAll('#trials th')].forEach((th,i)=>{const sort=()=>{const asc=th.getAttribute('aria-sort')!=='ascending';document.querySelectorAll('#trials th').forEach(h=>h.setAttribute('aria-sort','none'));th.setAttribute('aria-sort',asc?'ascending':'descending');rows.sort((a,b)=>{const x=a.cells[i].dataset.sort,y=b.cells[i].dataset.sort;if(x===''||y==='')return x===''?(y===''?0:1):-1;const nx=Number(x),ny=Number(y);const delta=Number.isFinite(nx)&&Number.isFinite(ny)?nx-ny:x.localeCompare(y);return asc?delta:-delta}).forEach(r=>body.appendChild(r))};th.addEventListener('click',sort);th.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();sort()}})});filterRows();
"""


def run(source_root, report_base):
    sources = Sources()
    study, audits = gate(source_root, sources)
    data = {track: load_track(track, source_root, study, audits[track], sources) for track in TRACKS}
    assets = report_base.parent / (report_base.name.replace('_report', '') + '_assets')
    # All checks and reads above precede output creation: an untrusted partial run
    # cannot replace an existing report.
    report_base.parent.mkdir(parents=True, exist_ok=True); assets.mkdir(exist_ok=True)
    tables = {name: pd.concat([d[name] for d in data.values()], ignore_index=True) for name in
        ['trials', 'comparison', 'monthly', 'periods', 'bootstrap', 'selected_parameters',
         'ofat_effects', 'axis_sensitivity', 'paired_4h', 'seed_best', 'boundary_checks']}
    filenames = {'trials':'all_trials', 'periods':'period_robustness'}
    produced = []
    for name, frame in tables.items():
        path = assets / (filenames.get(name, name) + '.csv'); frame.to_csv(path, index=False); produced.append(path)
    path = assets / 'parameter_inventory.csv'; inventory(study).to_csv(path, index=False); produced.append(path)
    for d in data.values():
        figures(d, assets)
    produced.extend(assets.glob('*/*.png'))
    doc = render_report(study, data, assets.name, source_root)
    markdown_path = report_base.with_suffix('.md'); markdown_path.write_text('\n\n'.join(doc.md) + '\n')
    body = '\n'.join(doc.html) + interactive_table(tables['trials'])
    document = '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A 策略深入調參報告</title><style>' + CSS + '</style></head><body><main>' + body + '<footer>兩軌獨立稽核 PASS；開發期事後選參。Active Share UNKNOWN，正式送單仍封鎖。</footer></main><script>' + JS + '</script></body></html>'
    html_path = report_base.with_suffix('.html'); html_path.write_text(document)
    produced += [markdown_path, html_path]
    sources.remember(Path(__file__))
    provenance = dict(status='GENERATED_FROM_TWO_PASS_AUDITS', created_at=datetime.now(timezone.utc).isoformat(),
        script=str(Path(__file__).relative_to(ROOT)), command=sys.argv, python=platform.python_version(),
        numpy=np.__version__, pandas=pd.__version__, matplotlib=matplotlib.__version__,
        source_root=str(source_root.relative_to(ROOT)), study_id=study['study_id'],
        candidate_count=len(tables['trials']), count_by_track={t: len(d['trials']) for t,d in data.items()},
        comparison_rows=len(tables['comparison']), monthly_rows=len(tables['monthly']),
        unique_realized_artifact_paths_by_track={t:d['unique_realized_artifact_paths'] for t,d in data.items()},
        physical_prefix_audits={t:d['audit']['physical_prefix_audit'] for t,d in data.items()},
        bootstrap=dict(method='PAIRED_CIRCULAR_MOVING_BLOCK', draws=1000, block_sessions=20, seed=BOOTSTRAP_SEED,
                       estimand='mean_daily_candidate_minus_incumbent_return_in_basis_points',
                       post_selection_adjusted=False, strategy_rerun=False),
        interpretation='EX_POST_DEVELOPMENT_NOT_UNSEEN_TEST', formal_compliance='UNKNOWN_BLOCK_SUBMISSION',
        input_hashes=sources.hashes,
        output_hashes={str(p.relative_to(ROOT)):sha(p) for p in sorted(produced)})
    (assets / 'provenance.json').write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k:v for k,v in provenance.items() if k not in ('input_hashes','output_hashes')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT / 'outputs/a_deep_tuning')
    parser.add_argument('--report-base', type=Path, default=ROOT / 'reports/a_deep_tuning_report')
    args = parser.parse_args()
    source_root = args.source_root.resolve(); report_base = args.report_base.resolve()
    require(source_root.is_relative_to(ROOT) and report_base.is_relative_to(ROOT), 'Report paths must remain in this repository')
    run(source_root, report_base)
