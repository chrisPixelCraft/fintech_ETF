"""D-Plan (schema 4.0) from one day's decision, following the guide's chain
source -> observation -> market view -> inference -> decision -> order.

Declared target weights are the planner's representable weights, so the
official formula floor(w x NAV / close / 1000) x 1000 - held reproduces every
order exactly (C2). Posture and the cash band are computed from the orders at
T-1 closes (C12). Every held name appears once in decisions or
no_trade_decisions.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from competition.rules import UNIVERSE_PATH
from production import engine
from production.state import symbol_to_ticker

TAIPEI = ZoneInfo('Asia/Taipei')
HOLD_BAND = .01              # |net flow| <= 1% NAV is declared "hold" (server tolerance 2%)
CASH_BAND = .02              # declared cash range = estimate +/- 2% (inside [0, 0.25])
REGIME_BAND = .03            # 0050 20-session return beyond +/-3% -> risk_on / risk_off
MAX_INFERENCES, MAX_OBSERVATIONS, PREMISES = 60, 100, 10
TWSE_URL = 'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={d:%Y%m%d}&type=ALLBUT0999'
TPEX_URL = 'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={d:%Y/%m/%d}&id=&response=json'
YAHOO_URL = 'https://finance.yahoo.com/quote/0050.TW/history'
ORGANIZER_URL = 'https://esun-ai-challenge.tw/'


@dataclass(frozen=True)
class Identity:
    team_id: str
    code_version: str
    model_provider: str = 'other'
    model_version: str = 'rule-based production_mom20'


def stamp(moment: datetime | pd.Timestamp) -> str:
    return pd.Timestamp(moment).tz_convert(TAIPEI).strftime('%Y-%m-%dT%H:%M:%S+08:00') \
        if pd.Timestamp(moment).tzinfo else pd.Timestamp(moment).strftime('%Y-%m-%dT%H:%M:%S+08:00')


def names() -> dict:
    frame = pd.read_csv(UNIVERSE_PATH, dtype={'ticker': str})
    return dict(zip(frame.yahoo_symbol, zip(frame.ticker, frame.name, frame.market)))


def flows(orders: dict, close: pd.Series, holdings: dict, cash: float, nav: float) -> dict:
    """Net buy notional and post-order cash ratio at T-1 closes (C12 estimate)."""
    buy = sum(q * close[s] for s, q in orders.items() if q > 0)
    sell = sum(-q * close[s] for s, q in orders.items() if q < 0)
    return dict(buy=buy, sell=sell, net=buy - sell, cash_after=(cash - buy + sell) / nav if nav > 0 else math.inf)


def posture(orders: dict, close: pd.Series, holdings: dict, cash: float, nav: float) -> dict:
    f = flows(orders, close, holdings, cash, nav)
    intent = 'hold' if abs(f['net']) <= HOLD_BAND * nav else 'increase' if f['net'] > 0 else 'reduce'
    low = max(0., round(f['cash_after'] - CASH_BAND, 4))
    high = min(.25, round(f['cash_after'] + CASH_BAND, 4))
    return dict(net_exposure_intent=intent, target_cash_pct_range=[low, max(low, high)])


def action_of(held: float, order: int) -> str:
    if held <= 0:
        return 'BUY'
    if held + order <= 0:
        return 'SELL_ALL'
    return 'ADD' if order > 0 else 'TRIM'


class _Ids:
    def __init__(self):
        self.n = {}

    def next(self, prefix: str) -> str:
        self.n[prefix] = self.n.get(prefix, 0) + 1
        return f'{prefix}{self.n[prefix]}'


def _pct(x) -> float:
    return round(float(x) * 100, 4)


def build(inp: engine.DayInput, result: engine.DayResult, identity: Identity, started: datetime,
          etf_source_url: str | None = None) -> dict:
    """The D-Plan dict for ``result`` (validate with production.validate before writing)."""
    ids, info, prev = _Ids(), names(), inp.prev_date
    content = stamp(pd.Timestamp(prev.date()) + pd.Timedelta(hours=13, minutes=30))
    sources = {
        'twse': dict(source_id=ids.next('S'), authority='twse', url=TWSE_URL.format(d=prev), content_as_of=content,
                     name='TWSE 每日收盤行情'),
        'tpex': dict(source_id=ids.next('S'), authority='tpex', url=TPEX_URL.format(d=prev), content_as_of=content,
                     name='TPEx 上櫃股票行情'),
        'vendor': dict(source_id=ids.next('S'), authority='vendor', url=YAHOO_URL, content_as_of=content,
                       name='Yahoo Finance 日線（還原報酬用）'),
    }
    if inp.official_holdings is not None:
        sources['organizer'] = dict(source_id=ids.next('S'), authority='other', url=ORGANIZER_URL,
                                    content_as_of=stamp(pd.Timestamp(prev.date()) + pd.Timedelta(hours=19)),
                                    name='主辦方結算持股（庫存明細）')
    if result.active_share.get('minimum') is not None and etf_source_url:
        sources['etf'] = dict(source_id=ids.next('S'), authority='fininst', url=etf_source_url, content_as_of=content,
                              name='主動 ETF 前十大持股')
    sid = {k: v['source_id'] for k, v in sources.items()}

    view = inp.market.asof(prev)
    log_ret = np.log1p(view.ret.iloc[-20:])
    mom20 = np.expm1(log_ret.sum(min_count=20))
    bench20 = float(np.expm1(np.log1p(view.benchmark_ret.iloc[-20:]).sum()))
    observations = [dict(obs_id=ids.next('O'), source_ref=[sid['vendor']],
                         statement=f'0050 近 20 日還原報酬 {bench20:+.2%}；150 檔中 {int((mom20 > 0).sum())} 檔近 20 日上漲，'
                                   f'等權平均 {float(mom20.mean()):+.2%}',
                         values=dict(benchmark_0050_20d_pct=_pct(bench20), advancers_20d=int((mom20 > 0).sum()),
                                     universe_ew_20d_pct=_pct(mom20.mean())))]
    market_obs = [observations[0]['obs_id']]
    book_source = sid.get('organizer', sid['vendor'])
    cash_ratio = inp.book.cash / result.nav if result.nav > 0 else 0.
    observations.append(dict(obs_id=ids.next('O'), source_ref=[book_source],
                             statement=f'前一交易日結算持股 {len(result.holdings)} 檔，估計淨值 {result.nav:,.0f} 元，'
                                       f'現金比 {cash_ratio:.2%}',
                             values=dict(holdings_count=len(result.holdings), nav_twd=round(result.nav, 2),
                                         cash_ratio_pct=_pct(cash_ratio))))
    market_obs.append(observations[-1]['obs_id'])
    if 'etf' in sid:
        a = result.active_share
        observations.append(dict(obs_id=ids.next('O'), source_ref=[sid['etf']],
                                 statement=f'目標組合前 10 大與主動 ETF 前 10 大之 Active Share 最低 {a["minimum"]:.2%}'
                                           f'（最接近 {a["closest_etf"]}）',
                                 values=dict(min_active_share_pct=_pct(a['minimum']))))
        market_obs.append(observations[-1]['obs_id'])

    orders = result.plan.orders
    weights = result.plan.audit.get('dplan_target_weight', {})
    held = {s: q for s, q in result.holdings.items() if q > 0}
    involved = sorted(set(held) | set(orders))
    scores = result.scores
    rank = scores.rank(ascending=False, method='first') if scores is not None else pd.Series(dtype=float)
    stock_obs = {}
    for s in involved:
        ticker, name, market = info[s]
        r = mom20.get(s, np.nan)
        values = dict(prev_close_twd=round(float(result.prev_close[s]), 4))
        text = f'{ticker} {name} 前日收盤 {result.prev_close[s]:,.2f} 元'
        if np.isfinite(r):
            values['momentum_20d_pct'] = _pct(r)
            text += f'，近 20 日還原報酬 {r:+.2%}'
        if s in rank.index:
            values['momentum_rank'] = int(rank[s])
            text += f'，動能排名 {int(rank[s])}/{len(rank)}'
        ref = [sid['twse'] if market == 'TWSE' else sid['tpex'], sid['vendor']]
        stock_obs[s] = dict(obs_id=ids.next('O'), source_ref=ref, statement=text, values=values)
    observations += [stock_obs[s] for s in involved]

    regime = 'risk_on' if bench20 > REGIME_BAND else 'risk_off' if bench20 < -REGIME_BAND else 'neutral'
    post = posture(orders, result.prev_close, held, inp.book.cash, result.nav)
    mode_text = {engine.NORMAL: '依凍結規則執行', engine.HOLD: '今日沿用前日持股、不做 alpha 交易',
                 engine.REPAIR: '今日只做維持合規所需的最少交易', engine.COLD_START: '首日以凍結名單等權建倉'}[result.mode]
    market_view = dict(
        basis_refs=market_obs,
        logic=(f'本 ETF 以 20 日動能選股、等權持有前 25 檔、目標投入 95%，並以排名前 35 名為續抱區。'
               f'0050 近 20 日 {bench20:+.2%}，判定為 {regime}；{mode_text}，'
               f'委託後預估現金比 {flows(orders, result.prev_close, held, inp.book.cash, result.nav)["cash_after"]:.2%}。'),
        regime=regime, stance='neutral', posture=post,
        counter_evidence='動能可能在短期內反轉；規則固定，不依單日訊號改變配置' if result.mode == engine.NORMAL else None)

    inferences, inf_of = [], {}
    notes = '；'.join(r for r in result.reasons if '\n' not in r)[:200]
    for s in involved:
        ticker, name, _ = info[s]
        q, have = orders.get(s, 0), held.get(s, 0.)
        r = rank.get(s)
        where = f'動能排名第 {int(r)}' if r is not None and not pd.isna(r) else '不在可排名名單（近 20 日資料不完整）'
        if q:
            act = action_of(have, q)
            why = {'BUY': '進入前 25 名，依動能規則新建倉', 'ADD': '仍在續抱區，權重低於目標，依組合規則加碼',
                   'TRIM': '權重高於目標或接近上限，依組合規則減碼', 'SELL_ALL': '跌出前 35 名續抱區，依規則出清'}[act]
            if result.mode != engine.NORMAL:
                why = f'{mode_text}（{notes or result.mode}）'
            logic = f'{ticker} {name} {where}；{why}，目標權重 {weights.get(s, 0.):.2%}。'
        else:
            why = '仍在前 35 名續抱區或換手低於門檻，維持持股' if result.mode == engine.NORMAL else mode_text
            logic = f'{ticker} {name} {where}；{why}。'
        inferences.append(dict(inf_id=ids.next('I'), premise_refs=[stock_obs[s]['obs_id']], logic=logic,
                               counter_evidence=None))
        inf_of[s] = inferences[-1]['inf_id']
    if len(inferences) > MAX_INFERENCES or len(observations) > MAX_OBSERVATIONS:
        raise ValueError(f'{len(inferences)} inferences / {len(observations)} observations exceed schema limits')

    decisions, order_rows, dec_of = [], [], {}
    for s in sorted(orders):
        did = ids.next('D')
        dec_of[s] = did
        decisions.append(dict(decision_id=did, ticker=symbol_to_ticker(s), action=action_of(held.get(s, 0.), orders[s]),
                              target_weight=weights[s], inference_refs=[inf_of[s]]))
        order_rows.append(dict(ticker=symbol_to_ticker(s), side='BUY' if orders[s] > 0 else 'SELL',
                               shares=int(abs(orders[s])), decision_ref=did))
    no_trade = [dict(ticker=symbol_to_ticker(s), reason_refs=[inf_of[s]]) for s in sorted(held) if s not in orders]
    return {
        'schema_version': '4.0', 'doc_type': 'D-Plan', 'team_id': identity.team_id,
        'trade_date': str(inp.trade_date.date()),
        'sources': referenced_sources(list(sources.values()), observations),
        'observations': observations, 'market_view': market_view, 'inferences': inferences,
        'decisions': decisions, 'no_trade_decisions': no_trade, 'orders': order_rows,
        'agent_metadata': dict(model_provider=identity.model_provider, model_version=identity.model_version,
                               run_started_at=stamp(started), run_completed_at=stamp(datetime.now(TAIPEI)),
                               code_version=identity.code_version),
    }


def referenced_sources(sources: list, observations: list) -> list:
    """Keep only cited sources and renumber them S1.. without gaps, rewriting the citations."""
    cited = {r for o in observations for r in o['source_ref']}
    kept = [dict(x) for x in sources if x['source_id'] in cited]
    renumber = {x['source_id']: f'S{i}' for i, x in enumerate(kept, 1)}
    for x in kept:
        x['source_id'] = renumber[x['source_id']]
    for o in observations:
        o['source_ref'] = [renumber[r] for r in o['source_ref']]
    return kept


def filename(plan: dict) -> str:
    return f'D-Plan_{plan["team_id"]}_{plan["trade_date"]}.json'


def write(plan: dict, folder: Path | str) -> Path:
    path = Path(folder) / filename(plan)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    return path
