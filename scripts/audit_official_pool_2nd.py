"""Independent bounded data gate; does not certify the future-known pool as PIT."""
from pathlib import Path
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_v2_study import check, sha
from src.backtest import aggregate_four_hour

BASE = ROOT / 'data/tuning_2nd/official_universe'
OUT = BASE / 'processed'
AUDIT = ROOT / 'outputs/tuning_report_2nd_try/official_data_audit.json'


def audit():
    evidence = json.loads((OUT / 'readiness.json').read_text())
    check(evidence['blockers'] == [], 'Producer reports unresolved data blockers')
    for name, expected in evidence['output_hashes'].items():
        check(sha(OUT / name) == expected, 'Canonical bytes changed: ' + name)
    for name, expected in evidence['input_hashes'].items():
        check(sha(ROOT / name) == expected, 'Raw evidence changed: ' + name)
    check(sha(ROOT / 'scripts/fetch_tuning2_official_data.py') == evidence['source_script_sha256'], 'Producer source changed')
    daily, hourly, universe = (pd.read_csv(OUT / name, low_memory=False) for name in ('daily.csv', 'hourly.csv', 'universe.csv'))
    check(len(universe) == universe.symbol.nunique() == 150, 'Wrong canonical membership count')
    check(universe.known_at.eq('2026-09-18T10:05:13+08:00').all(), 'Publication date lost/backdated')
    check(universe.universe_type.eq('EX_POST_FIXED_OFFICIAL_POOL_NOT_PIT').all(), 'Biased universe incorrectly labeled PIT')
    check(not daily.duplicated(['date', 'symbol']).any(), 'Duplicate daily bars')
    check(not hourly.duplicated(['timestamp', 'symbol']).any(), 'Duplicate hourly bars')
    members = set(universe.symbol)
    observed = daily[daily.symbol.isin(members) & daily.date.ge('2025-01-01')]
    check(observed.date.nunique() == 417, 'Wrong evaluation calendar')
    tradable = observed[observed.volume.gt(0)]
    check(tradable.turnover.gt(0).all() and tradable.execution_volume.gt(0).all(), 'Tradable bar lacks paired VWAP')
    np.testing.assert_allclose(tradable.execution_vwap, tradable.turnover / tradable.execution_volume, rtol=1e-12)
    new = set(pd.read_csv(OUT / 'new_company_coverage.csv').symbol)
    primary_listing = {}
    for mode, suffix in ((2, '.TW'), (4, '.TWO')):
        text = BeautifulSoup((BASE / f'raw/listing/isin_{mode}.html').read_bytes(), 'html.parser')
        for tr in text.select('tr'):
            values = [td.get_text(' ', strip=True) for td in tr.select('td')]
            if len(values) >= 3 and values[0].split():
                primary_listing[values[0].split()[0] + suffix] = values[2].replace('/', '-')
    new_listing_checks = {}
    for symbol in sorted(new):
        first = primary_listing[symbol]
        for frame in (daily, hourly):
            check(not (frame.symbol.eq(symbol) & frame.date.lt(first)).any(), 'Pre-listing warmup: ' + symbol)
        new_listing_checks[symbol] = dict(regular_listing=first, first_daily=daily[daily.symbol.eq(symbol)].date.min())
    special = [('8932.TWO', '2024-08-29', '2024-09-09', 2.),
               ('8932.TWO', '2026-02-25', '2026-03-09', 2.),
               ('6919.TW', '2025-07-14', '2025-07-21', 10.)]
    for symbol, halt, resumed, ratio in special:
        for frame in (daily, hourly):
            check(not (frame.symbol.eq(symbol) & frame.date.ge(halt) & frame.date.lt(resumed)).any(), 'Fabricated suspended bar')
            event = frame[frame.symbol.eq(symbol) & frame.date.eq(resumed)]
            if frame is daily or frame[frame.symbol.eq(symbol)].date.min() <= resumed:
                check(len(event) and event.split.eq(ratio).all(), 'Split not applied on observed resumption: ' + symbol)
    for name, tokens in [('8932_20240909.html', ('113年9月9日', '每股10元', '每股5元')),
                         ('8932_20260309.html', ('115年3月9日', '每股5元', '每股2.5元'))]:
        body = BeautifulSoup((BASE / 'raw/events' / name).read_bytes(), 'html.parser').get_text(' ', strip=True)
        check(all(token in body for token in tokens), 'TPEx split announcement differs')
    primary_6919 = json.loads((BASE / 'raw/events/6919_202507_official.json').read_text())
    dates = [row[0] for row in primary_6919['data']]
    check(dates[dates.index('114/07/11') + 1] == '114/07/21', '6919 resumption not supported by TWSE')
    # Reconstruct hourly normalization from independent raw timestamps and factors.
    volume_rows, max_price_error = 0, 0.
    for symbol in sorted(new):
        raw = json.loads((BASE / f'raw/yahoo/{symbol}_60m.json').read_text())['chart']['result'][0]
        values = raw['indicators']['quote'][0]
        source = pd.DataFrame(values, index=pd.to_datetime(raw['timestamp'], unit='s', utc=True))
        actual = hourly[hourly.symbol.eq(symbol)].copy()
        original = source.reindex(pd.to_datetime(actual.timestamp, utc=True))
        np.testing.assert_allclose(actual.volume.to_numpy(), original.volume.to_numpy(), rtol=0, atol=0)
        for column in ('open', 'high', 'low', 'close'):
            expected = original[column].to_numpy() * actual.split_restoration_factor.to_numpy()
            error = np.abs(actual[column].to_numpy() / expected - 1)
            max_price_error = max(max_price_error, float(error.max()))
            check((error < 1e-12).all(), 'Hourly restoration mismatch: ' + symbol)
        volume_rows += len(actual)
    opens = hourly[hourly.symbol.isin(new) & hourly.timestamp.str[11:16].eq('09:00')]
    pairs = opens.merge(daily[['date', 'symbol', 'open']], on=['date', 'symbol'], suffixes=('_hourly', '_daily'))
    differences = np.abs(pairs.open_hourly / pairs.open_daily - 1)
    check(differences.max() < .02, 'Material hourly/daily price unit disagreement')
    actions = pd.read_csv(OUT / 'new_company_actions.csv')
    check(actions.cash_basis.eq('per_pre_action_old_share').all(), 'Cash is not per old share')
    for event in actions.itertuples():
        row = daily[daily.symbol.eq(event.symbol) & daily.date.eq(event.date)]
        check(len(row) == 1, 'Missing corporate-action daily bar')
        np.testing.assert_allclose(row[['dividend', 'split']].to_numpy()[0], [event.dividend, event.split], rtol=1e-12)
    all_bars = aggregate_four_hour(hourly)
    warmups = {}
    for symbol in sorted(new):
        days = sorted(daily[daily.symbol.eq(symbol)].date)
        bars = sorted(all_bars[all_bars.symbol.eq(symbol)].date)
        warmups[symbol] = dict(daily_200th=days[199] if len(days) >= 200 else None,
                               completed_four_hour_50th=str(bars[49].date()) if len(bars) >= 50 else None)
    return dict(status='PASS', scope='BOUNDED_CORPORATE_ACTION_LISTING_AND_UNIT_QA_NOT_PIT_CERTIFICATION',
                verified_at=datetime.now(timezone.utc).isoformat(), auditor_sha256=sha(__file__),
                verified_output_hashes=evidence['output_hashes'], raw_source_hashes_checked=len(evidence['input_hashes']),
                universe_members=150, actual_known_at=universe.known_at.unique().tolist(), sessions=417,
                daily_rows=len(daily), hourly_rows=len(hourly), raw_hourly_volume_rows_verified=volume_rows,
                max_hourly_price_restoration_relative_error=max_price_error,
                hourly_open_pairs=len(pairs), max_hourly_open_relative_difference=float(differences.max()),
                hourly_open_difference_above_half_percent=int((differences > .005).sum()),
                new_listing_checks=new_listing_checks, special_actions=special, warmup_observation_dates=warmups,
                caveats=['Official membership is future-known for most of this history; this track is explicitly retrospective.',
                         '6919 1-for-10 ratio was checked in issuer presentation physical page6; the resumption date is corroborated by official monthly trading records.',
                         'Intraday and daily opening prices have 15 small discrepancies above0.5%, none above2%; this check rejects unit-scale errors, not every vendor tick discrepancy.',
                         'Existing canonical-data limitations and unknown Active Share remain unresolved.'])


if __name__ == '__main__':
    try:
        result = audit()
    except Exception as error:
        result = dict(status='FAIL', error=str(error), auditor_sha256=sha(__file__))
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        AUDIT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
        raise
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: result[k] for k in ('status', 'sessions', 'daily_rows', 'hourly_rows', 'raw_hourly_volume_rows_verified')}))
