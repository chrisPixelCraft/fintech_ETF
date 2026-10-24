"""New 24-session candidates (frozen; see PREREGISTRATION.md).

- RevenueMomentum (S1): sales momentum. Score = mean percentile rank of latest-month revenue YoY growth and
  the record-high flag (revenue >= prior 12-month max). Month m is usable from the 11th of month m+1.
  Portfolio layer identical to Mom20 (top 25, keep 35, 95%, 10% turnover gate, last-3 freeze).
- CrashGuard (S2): low-vol large-cap core with a momentum sleeve sized by D-1 market state
  (Daniel & Moskowitz 2016: momentum crashes follow market declines in high volatility).
  bear = 0050 120-session log return < 0; high_vol = 0050 20-session std > median of its last 250 values.
  Momentum slots k = 15 / 8 / 0 for 0 / 1 / 2 flags; the remaining 24 - k slots hold the lowest-vol
  large caps; 2330 at 23.5%; the 24 others share 71.5% equally.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pandas as pd

from competition import portfolio
from competition.rules import ROOT
from research.baselines import MomentumConfig, _eligible, momentum_score

sys.path.append(str(ROOT / 'legacy/ml'))
from hybrid.features import revenue_features  # noqa: E402

HERE = Path(__file__).resolve().parent
TSMC = '2330.TW'
TSMC_WEIGHT, OTHERS_WEIGHT, INVESTED = .235, .715, .95


def revenue_panels(calendar: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    """Point-in-time revenue frames on the backtest calendar; 2026-09 comes from the separate fetch."""
    universe = pd.read_csv(ROOT / 'data/reference/universe_competition_20260731.csv', dtype={'ticker': str})
    symbols = dict(zip(universe.ticker, universe.yahoo_symbol))
    base = pd.read_csv(ROOT / 'data/hybrid/revenue.csv', dtype={'ticker': str})
    extra = pd.read_csv(HERE / 'data/revenue_202609.csv', dtype={'ticker': str})
    assert set(extra.month) == {'2026-09'} and '2026-09' not in set(base.month)
    return revenue_features(pd.concat([base, extra], ignore_index=True), calendar, symbols)


def _rank(raw: pd.Series, names) -> pd.Series:
    return raw.reindex(names).replace([np.inf, -np.inf], np.nan).rank(pct=True)


def _targets(desired: dict, rules) -> dict:
    assert rules.min_positions <= len(desired) <= rules.max_positions
    caps = pd.Series({s: rules.cap(s) for s in desired})
    weights = portfolio.cap_fill(pd.Series(desired), INVESTED, caps)
    assert abs(weights.sum() - INVESTED) < 1e-12
    return weights.to_dict()


def _turnover(target: dict, current: dict) -> float:
    return .5 * sum(abs(target.get(s, 0.) - current.get(s, 0.)) for s in set(target) | set(current))


class RevenueMomentum:
    def __init__(self, calendar, rules, portfolio_config: dict):
        self.panels, self.rules = revenue_panels(calendar), rules
        self.p = portfolio.PortfolioConfig.from_dict(portfolio_config)
        self.log = []

    def scores(self, view) -> pd.Series:
        names = _eligible(view, 20)
        parts = [_rank(self.panels[k].loc[view.date], names) for k in ('rev_yoy', 'rev_record')]
        score = pd.concat(parts, axis=1).mean(axis=1, skipna=False)
        self.log.append(dict(date=str(view.date.date()), eligible=len(names), scored=int(score.notna().sum()),
                             max_release_age_days=float(self.panels['rev_days'].loc[view.date, names].max())))
        return score

    def decide(self, view, state):
        return portfolio.target_weights(self.scores(view), state.weights, state.sessions_remaining, self.rules, self.p)


def market_state(view) -> tuple[int, dict]:
    r = view.benchmark_ret
    assert r.iloc[-250:].notna().all() and r.index[-1] == view.date
    trailing = np.log1p(r.iloc[-120:]).sum()
    vol = r.rolling(20).std(ddof=1)
    bear, high_vol = bool(trailing < 0), bool(vol.iloc[-1] > vol.iloc[-250:].median())
    k = {0: 15, 1: 8, 2: 0}[bear + high_vol]
    return k, dict(bear=bear, high_vol=high_vol, ret120=float(trailing), vol20=float(vol.iloc[-1]),
                   vol20_median250=float(vol.iloc[-250:].median()), momentum_slots=k)


class CrashGuard:
    def __init__(self, rules):
        self.rules, self.lowvol, self.applied_k, self.log = rules, None, None, []

    def _lowvol_order(self, view) -> list[str]:
        """C3 ordering, fixed at the window start: top-50 by 60-session traded value, then ascending 60d std."""
        valid = view.valid.iloc[-60:]
        names = valid.columns[valid.all()]
        values = (view.close[names] * view.volume[names]).iloc[-60:]
        names = values.columns[np.isfinite(values).all() & values.gt(0).all()]
        pool = [s for s in portfolio.ranked(values[names].mean())[:50] if s != TSMC]
        rets = view.ret[pool].iloc[-60:]
        return portfolio.ranked(-rets.std(ddof=1).where(rets.notna().all()))

    def decide(self, view, state):
        if self.lowvol is None:
            self.lowvol = self._lowvol_order(view)
            assert len(self.lowvol) >= 24
        k, info = market_state(view)
        if state.sessions_remaining <= 3 and state.weights:
            self.log.append(dict(date=str(state.date.date()), action='freeze', **info))
            return dict(state.weights)
        core = self.lowvol[:24 - k]
        scores = momentum_score(view, MomentumConfig(window=20)).drop(labels=[TSMC, *core], errors='ignore')
        held = [s for s, w in state.weights.items() if w > 0 and s != TSMC and s not in core]
        sleeve = portfolio.select(scores, held, SimpleNamespace(n_holdings=k, keep_rank=k + 6)) if k else []
        assert len(sleeve) == k
        target = _targets({TSMC: TSMC_WEIGHT, **{s: OTHERS_WEIGHT / 24 for s in [*core, *sleeve]}}, self.rules)
        turnover = _turnover(target, state.weights)
        trade = not state.weights or k != self.applied_k or turnover >= .10
        self.log.append(dict(date=str(state.date.date()), action='trade' if trade else 'hold',
                             turnover=turnover, **info))
        if trade:
            self.applied_k = k
            return target
        return dict(state.weights)
