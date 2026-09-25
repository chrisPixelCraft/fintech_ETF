"""The single daily entry point (docs/production_spec.md section 11).

    .venv/bin/python -m production.run_daily --trade-date 2026-10-27 [--holdings export.json] [--etf-holdings f.csv]
    .venv/bin/python -m production.run_daily --trade-date 2026-09-24 --offline     # dry run on the local snapshot

1. T-1 = the session before T on the market calendar.
2. Data: refresh Yahoo through T-1, fetch official T-1 quotes (TWSE/TPEx),
   cross-check closes, size with the exchange close. Yahoo stale -> the
   official row is appended and the normal path is barred (fallback).
3. Shadow ledger: settle yesterday's submitted D-Plan through T-1.
4. Organizer holdings (source of truth) and active-ETF holdings if given.
5. engine.run_day -> D-Plan -> validate; an invalid normal plan falls back to
   FALLBACK_HOLD and is validated again.
6. Write production_runs/<T>/: the D-Plan (only when valid), audit.json,
   audit.md and status.txt (READY_TO_SUBMIT or EMERGENCY_REVIEW_REQUIRED).

Submitting is manual (P-U2): upload the D-Plan file from the run folder.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pandas as pd

from competition.backtest import ExecutionConfig
from competition.rules import ROOT, load_rules
from production import active_share, cold_start, dplan, engine, market_data
from production import etf_holdings as etf_holdings_module
from production.state import SettledBook, initial_book, load_official_holdings, settle_day, ticker_map
from production.validate import validate

SETTINGS = ROOT / 'production/settings.json'
STRATEGY = ROOT / 'production/strategy.json'
READY, BLOCKED = 'READY_TO_SUBMIT', 'EMERGENCY_REVIEW_REQUIRED'


def code_version() -> str:
    run = lambda *a: subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return f'git:{run("rev-parse", "--short=12", "HEAD")}' + ('-dirty' if run('status', '--porcelain') else '')


def contest_position(market, trade_date: pd.Timestamp, settings: dict) -> tuple[int, int]:
    """(day_index, sessions_remaining) of T counted on the market calendar from contest_start."""
    start = pd.Timestamp(settings['contest_start'])
    done = int(((market.calendar >= start) & (market.calendar < trade_date)).sum())
    return done, max(settings['contest_sessions'] - done, 1)


def load_market_for(trade_date: pd.Timestamp, run_dir: Path, offline: bool) -> tuple:
    """(market, prev_date, degraded reasons, data report)."""
    report, degraded = {}, []
    if offline:
        market = market_data.load_live_market(None, None)
        prev = market.calendar[market.calendar < trade_date][-1]
        return market, prev, degraded, dict(mode='offline snapshot')
    runs = run_dir.parent
    store = runs / 'official_daily.csv'
    candidate_prev = pd.bdate_range(end=trade_date - pd.Timedelta(days=1), periods=10)
    try:
        yahoo_dir = market_data.refresh_yahoo(trade_date)
        report['yahoo'] = str(yahoo_dir)
    except Exception as error:
        yahoo_dir, report['yahoo_error'] = None, repr(error)
        degraded.append('YAHOO_REFRESH_FAILED')
    report['official'] = market_data.update_official_store(store, candidate_prev, run_dir / 'raw')
    official_csv = market_data.combined_official(store, run_dir / 'official_combined.csv')
    market = market_data.load_live_market(yahoo_dir, official_csv)
    fetched = pd.read_csv(store) if store.exists() else pd.DataFrame(columns=['date', 'symbol', 'close'])
    official_days = sorted(pd.to_datetime(fetched.date).unique())
    known = sorted(set(market.calendar[market.calendar < trade_date]) | {d for d in official_days if d < trade_date})
    prev = pd.Timestamp(known[-1])
    official = fetched[pd.to_datetime(fetched.date) == prev]
    if prev not in market.calendar:
        degraded.append('YAHOO_STALE_OFFICIAL_ROW_USED')
        market = market_data.append_official_row(market, prev, official)
    check = market_data.cross_check(market, prev, official)
    report['cross_check'] = check
    degraded += check['problems']
    market = market_data.with_exchange_close(market, prev, official)
    return market, prev, degraded, report


def settled_book(runs: Path, market, prev: pd.Timestamp, strategy: dict, rules) -> tuple[SettledBook, list]:
    """Shadow ledger after the T-1 close: yesterday's submitted D-Plan is settled at T-1 prices."""
    path, notes = runs / 'state' / 'book.json', []
    book = SettledBook.load(path) if path.exists() else initial_book(rules, str(prev.date()))
    symbols = ticker_map()
    for day in market.calendar[(market.calendar > pd.Timestamp(book.date)) & (market.calendar <= prev)]:
        plans = sorted((runs / str(day.date())).glob('D-Plan_*.json'))
        submitted = json.loads(plans[-1].read_text()) if plans else {'orders': []}
        orders = {symbols[o['ticker']]: (o['shares'] if o['side'] == 'BUY' else -o['shares'])
                  for o in submitted['orders']}
        if not book.holdings and not orders:          # all cash and nothing traded: nothing to settle
            book = replace(book, date=str(day.date()))
            continue
        if not plans:
            notes.append(f'NO_DPLAN_FOUND_FOR:{day.date()}')
        book, record, _, _ = settle_day(book, day, orders, market, rules,
                                        ExecutionConfig(**strategy.get('execution', {})), engine.policy_of(strategy))
        if record['warning']:
            notes.append(f'LEDGER_WARNING:{day.date()}:{record["warning_reasons"]}')
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    return book, notes


