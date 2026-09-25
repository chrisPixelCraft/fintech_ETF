"""Active ETF top-10 holdings for the Active Share check (docs/production_spec.md section 7, P-U3).

    .venv/bin/python -m production.etf_holdings --out production_runs/<T>/etf_top10.csv

Source: MoneyDJ ETF "持股狀況" page (Basic0007), which lists each fund's top
10 holdings as "name(code.TW)" with weight (%) and a 資料日期 stamp. It is a
data vendor, not the issuer's own disclosure, so the D-Plan cites it as
vendor. The date shown next to the top-10 table is kept as ``as_of``; the
Active Share check treats data older than MAX_AGE_DAYS as unverified.
Output CSV: etf, as_of, ticker, name, weight (fraction), source_url.
"""
from __future__ import annotations

import argparse
import io
import re
import time
from pathlib import Path

import pandas as pd
import requests

from competition.rules import ROOT

URL = 'https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid={etf}.TW'
LIST = ROOT / 'data/reference/tuning_2nd_active_etf_readiness.csv'
HEADERS = {'User-Agent': 'Mozilla/5.0 (fintech_ETF active share check)'}
CODE = re.compile(r'^(?P<name>.*?)\((?P<code>[^()]+)\)\s*$')
DATE = re.compile(r'資料日期：\s*(\d{4}/\d{2}/\d{2})')


def parse_top10(html: str, etf: str, url: str) -> pd.DataFrame:
    """The top-10 table and the 資料日期 printed just before it."""
    anchor = html.find('個股名稱')
    if anchor < 0:
        raise ValueError(f'{etf}: no top-10 table')
    dates = DATE.findall(html[:anchor])
    if not dates:
        raise ValueError(f'{etf}: no 資料日期 before the top-10 table')
    tables = [t for t in pd.read_html(io.StringIO(html)) if '個股名稱' in t.columns and '投資比例(%)' in t.columns]
    if len(tables) != 1:
        raise ValueError(f'{etf}: {len(tables)} top-10 tables')
    rows = []
    for _, r in tables[0].iterrows():
        match = CODE.match(str(r['個股名稱']).strip())
        if not match or not pd.notna(r['投資比例(%)']):
            continue
        code = match['code'].strip()
        ticker = code.split('.')[0] if code.endswith(('.TW', '.TWO')) else code
        rows.append(dict(etf=etf, as_of=dates[-1].replace('/', '-'), ticker=ticker,
                         name=match['name'].strip().rstrip('*'), weight=float(r['投資比例(%)']) / 100, source_url=url))
    if not rows:
        raise ValueError(f'{etf}: empty top-10 table')
    return pd.DataFrame(rows).head(10)


def fetch_all(etfs, pause: float = .5) -> tuple[pd.DataFrame, dict]:
    frames, failures = [], {}
    for etf in etfs:
        url = URL.format(etf=etf)
        try:
            response = requests.get(url, timeout=30, headers=HEADERS)
            response.raise_for_status()
            frames.append(parse_top10(response.content.decode('utf-8', errors='ignore'), etf, url))
        except Exception as error:                  # a missing ETF makes the check unverified, never a pass
            failures[etf] = repr(error)
        time.sleep(pause)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), failures


def required() -> list[str]:
    return list(pd.read_csv(LIST, dtype=str).ticker)


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    frame, failures = fetch_all(required())
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False)
    report = dict(etfs=int(frame.etf.nunique()) if len(frame) else 0, failures=failures,
                  as_of=sorted(frame.as_of.unique().tolist()) if len(frame) else [])
    print(report, flush=True)
    return report


if __name__ == '__main__':
    main()
