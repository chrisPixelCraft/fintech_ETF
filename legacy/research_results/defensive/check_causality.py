"""Independent non-vacuous future-corruption check for frozen LowVol D.

In-memory copies only. Actual target weights for every decision through a chosen
trade date must match, with both the market and strategy revenue panels poisoned
strictly after that date. Original market/panel objects are never modified.
"""
from dataclasses import fields

import numpy as np
import pandas as pd

from competition import backtest
from production import engine


def changed_cells(a, b):
    return int(((a != b) & ~(a.isna() & b.isna())).to_numpy().sum())


def check(market, rules, base, strategies, historical, recent):
    episodes = [next(e for e in historical if e.start.year == year) for year in (2019, 2023)]
    episodes.append(next(e for e in recent if e.episode_id == 'W5'))
    cases = []
    checked = 0
    for ep in episodes:
        decision_day = ep.sessions[6]
        poisoned_frames, items = {}, []
        for field in fields(market):
            value = getattr(market, field.name)
            if not isinstance(value, (pd.DataFrame, pd.Series)):
                continue
            poisoned = value.copy(deep=True)
            mask = poisoned.index > decision_day
            if value.dtypes.eq(bool).all() if isinstance(value, pd.DataFrame) else value.dtype == bool:
                poisoned.loc[mask] = ~value.loc[mask]
            elif field.name == 'ret':
                # Force symbol-specific future returns, without relying on data values.
                poisoned.loc[mask] = np.linspace(-.19, .31, value.shape[1])
            elif field.name == 'benchmark_ret':
                poisoned.loc[mask] = .125
            elif field.name == 'split':
                poisoned.loc[mask] = 1.05
            elif field.name == 'dividend':
                poisoned.loc[mask] = value.loc[mask].fillna(0.) + .25
            else:
                poisoned.loc[mask] = value.loc[mask] * 1.37 + .17
            assert value.loc[~mask].equals(poisoned.loc[~mask]), field.name
            count = changed_cells(value, poisoned)
            assert count > 0, (ep.episode_id, field.name, 'vacuous corruption')
            items.append(dict(item='market.' + field.name, cells_changed=count))
            poisoned_frames[field.name] = poisoned
        future_market = market.with_frames(**poisoned_frames)
        ordinary = strategies.LowVol(market.calendar, rules, market.close.columns)
        corrupt = strategies.LowVol(market.calendar, rules, market.close.columns)
        corrupt.rev = {name: value.copy(deep=True) for name, value in corrupt.rev.items()}
        for name, panel in corrupt.rev.items():
            original = ordinary.rev[name]
            mask = panel.index > decision_day
            if name == 'month':
                panel.loc[mask] = panel.loc[mask].apply(lambda s: s.map(lambda v: v + 120 if pd.notna(v) else v))
            elif name == 'high3':
                panel.loc[mask] = 1 - panel.loc[mask]
            else:
                panel.loc[mask] = panel.loc[mask] + 100
            assert original.loc[~mask].equals(panel.loc[~mask]), name
            count = changed_cells(original, panel)
            assert count > 0, (ep.episode_id, name, 'vacuous revenue corruption')
            items.append(dict(item='revenue.' + name, cells_changed=count))

        class HorizonComplete(Exception):
            pass

        class Recorder:
            def __init__(self, wrapped):
                self.wrapped, self.decisions, self.states = wrapped, [], []

            def decide(self, view, state):
                # Keep the original episode and sessions_remaining, but do not
                # execute poisoned future sessions outside the tested horizon.
                if state.date > decision_day:
                    raise HorizonComplete()
                target = dict(self.wrapped.decide(view, state))
                self.decisions.append((str(state.date.date()), str(view.date.date()), target))
                self.states.append(dict(date=str(state.date), asof=str(state.asof),
                                        holdings=state.holdings, cash=state.cash, nav=state.nav,
                                        weights=state.weights, remaining=state.sessions_remaining))
                return target

        left, right = Recorder(ordinary), Recorder(corrupt)
        for source, recorder in ((market, left), (future_market, right)):
            stopped = False
            try:
                backtest.run_episode(source, ep, recorder, rules, engine.policy_of(base),
                                     backtest.ExecutionConfig(**base['execution']))
            except HorizonComplete:
                stopped = True
            assert stopped, (ep.episode_id, 'did not reach the comparison horizon')
        a, b = left.decisions, right.decisions
        assert len(a) == len(b) == 7 and a == b, ep.episode_id
        assert left.states == right.states, ep.episode_id
        checked += len(a)
        cases.append(dict(window=ep.episode_id, decision_day=str(decision_day.date()), decisions_checked=len(a),
                          decisions_equal=True, portfolio_states_equal=True, items=items,
                          targets=[dict(date=d, asof=v, weights=w) for d,v,w in a],
                          stopped_before_future_execution=True))
    assert checked > 0 and len(cases) == 3
    return dict(passed=True, decisions_checked=checked, cases=cases, extra_backtests=6,
                corruption_boundary='strictly after day-7 trade date; all market frame fields and all revenue panels; in-memory copies',
                comparison='exact date/asof/target dictionaries and portfolio states through decision day; original episode/session counts retained; halt before first future decision; 7 actual decisions per case')


if __name__ == '__main__':
    import run
    run.check_frozen()
    run.initialize()
    historical, recent = run.windows()
    import json
    print(json.dumps(check(run.MARKET, run.RULES, run.BASE, run.STRATEGIES, historical, recent), indent=2))