def build_and_validate(inp, result, identity, started, rules) -> tuple[dict, list]:
    plan = dplan.build(inp, result, identity, started, etf_source_url=etf_holdings_module.URL.format(etf='00981A'))
    errors = validate(plan, holdings=result.holdings, close=result.prev_close, nav=result.nav, cash=inp.book.cash,
                      rules=rules, filename=dplan.filename(plan), allow_placeholder_team=identity.team_id == 'TEAM_UNSET')
    return plan, errors


def audit_md(audit: dict) -> str:
    lines = [f'# 每日執行報告：{audit["trade_date"]}', '', f'**{audit["status"]}**（模式 `{audit["mode"]}`）', '',
             f'- T−1：{audit["prev_date"]}',
             f'- 持股來源：{audit["state_source"]}；對帳：{audit["reconciliation"]}',
             f'- 委託 {audit["orders"]} 筆；Active Share：{audit["active_share"].get("status")}'
             f'（最低 {audit["active_share"].get("minimum")}）']
    if audit['reasons']:
        lines += ['- 原因：'] + [f'  - {r}' for r in audit['reasons'] if '\n' not in r]
    if audit['validation_errors']:
        lines += ['- 驗證錯誤：'] + [f'  - {e}' for e in audit['validation_errors']]
    if audit.get('dplan'):
        lines += [f'- 要上傳的檔案：`{audit["dplan"]}`']
    lines += ['', '未解決的外部依賴見 docs/production_spec.md 第 13 節。', '']
    return '\n'.join(lines)


