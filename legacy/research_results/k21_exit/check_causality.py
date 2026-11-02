"""Future market/flow corruption checks; writes nothing outside the runner's outputs."""
import numpy as np
import pandas as pd
from competition import backtest
from production import engine


class Recorder:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def decide(self, view, state):
        weights = self.inner.decide(view, state)
        self.calls.append((view.date, view.close.index[-1], state.date, dict(weights)))
        return weights


def corrupt_after(market, cutoff):
    rng = np.random.default_rng(1)
    future = market.calendar > cutoff
    frames = {}
    for name in ('open', 'high', 'low', 'close', 'volume', 'ret', 'official_vwap'):
        frame = getattr(market, name).copy()
        frame.loc[future] = frame.loc[future] * rng.uniform(.5, 1.5, frame.loc[future].shape)
        frames[name] = frame
    valid = market.valid.copy()
    valid.loc[future] = rng.uniform(size=valid.loc[future].shape) > .3
    bench = market.benchmark_ret.copy()
    bench.loc[future] = rng.normal(size=int(future.sum()))
    return market.with_frames(valid=valid, benchmark_ret=bench, **frames)


def check(market, rules, base, strategies, historical, recent):
    episodes = [next(e for e in historical if e.start.year == year) for year in (2019, 2023)] + [recent[-1]]
    records = []
    for ep in episodes:
        # As in tests/test_causality.py: corrupt after day 6's close, and compare
        # decisions through day 7 (whose only permitted information is day 6).
        k = 6
        cutoff = ep.sessions[k - 1]
        bad_market = corrupt_after(market, cutoff)
        assert not bad_market.close.loc[bad_market.calendar > cutoff].equals(
            market.close.loc[market.calendar > cutoff])
        for name, cls in [('K2.1', strategies.K21Exit), ('K2.S', strategies.K2Stop)]:
            original = cls(market.calendar, rules, market.close.columns)
            altered = cls(market.calendar, rules, market.close.columns)
            altered.trust = altered.trust.copy()
            future = altered.trust.index > cutoff
            rng = np.random.default_rng(2)
            altered.trust.loc[future] = rng.normal(0, 1e9, altered.trust.loc[future].shape)
            assert not altered.trust.loc[future].equals(original.trust.loc[future])
            # Also corrupt future revenue signal panels, without touching cached inputs.
            altered.rev = {key: value.copy() for key, value in altered.rev.items()}
            for key in ('yoy', 'high3'):
                mask = altered.rev[key].index > cutoff
                altered.rev[key].loc[mask] = rng.normal(size=altered.rev[key].loc[mask].shape)
            month = altered.rev['month']
            month.loc[month.index > cutoff] = pd.Period('2099-01', freq='M')
            clean_rec, bad_rec = Recorder(original), Recorder(altered)
            clean = backtest.run_episode(market, ep, clean_rec, rules, engine.policy_of(base),
                                         backtest.ExecutionConfig(**base['execution']))
            bad = backtest.run_episode(bad_market, ep, bad_rec, rules, engine.policy_of(base),
                                       backtest.ExecutionConfig(**base['execution']))
            assert len(clean_rec.calls) >= k + 1 and len(bad_rec.calls) >= k + 1
            for i in range(k + 1):
                view_date, last_row, day, weights = clean_rec.calls[i]
                assert view_date < day and view_date == last_row
                assert clean_rec.calls[i] == bad_rec.calls[i], (name, ep.episode_id, i)
            np.testing.assert_array_equal(clean['ledger'].nav.iloc[:k], bad['ledger'].nav.iloc[:k])
            clean_later = clean['ledger'].nav.iloc[k:].to_numpy()
            bad_later = bad['ledger'].nav.iloc[k:].to_numpy()
            changed = len(clean_later) != len(bad_later) or not np.allclose(clean_later, bad_later)
            assert changed, 'Corruption must change the later ledger'
            problems = backtest.verify_episode(market, ep, clean, rules)
            assert not problems, (name, ep.episode_id, problems)
            records.append(dict(strategy=name, window=ep.episode_id, cutoff=str(cutoff.date()),
                                decision_day=str(ep.sessions[k].date()), decisions_checked=k + 1,
                                unchanged=True, market_future_changed=True, flows_future_changed=True,
                                revenue_future_changed=True, later_ledger_changed=changed,
                                clean_verify_problems=len(problems)))
    checked = sum(r['decisions_checked'] for r in records)
    assert len(records) == 6 and checked > 0
    return dict(passed=True, cases=len(records), decisions_checked=checked,
                extra_backtest_runs=12, records=records)


if __name__ == '__main__':
    import run
    run.check_frozen()
    run.initialize()
    historical, recent = run.windows()
    import json
    print(json.dumps(check(run.MARKET, run.RULES, run.BASE, run.STRATEGIES, historical, recent), indent=2))
