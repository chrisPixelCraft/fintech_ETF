"""End-to-end consistency gate (docs/production_spec.md section 8): production path vs canonical backtest.

    PYTHONHASHSEED=0 .venv/bin/python -m production.replay [--split holdout] [--episodes last|all]

For each historical 24-session window the production pipeline runs day by
day: T-1 data -> engine.run_day -> D-Plan -> validate -> orders read back from
the D-Plan JSON -> shadow-ledger settlement -> next day, with the organizer's
holdings simulated by the ledger. The same window also runs through
competition.backtest.run_episode. Per day the target weights, orders, cash,
holdings and NAV must match exactly (NAV/cash within TOL), every D-Plan must
validate, and every day must take the normal path; otherwise CONSISTENCY_FAIL.
Results: research/results/production_replay/.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

import numpy as np
import pandas as pd

from competition import backtest, episodes as ep
from competition.data import load_market
from competition.rules import ROOT, load_rules
from production import dplan, engine
from production.state import initial_book, settle_day, ticker_map
from production.validate import validate
from research.run_experiment import build_strategy

RESULTS = ROOT / 'research/results/production_replay'
STRATEGY = ROOT / 'production/strategy.json'
TOL = 1e-6                    # NAV / cash tolerance in TWD
IDENTITY = dplan.Identity(team_id='TEAM_UNSET', code_version='replay')


class Recorder:
    def __init__(self, inner):
        self.inner, self.targets = inner, {}

    def decide(self, view, state):
        weights = self.inner.decide(view, state)
        self.targets[str(state.date.date())] = dict(weights)
        return weights


def orders_from_dplan(plan: dict) -> dict:
    symbols = ticker_map()
    return {symbols[o['ticker']]: (o['shares'] if o['side'] == 'BUY' else -o['shares']) for o in plan['orders']}


def replay_episode(market, episode, strategy: dict, rules) -> list[dict]:
    execution = backtest.ExecutionConfig(**strategy.get('execution', {}))
    policy = engine.policy_of(strategy)
    recorder = Recorder(build_strategy(strategy, rules))
    canonical = backtest.run_episode(market, episode, recorder, rules, policy, execution)
    bt_ledger = canonical['ledger'].set_index('date')
    bt_orders = canonical['orders'].groupby('date').apply(
        lambda g: dict(zip(g.symbol, g.shares.astype(int))), include_groups=False).to_dict()
    bt_holdings = canonical['holdings'].groupby('date').apply(
        lambda g: dict(zip(g.symbol, g.shares)), include_groups=False).to_dict()
    book = initial_book(rules, str(episode.prior_session.date()))
    rows = []
    for i, day in enumerate(episode.sessions):
        key = str(day.date())
        if key not in bt_ledger.index:        # the backtest stops after a disqualification
            break
        prev = market.calendar[market.position(day) - 1]
        inp = engine.DayInput(trade_date=day, prev_date=prev, market=market, book=book, strategy=strategy,
                              rules=rules, day_index=i, sessions_remaining=len(episode.sessions) - i,
                              official_holdings=dict(book.holdings))
        result = engine.run_day(inp)
        plan = dplan.build(inp, result, IDENTITY, datetime.now(dplan.TAIPEI))
        errors = validate(plan, holdings=result.holdings, close=result.prev_close, nav=result.nav, cash=book.cash,
                          rules=rules, filename=dplan.filename(plan), allow_placeholder_team=True)
        orders = orders_from_dplan(plan)
        book, record, _, _ = settle_day(book, day, orders, market, rules, execution, policy)
        canon = bt_ledger.loc[key]
        target_match = result.target == recorder.targets.get(key)
        rows.append(dict(
            episode=episode.episode_id, date=key, mode=result.mode, validation_errors=';'.join(errors),
            target_match=target_match, orders_match=orders == bt_orders.get(key, {}),
            holdings_match=book.holdings == bt_holdings.get(key, {}),
            cash_diff=abs(book.cash - canon.cash), nav_diff=abs(record['nav'] - canon.nav),
            orders=len(orders), warning=bool(record['warning'])))
    return rows


def verdict(rows: list[dict]) -> dict:
    frame = pd.DataFrame(rows)
    checks = {
        'all days normal path': bool((frame['mode'] == engine.NORMAL).all()),
        'every D-Plan validates': bool((frame.validation_errors == '').all()),
        'target weights identical': bool(frame.target_match.all()),
        'orders identical': bool(frame.orders_match.all()),
        'holdings identical': bool(frame.holdings_match.all()),
        f'cash and NAV within {TOL}': bool((frame.cash_diff <= TOL).all() and (frame.nav_diff <= TOL).all()),
    }
    return dict(status='CONSISTENCY_PASS' if all(checks.values()) else 'CONSISTENCY_FAIL', checks=checks,
                days=len(frame), episodes=int(frame.episode.nunique()),
                max_nav_diff=float(frame.nav_diff.max()), max_cash_diff=float(frame.cash_diff.max()),
                failing_days=frame[~(frame.target_match & frame.orders_match & frame.holdings_match
                                     & (frame.validation_errors == '') & (frame['mode'] == engine.NORMAL))]
                .head(20).to_dict('records'))


def summary_md(result: dict, split: str) -> str:
    lines = ['# Production replay：一致性 gate', '',
             f'- 期間：{split}，{result["episodes"]} 個 24 日窗口、共 {result["days"]} 個交易日',
             '- 每天：T−1 資料 → engine → D-Plan → 驗證 → 從 D-Plan 讀回委託 → shadow ledger 結算',
             '- 同一窗口另跑 `competition.backtest.run_episode`，逐日比對', '',
             f'**{result["status"]}**', '']
    lines += [f'- {"✓" if ok else "✗"} {name}' for name, ok in result['checks'].items()]
    lines += ['', f'- NAV 最大差 {result["max_nav_diff"]:.3g} 元，現金最大差 {result["max_cash_diff"]:.3g} 元', '']
    if result['failing_days']:
        lines += ['## 不一致的日子（前 20）', '', '```json', json.dumps(result['failing_days'], ensure_ascii=False,
                                                                     indent=1, default=str), '```', '']
    return '\n'.join(lines)


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--split', default='holdout', choices=sorted(ep.SPLITS))
    parser.add_argument('--episodes', default='all', help='all, last, or N evenly spaced')
    args = parser.parse_args(argv)
    market, rules = load_market(), load_rules()
    strategy = json.loads(STRATEGY.read_text())
    data_end = market.close.dropna(how='all').index[-1]
    windows = ep.build_episodes(market.calendar, args.split, rules.episode_sessions,
                                tuple(strategy['episodes']['offsets']), data_end)
    chosen = windows[-1:] if args.episodes == 'last' else ep.select(windows, None if args.episodes == 'all'
                                                                     else int(args.episodes))
    rows = [r for e in chosen for r in replay_episode(market, e, strategy, rules)]
    result = verdict(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(RESULTS / f'days_{args.split}.csv', index=False)
    (RESULTS / f'summary_{args.split}.json').write_text(json.dumps(result, indent=1, default=str))
    (RESULTS / f'summary_{args.split}.md').write_text(summary_md(result, args.split))
    print(result['status'], json.dumps(result['checks'], ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    main()
