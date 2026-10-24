"""Fetch 2026-09 monthly revenue (published by 2026-10-10) into a separate file; existing data/hybrid untouched."""
import hashlib, json, sys
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT / 'legacy/ml'))
from hybrid import sources
OUT = Path(__file__).parent / 'data'
universe, rows, pages = sources.tickers(), [], []
for market in ('sii', 'otc'):
    for kind in (0, 1):
        url = sources.REVENUE_URL.format(market=market, roc=2026 - 1911, month=9, kind=kind)
        try:
            content = sources._get(url)
        except Exception as e:
            content = b''
        got = sources.parse_revenue(content, '2026-09', universe)
        rows += got; pages.append(dict(url=url, bytes=len(content), rows=len(got), sha256=hashlib.sha256(content).hexdigest()))
df = pd.DataFrame(rows)
df.to_csv(OUT / 'revenue_202609.csv', index=False)
(OUT / 'revenue_202609_pages.json').write_text(json.dumps(pages, indent=1))
print(pages); print(df.shape, df.columns.tolist() if len(df) else None)
