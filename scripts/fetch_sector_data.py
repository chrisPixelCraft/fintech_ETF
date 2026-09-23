#!/usr/bin/env python3
"""Build dated official sector evidence; absent evidence stays UNKNOWN.

Existing equity data are read only.  Network mode is bounded and caches original
responses.  `--mode build` is offline and deterministic from those responses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/sector'
RAW = OUT / 'raw'
START, END = '2024-01-01', '2026-09-21'
UNIVERSE = ROOT / 'data/extended/processed/universe_20241231.csv'
DAILY = ROOT / 'data/extended/processed/daily_canonical.csv'
MEMBER_PAGE = 'https://www.tpex.org.tw/zh-tw/mainboard/trading/statistics/indices/constituents.html'
LISTING_SOURCES = [
    ('6446.TW', '2024-01-25', '2024-01-24', 'twse_listing_6446_20240124.pdf',
     'https://www.twse.com.tw/staticFiles/news/news/tsecnews/ff8080818d397607018d3ae24541002f.pdf'),
    ('6589.TWO', '2025-07-21', '2025-07-18', 'twse_listing_6589_20250718.pdf',
     'https://wwwc.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d697fc438f01981cd4dba30092.pdf'),
]
IDENTITY_SOURCE = 'https://www.taifex.com.tw/file/taifex/CHINESE/11/attach/5371_20260903.pdf'
TAXONOMY = {
    '01': '水泥工業', '02': '食品工業', '03': '塑膠工業', '04': '紡織纖維',
    '05': '電機機械', '06': '電器電纜', '07': '化學生技醫療', '08': '玻璃陶瓷',
    '09': '造紙工業', '10': '鋼鐵工業', '11': '橡膠工業', '12': '汽車工業',
    '13': '電子工業', '14': '建材營造', '15': '航運業', '16': '觀光餐旅',
    '17': '金融保險', '18': '貿易百貨', '19': '綜合', '20': '其他',
    '21': '化學工業', '22': '生技醫療業', '23': '油電燃氣業', '24': '半導體業',
    '25': '電腦及週邊設備業', '26': '光電業', '27': '通信網路業', '28': '電子零組件業',
    '29': '電子通路業', '30': '資訊服務業', '31': '其他電子業',
    '35': '綠能環保', '36': '數位雲端', '37': '運動休閒', '38': '居家生活',
    'TPEX_CULTURE': '文化創意業', 'TPEX_AGRI': '農業科技',
}
ALIASES = {'生技醫療': '生技醫療業', '金融保險業': '金融保險', '電子': '電子工業',
           '半導體': '半導體業', '電腦及週邊設備': '電腦及週邊設備業',
           '光電': '光電業', '通信網路': '通信網路業', '電子零組件': '電子零組件業',
           '電子通路': '電子通路業', '資訊服務': '資訊服務業', '其他電子': '其他電子業',
           '油電燃氣': '油電燃氣業'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean(value):
    return re.sub(r'\s+', '', BeautifulSoup(str(value), 'html.parser').get_text())


def sector(name, market):
    name = clean(name).replace('報酬指數', '').replace('類指數', '').replace('類', '')
    name = ALIASES.get(name, name)
    by_name = {v: k for k, v in TAXONOMY.items()}
    return f'{market}_{by_name[name]}' if name in by_name else None


def fetch(url, path):
    path = Path(path)
    if path.exists() and path.with_suffix(path.suffix + '.meta.json').exists():
        return
    response = requests.get(url, timeout=35)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    metadata = {'source_url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'sha256': sha(path), 'http_status': response.status_code}
    path.with_suffix(path.suffix + '.meta.json').write_text(json.dumps(metadata, indent=2))
    response.raise_for_status()
    if any(t in response.text[:5000] for t in ['Anti-DDoS', 'FOR SECURITY REASONS']):
        raise RuntimeError(f'Protection response; stop requests to this source: {url}')
    time.sleep(0.6)


def download():
    RAW.mkdir(parents=True, exist_ok=True)
    for month in pd.date_range('2024-01-01', '2026-09-01', freq='MS'):
        fetch('https://www.tpex.org.tw/www/zh-tw/indexInfo/idxsm?date=' + month.strftime('%Y/%m/01') + '&response=json',
              RAW / f'tpex_idxsm_{month:%Y%m}.json')
        print(f'indices {month:%Y-%m}', flush=True)
    for month in pd.date_range('2023-11-01', '2026-08-01', freq='MS'):
        roc = month.year - 1911
        fetch(f'https://www.tpex.org.tw/storage/ch/stock/statistics/idx/{roc}/IDX4_{roc}{month:%m}.xlsx',
              RAW / f'tpex_members_{month:%Y%m}.xlsx')
        print(f'membership {month:%Y-%m}', flush=True)
    for year, code, month in [(2024, '11302010341', '05'), (2025, '11402010541', '05'), (2026, '11502011171', '05')]:
        fetch(f'https://www.tpex.org.tw/storage/eb_data/{year-1911}{month}/{code}.html', RAW / f'tpex_reclass_{year}.html')
    fetch(MEMBER_PAGE, RAW / 'tpex_constituents_page.html')
    for _, _, _, name, url in LISTING_SOURCES:
        fetch(url, RAW / name)
    fetch(IDENTITY_SOURCE, RAW / 'taifex_5371_20260903.pdf')


def source(path, fallback):
    meta = Path(str(path) + '.meta.json')
    return json.loads(meta.read_text())['source_url'] if meta.exists() else fallback


def parse_indices(calendar):
    rows = []
    for path in sorted(RAW.glob('tpex_idxsm_*.json')):
        if '.meta.' in path.name:
            continue
        data = json.loads(path.read_text())
        ym = path.stem[-6:]
        if data.get('date') != ym + '01':
            raise ValueError(f'Unexpected source month: {path}')
        url = source(path, f'https://www.tpex.org.tw/www/zh-tw/indexInfo/idxsm?date={ym[:4]}/{ym[4:]}/01&response=json')
        for table in data.get('tables', []):
            kind = 'TOTAL_RETURN' if '報酬' in table.get('title', '') else 'PRICE'
            for values in table.get('data', []):
                roc = str(values[0]).replace('/', '')
                date = f'{int(roc[:3])+1911}-{roc[3:5]}-{roc[5:7]}'
                if date not in calendar:
                    continue
                for name, val in zip(table['fields'][1:], values[1:]):
                    sid = sector(name, 'TPEX')
                    if sid and val not in ['', '--', '---']:
                        rows.append(dict(date=date, sector_id=sid, sector_name=clean(name).replace('報酬指數', ''),
                                         market='TPEX', close=float(str(val).replace(',', '')), index_type=kind,
                                         available_at=date+'T19:30:00+08:00', source_url=url, sha256=sha(path),
                                         status='OFFICIAL_HISTORICAL_INDEX', available_at_policy='conservative_after_close_bound'))
    paths = list((ROOT/'data/raw/official').glob('*_TWSE.json'))
    paths += list((ROOT/'data/v2/raw_execution').glob('*_TWSE.json'))
    paths += list((ROOT/'data/extended/raw/universe').glob('twse_prices_*.json'))
    for path in sorted(paths):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        dt = data.get('date', '')
        if not re.fullmatch(r'20\d{6}', dt):
            continue
        date = f'{dt[:4]}-{dt[4:6]}-{dt[6:8]}'
        if date not in calendar:
            continue
        for table in data.get('tables', []):
            title = table.get('title', '')
            if '指數(臺灣證券交易所)' not in title:
                continue
            kind = 'TOTAL_RETURN' if '報酬' in title else 'PRICE'
            for vals in table.get('data', []):
                sid = sector(vals[0], 'TWSE')
                if sid:
                    rows.append(dict(date=date, sector_id=sid, sector_name=clean(vals[0]), market='TWSE',
                                     close=float(str(vals[1]).replace(',', '')), index_type=kind,
                                     available_at=date+'T19:30:00+08:00',
                                     source_url=f'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={dt}&type=ALLBUT0999',
                                     sha256=sha(path), status='OFFICIAL_HISTORICAL_INDEX',
                                     available_at_policy='conservative_after_close_bound'))
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    keys = ['date', 'sector_id', 'index_type']
    conflicts = frame.groupby(keys).close.nunique()
    if (conflicts > 1).any():
        raise ValueError('Conflicting official index revisions')
    frame = frame.drop_duplicates(keys).sort_values(keys).reset_index(drop=True)
    frame['is_leaf'] = ~frame.sector_id.str.endswith(('_07', '_13'))
    return frame


def membership_evidence(universe):
    observations = []
    symbol_by_code = dict(zip(universe.code.astype(str), universe.symbol))
    for path in sorted(RAW.glob('tpex_members_*.xlsx')):
        ym = path.stem[-6:]
        month = pd.Timestamp(f'{ym[:4]}-{ym[4:]}-01')
        # Next-month end is deliberately later than the published seventh-business-day schedule.
        known = (month + pd.offsets.MonthEnd(2)).strftime('%Y-%m-%d')
        url = source(path, f'https://www.tpex.org.tw/storage/ch/stock/statistics/idx/{month.year-1911}/IDX4_{month.year-1911}{month:%m}.xlsx')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            sheet = pd.read_excel(path, header=None)
        for values in sheet.itertuples(index=False, name=None):
            if len(values) < 3:
                continue
            code = str(values[1]).strip().removesuffix('.0')
            sid = sector(values[0], 'TPEX')
            if code in symbol_by_code and sid:
                observations.append(dict(symbol=symbol_by_code[code], market='TPEX', sector_id=sid,
                    sector_name=clean(values[0]), effective_anchor=month.strftime('%Y-%m-%d'),
                    known_at=known+'T23:59:59+08:00', source_url=url, sha256=sha(path),
                    status='ASSUMED_PUBLICATION_BOUND', known_at_policy='historical_monthly_file_next_month_end_bound',
                    source_as_of=month.strftime('%Y-%m-%d'), taxonomy_version='official_market_industry_2023'))
    # Actual effective dates from dated annual exchange notices override lagged snapshots.
    for year, effective in [(2024, '2024-06-03'), (2025, '2025-06-02'), (2026, '2026-06-01')]:
        path = RAW / f'tpex_reclass_{year}.html'
        if not path.exists():
            continue
        text = BeautifulSoup(path.read_bytes(), 'html.parser').get_text(' ', strip=True)
        pub = re.search(r'發文日期.*?中華民國\s*(\d+)年\s*(\d+)月\s*(\d+)日', text)
        if not pub:
            raise ValueError(f'No dated classification notice {path}')
        known = f'{int(pub[1])+1911}-{int(pub[2]):02d}-{int(pub[3]):02d}T23:59:59+08:00'
        for match in re.finditer(r'股票代號[：:]\s*(\d{4}).*?由「([^」]+)」調整為「([^」]+)」', text):
            code, old, new = match.groups()
            if code not in symbol_by_code:
                continue
            sid = sector(new, 'TPEX')
            if not sid:
                raise ValueError(f'Unmapped industry {new}')
            observations.append(dict(symbol=symbol_by_code[code], market='TPEX', sector_id=sid,
                sector_name=new, effective_anchor=effective, known_at=known, source_url=source(path, ''),
                sha256=sha(path), status='DATED_OFFICIAL_ANNOUNCEMENT', known_at_policy='official_notice_date_end_of_day',
                source_as_of=effective, taxonomy_version='official_market_industry_2023'))
    for symbol, effective, publication, name, url in LISTING_SOURCES:
        path = RAW / name
        if not path.exists():
            raise ValueError(f'Missing dated listing evidence: {path}')
        # Both dated official notices explicitly identify 生技醫療業 in their listing table.
        observations.append(dict(symbol=symbol, market='TWSE', sector_id='TWSE_22',
            sector_name='生技醫療業', effective_anchor=effective,
            known_at=publication+'T23:59:59+08:00', source_url=url, sha256=sha(path),
            status='DATED_OFFICIAL_ANNOUNCEMENT', known_at_policy='official_notice_date_end_of_day',
            source_as_of=effective, taxonomy_version='official_market_industry_2023'))
    identity = RAW / 'taifex_5371_20260903.pdf'
    if not identity.exists():
        raise ValueError('Missing dated 5371/3718 identity evidence')
    observations.append(dict(symbol='5371.TWO', market='TPEX', sector_id='UNKNOWN', sector_name='',
        effective_anchor='2026-09-03', known_at='2026-08-20T23:59:59+08:00', source_url=IDENTITY_SOURCE,
        sha256=sha(identity), status='UNKNOWN_ENTITY_TRANSITION', known_at_policy='official_notice_date_end_of_day',
        source_as_of='2026-09-03', taxonomy_version='official_market_industry_2023'))
    return observations


def build_industry(universe, observations):
    rows = []
    # Knowledge intervals are based on evidence actually available at that date.
    # Their effective_from is the start of usable knowledge, not a fabricated historical corporate event date.
    for stock in universe.itertuples(index=False):
        candidates = [x for x in observations if x['symbol'] == stock.symbol]
        prev = None
        for day in pd.date_range(START, END):
            ds = day.strftime('%Y-%m-%d')
            cutoff = ds+'T08:55:00+08:00'
            available = [x for x in candidates if x['effective_anchor'] <= ds and x['known_at'] <= cutoff]
            best = max(available, key=lambda x: (x['effective_anchor'], x['known_at'])) if available else None
            state = (best['sector_id'], best['source_url']) if best else ('UNKNOWN', '')
            if state != prev:
                if prev is not None:
                    rows[-1]['effective_to'] = ds
                row = {k: v for k, v in best.items() if k != 'effective_anchor'} if best else dict(
                    symbol=stock.symbol, market=stock.market, sector_id='UNKNOWN', sector_name='', known_at='',
                    source_url='', sha256='', status='UNKNOWN', known_at_policy='no_dated_official_evidence',
                    source_as_of='', taxonomy_version='official_market_industry_2023')
                row.update(effective_from=ds, effective_to='2026-09-22',
                           validity_policy='as_known_interval_inclusive_start_exclusive_end')
                rows.append(row)
                prev = state
    return pd.DataFrame(rows).sort_values(['symbol', 'effective_from'])


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(UNIVERSE, dtype={'code': str})
    daily = pd.read_csv(DAILY, usecols=['date', 'symbol', 'turnover'])
    calendar = set(daily.date[daily.date.between(START, END)].unique())
    indices = parse_indices(calendar)
    industry = build_industry(universe, membership_evidence(universe))
    industry.to_csv(OUT/'industry_history.csv', index=False)
    indices.to_csv(OUT/'sector_index_daily.csv', index=False)
    coverage = indices.groupby(['market', 'sector_id', 'index_type']).agg(rows=('date', 'size'), start=('date', 'min'), end=('date', 'max')).reset_index()
    coverage.to_csv(OUT/'index_coverage.csv', index=False)
    raw_files = [{'path': str(p.relative_to(ROOT)), 'sha256': sha(p)} for p in sorted(RAW.iterdir()) if p.is_file() and '.meta.' not in p.name]
    endpoints = {}
    for date in ['2025-01-02', END]:
        at = industry[(industry.effective_from <= date) & (industry.effective_to > date)]
        endpoints[date] = dict(known=int((at.sector_id!='UNKNOWN').sum()), unknown_symbols=at.loc[at.sector_id=='UNKNOWN','symbol'].tolist())
    manifest = dict(trust_state='CANDIDATE_PENDING_INDEPENDENT_REVIEW', period=[START, END],
        universe_path=str(UNIVERSE.relative_to(ROOT)), universe_sha256=sha(UNIVERSE),
        index_rows=len(indices), industry_rows=len(industry), coverage=endpoints, raw_files=raw_files,
        known_at_policy='Never use retrieval time as historical availability. Monthly classification uses following-month-end conservative public availability assumption, supported by official seventh-business-day release schedule.',
        source_schedule_url=MEMBER_PAGE,
        index_available_at_policy='Each official daily index close assumed publicly available by19:30Asia/Taipei on that session; not an archived timestamp.',
        history_policy='UNKNOWN before dated evidence; carry most recent publicly-known historical classification forward, record refreshed source intervals; no current classification backfill.',
        limitations=['Historical files retrieved today may contain revisions; no original-time archive snapshot exists.',
                     'TWSE industry-filter snapshot not used until historical classification semantics verified.',
                     'Official TWSE index coverage depends on existing cached daily reports; absent days not synthesized.',
                     'TPEx does not publish indices for every industry, including finance and trade department stores.',
                     '6446 and6589 switch from TPEx toTWSE biotechnology on official listing dates; 5371 successor classification is UNKNOWN from2026-09-03.',
                     'Canonical turnover coverage is incomplete; v2 has a separate execution dataset owned by the runner, not audited here.',
                     'Parent electronic/chemical composite indices are retained for audit but is_leaf=false; do not rank them alongside leaf sectors.'],
        canonical_turnover=dict(rows=len(daily), nonnull=int(daily.turnover.notna().sum())),
        output_hashes={name:sha(OUT/name) for name in ['industry_history.csv','sector_index_daily.csv','index_coverage.csv']})
    (OUT/'provenance.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({k:manifest[k] for k in ['index_rows','industry_rows','coverage']},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['all','download','build'], default='all')
    args = parser.parse_args()
    if args.mode in ['all','download']:
        download()
    if args.mode in ['all','build']:
        build()
