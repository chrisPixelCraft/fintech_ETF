"""Fetch TWSE/TPEx institutional net buying for sessions after the cached data (2026-09-24..2026-10-22)
into a separate file; data/hybrid is untouched."""
import hashlib, json, sys, time
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT / 'legacy/ml'))
from hybrid import sources
OUT = Path(__file__).parent / 'data'
cal = pd.DatetimeIndex(json.loads((ROOT / 'production_runs/preflight_20261026/calendar_v2.json').read_text())['calendar'])
days = cal[(cal >= '2026-09-24') & (cal <= '2026-10-22')]
universe, rows, log = sources.tickers(), [], []
for day in days:
    for src in ('twse', 'tpex'):
        url = sources.TWSE_URL.format(d=day) if src == 'twse' else sources.TPEX_URL.format(roc=day.year - 1911, d=day)
        content = sources._get(url)
        got = sources.PARSE[src](json.loads(content), universe)
        rows += [dict(date=str(day.date()), source=src, **r) for r in got]
        log.append(dict(date=str(day.date()), source=src, rows=len(got), sha256=hashlib.sha256(content).hexdigest()))
        time.sleep(sources.PAUSE[src])
    print(day.date(), flush=True)
pd.DataFrame(rows)[['date', 'ticker', 'foreign_net', 'trust_net', 'source']].to_csv(OUT / 'flows_20260924_20261022.csv', index=False)
(OUT / 'flows_20260924_20261022_log.json').write_text(json.dumps(log, indent=1))
print(len(days), 'sessions', len(rows), 'rows', min(l['rows'] for l in log), 'min rows/page')
