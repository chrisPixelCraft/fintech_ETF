"""Frozen COLD_START_FALLBACK list (docs/production_spec.md section 6): Day 1 must build a legal book even
when the normal pipeline fails, so a 25-name equal-weight list is chosen and checked ahead of time.

    .venv/bin/python -m production.cold_start [--as-of YYYY-MM-DD]

Names: the 25 largest by average traded value (close x volume) over the last
60 sessions to --as-of, fully observed and tradable. The list is checked by
planning Day 1 from all cash at the as-of closes and validating the D-Plan.
Regenerate and freeze it again just before the contest.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

import pandas as pd

from competition.data import load_market
from competition.rules import ROOT, load_rules
from production import dplan, engine
from production.state import initial_book
from production.validate import validate

PATH = ROOT / 'production/cold_start.json'
N, WINDOW = 25, 60


def choose(market, as_of: pd.Timestamp) -> list[str]:
    view = market.asof(as_of)
    ok = view.valid.iloc[-WINDOW:].all()
    value = (view.close * view.volume).iloc[-WINDOW:].mean()[ok[ok].index]
    return sorted(value.sort_values(ascending=False).index[:N])


def check(market, symbols: list[str], as_of: pd.Timestamp, rules) -> dict:
    """Plan Day 1 with the fallback list at the as-of closes; the D-Plan must validate."""
    day = market.calendar[market.position(as_of) + 1] if as_of < market.calendar[-1] else as_of + pd.offsets.BDay()
    strategy = json.loads((ROOT / 'production/strategy.json').read_text())
    book = initial_book(rules, str(as_of.date()))
    inp = engine.DayInput(trade_date=pd.Timestamp(day), prev_date=as_of, market=market, book=book, strategy=strategy,
                          rules=rules, day_index=0, sessions_remaining=24, cold_start=tuple(symbols))
    close = engine.sizing_close(market, as_of, book)
    result = engine.fallback_path(inp, book, book.nav, close, ['COLD_START_CHECK'])
    plan = dplan.build(inp, result, dplan.Identity('TEAM_UNSET', 'cold-start-check'), datetime.now(dplan.TAIPEI))
    errors = validate(plan, holdings={}, close=close, nav=book.nav, cash=book.cash, rules=rules,
                      filename=dplan.filename(plan), allow_placeholder_team=True)
    return dict(mode=result.mode, plan_status=result.plan.status, orders=len(result.plan.orders), errors=errors,
                active_share=result.active_share['status'])


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--as-of', help='default: last session with data')
    args = parser.parse_args(argv)
    market, rules = load_market(), load_rules()
    as_of = pd.Timestamp(args.as_of) if args.as_of else market.close.dropna(how='all').index[-1]
    symbols = choose(market, as_of)
    report = check(market, symbols, as_of, rules)
    if report['mode'] != engine.COLD_START or report['errors']:
        raise SystemExit(f'Cold-start list fails its Day-1 check: {report}')
    frozen = dict(as_of=str(as_of.date()), method=f'top {N} by {WINDOW}-session average traded value, equal weight',
                  symbols=symbols, check=report)
    PATH.write_text(json.dumps(frozen, indent=1, ensure_ascii=False))
    print(json.dumps(frozen, ensure_ascii=False), flush=True)
    return frozen


def load() -> tuple[str, ...]:
    return tuple(json.loads(PATH.read_text())['symbols']) if PATH.exists() else ()


if __name__ == '__main__':
    main()