def run(trade_date: str, holdings: str | None = None, etf_holdings: str | None = None, offline: bool = False,
        runs_dir: str | None = None) -> dict:
    started = datetime.now(dplan.TAIPEI)
    settings = json.loads(SETTINGS.read_text())
    strategy, rules = json.loads(STRATEGY.read_text()), load_rules()
    trade = pd.Timestamp(trade_date)
    runs = Path(runs_dir or ROOT / settings['runs_dir'])
    out = runs / str(trade.date())
    out.mkdir(parents=True, exist_ok=True)
    audit = dict(trade_date=str(trade.date()), started=dplan.stamp(started), code_version=code_version(),
                 status=BLOCKED, mode=engine.EMERGENCY, reasons=[], validation_errors=[], orders=0,
                 active_share={}, state_source='local', reconciliation='NOT_CHECKED', prev_date=None)
    try:
        try:
            market, prev, degraded, data_report = load_market_for(trade, out, offline)
        except Exception as error:                   # both sources down: fall back on the local snapshot
            market = market_data.load_live_market(None, None)
            prev = pd.Timestamp(trade - pd.offsets.BDay())
            degraded, data_report = [f'MARKET_DATA_FAILED:{error!r}'], dict(mode='local snapshot after failure')
        audit.update(prev_date=str(prev.date()), data=data_report)
        book, ledger_notes = settled_book(runs, market, prev, strategy, rules)
        official = None
        if holdings:
            try:
                official = load_official_holdings(holdings)
            except Exception as error:                  # unreadable truth: fall back, never trust the local book
                degraded.append(f'OFFICIAL_HOLDINGS_UNREADABLE:{error!r}')
        if etf_holdings is None and not offline:
            frame, failures = etf_holdings_module.fetch_all(etf_holdings_module.required())
            etf_holdings = out / 'etf_top10.csv'
            frame.to_csv(etf_holdings, index=False)
            audit['etf_fetch'] = dict(etfs=int(frame.etf.nunique()) if len(frame) else 0, failures=failures)
        etfs = active_share.load_etf_holdings(etf_holdings) if etf_holdings and Path(etf_holdings).stat().st_size \
            else None
        required = tuple(pd.read_csv(ROOT / settings['active_etf_list'], dtype=str).ticker)
        day_index, remaining = contest_position(market, trade, settings)
        inp = engine.DayInput(trade_date=trade, prev_date=prev, market=market, book=book, strategy=strategy,
                              rules=rules, day_index=day_index, sessions_remaining=remaining,
                              official_holdings=official, require_official=settings['require_official_holdings'],
                              etfs=etfs, required_etfs=required, cold_start=cold_start.load(), degraded=tuple(degraded))
        result = engine.run_day(inp)
        identity = dplan.Identity(settings['team_id'], audit['code_version'])
        audit.update(mode=result.mode, reasons=ledger_notes + result.reasons, active_share=result.active_share,
                     state_source='official' if official is not None else 'local',
                     reconciliation=result.reconciliation, data_gate=result.data_gate,
                     plan_status=result.plan.status, plan_audit=result.plan.audit,
                     book=dict(date=book.date, cash=book.cash, nav=book.nav, holdings=len(book.holdings)))
        if result.submittable:
            plan, errors = build_and_validate(inp, result, identity, started, rules)
            if errors and result.mode != engine.HOLD and book.holdings:
                audit['reasons'].append('NORMAL_DPLAN_INVALID:' + ';'.join(errors[:5]))
                result = engine.fallback_path(inp, book, result.nav, result.prev_close, audit['reasons'])
                audit['mode'] = result.mode
                plan, errors = build_and_validate(inp, result, identity, started, rules)
            audit['validation_errors'] = errors
            if not errors:
                path = dplan.write(plan, out)
                audit.update(status=READY, dplan=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT)
                             else str(path), orders=len(plan['orders']))
    except Exception as error:                               # never silent: the audit says what broke
        audit['reasons'].append(f'PIPELINE_ERROR:{error!r}')
        audit['traceback'] = traceback.format_exc()
    audit['finished'] = dplan.stamp(datetime.now(dplan.TAIPEI))
    (out / 'audit.json').write_text(json.dumps(audit, indent=1, ensure_ascii=False, default=str))
    (out / 'audit.md').write_text(audit_md(audit))
    (out / 'status.txt').write_text(audit['status'] + '\n')
    return audit


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--trade-date', required=True)
    parser.add_argument('--holdings', help='organizer holdings export (JSON or CSV)')
    parser.add_argument('--etf-holdings', help='active ETF top-10 CSV (etf, as_of, ticker, weight)')
    parser.add_argument('--offline', action='store_true', help='use the local snapshot, no network (dry run)')
    parser.add_argument('--runs-dir', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    audit = run(args.trade_date, args.holdings, args.etf_holdings, args.offline, args.runs_dir)
    print(f'{audit["status"]} mode={audit["mode"]} orders={audit["orders"]} -> {audit.get("dplan", "no D-Plan")}',
          flush=True)
    return audit


if __name__ == '__main__':
    main()
