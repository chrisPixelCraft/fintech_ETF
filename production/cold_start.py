"""Frozen COLD_START_FALLBACK list (docs/production_spec.md section 6): Day 1 must build a legal book even
when the normal pipeline fails, so a 25-name equal-weight list is chosen and checked ahead of time.

    .venv/bin/python -m production.cold_start --etf-holdings <etf_top10.csv> [--yahoo-dir <cache>] [--as-of D]

Names: the 25 largest by average traded value (close x volume) over the last
60 sessions to --as-of, fully observed and tradable, then repaired for Active
Share (largest caps overlap the active ETFs' top 10s): overlapping names are
dropped for the next by traded value until every ETF clears the internal 25%.
The list is checked by planning Day 1 from all cash at the as-of closes and
validating the D-Plan; it is frozen only with ETF data and an Active Share PASS.
Regenerate and freeze it again just before the contest.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from competition.rules import ROOT, load_rules
from production import active_share, dplan, engine, etf_holdings, market_data
from production.state import initial_book, symbol_to_ticker
from production.validate import validate

PATH = ROOT / 'production/cold_start.json'
N, WINDOW = 25, 60


def choose(market, as_of: pd.Timestamp, etfs: dict | None) -> tuple[list[str], list[str]]:
    """(names, dropped for Active Share)."""
    view = market.asof(as_of)
    ok = view.valid.iloc[-WINDOW:].all()
    value = (view.close * view.volume).iloc[-WINDOW:].mean()[ok[ok].index]

    def build(scores):
        top = scores.sort_values(ascending=False).index[:N]
        return {s: .95 / N for s in top}

    if not etfs:
        return sorted(build(value)), []
    weights, dropped = active_share.repair(value, build, etfs, symbol_to_ticker)
    return sorted(weights), dropped


def check(market, symbols: list[str], as_of: pd.Timestamp, rules, etfs: dict | None = None) -> dict:
    """Plan Day 1 with the fallback list at the as-of closes; the D-Plan must validate."""
    day = market.calendar[market.position(as_of) + 1] if as_of < market.calendar[-1] else as_of + pd.offsets.BDay()
    strategy = json.loads((ROOT / 'production/strategy.json').read_text())
    book = initial_book(rules, str(as_of.date()))
    inp = engine.DayInput(trade_date=pd.Timestamp(day), prev_date=as_of, market=market, book=book, strategy=strategy,
                          rules=rules, day_index=0, sessions_remaining=24, cold_start=tuple(symbols), etfs=etfs,
                          required_etfs=tuple(etf_holdings.required()))
    close = engine.sizing_close(market, as_of, book)
    result = engine.fallback_path(inp, book, book.nav, close, ['COLD_START_CHECK'])
    plan = dplan.build(inp, result, dplan.Identity('TEAM_UNSET', 'cold-start-check'), datetime.now(dplan.TAIPEI))
    errors = validate(plan, holdings={}, close=close, nav=book.nav, cash=book.cash, rules=rules,
                      filename=dplan.filename(plan), allow_placeholder_team=True)
    return dict(mode=result.mode, plan_status=result.plan.status, orders=len(result.plan.orders), errors=errors,
                active_share=result.active_share['status'], active_share_min=result.active_share.get('minimum'),
                closest_etf=result.active_share.get('closest_etf'))


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--as-of', help='default: last session with data')
    parser.add_argument('--etf-holdings', required=True, help='active ETF top-10 CSV (production.etf_holdings)')
    parser.add_argument('--yahoo-dir', help='fresh Yahoo cache (production.market_data.refresh_yahoo)')
    args = parser.parse_args(argv)
    market = market_data.load_live_market(Path(args.yahoo_dir) if args.yahoo_dir else None, None)
    rules = load_rules()
    as_of = pd.Timestamp(args.as_of) if args.as_of else market.close.dropna(how='all').index[-1]
    etfs = active_share.load_etf_holdings(args.etf_holdings)
    symbols, dropped = choose(market, as_of, etfs)
    report = check(market, symbols, as_of, rules, etfs)
    if report['mode'] != engine.COLD_START or report['errors'] or report['active_share'] != active_share.PASS:
        raise SystemExit(f'Cold-start list fails its Day-1 check: {report}')
    frozen = dict(as_of=str(as_of.date()), method=f'top {N} by {WINDOW}-session average traded value, equal weight, '
                                                   f'Active Share repaired to >= {active_share.INTERNAL_MIN:.0%}',
                  symbols=symbols, dropped_for_active_share=dropped,
                  etf_holdings_as_of=sorted({x.as_of for x in etfs.values()}), check=report)
    PATH.write_text(json.dumps(frozen, indent=1, ensure_ascii=False))
    print(json.dumps(frozen, ensure_ascii=False), flush=True)
    return frozen


def load() -> tuple[str, ...]:
    return tuple(json.loads(PATH.read_text())['symbols']) if PATH.exists() else ()


if __name__ == '__main__':
    main()
