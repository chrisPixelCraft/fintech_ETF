#!/usr/bin/env python3
"""Build a resumable official cache; failed requests are explicit missing rows."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import gzip
from pathlib import Path
import sys
from urllib.request import Request, HTTPRedirectHandler, build_opener

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.v4_execution import (content_sha256, normalize_official_rows, official_url,
                              parse_official_day)


def build_execution_data(dates, universe_path, output, workers=2, timeout=25):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = output.parent / "official_raw"
    raw_dir.mkdir(exist_ok=True)
    universe = pd.read_csv(universe_path, dtype=str)
    roster = dict(zip(universe["yahoo_symbol"], universe["market"].replace({"TPEX": "TPEx"})))
    tasks = [(pd.Timestamp(d).normalize(), m) for d in sorted(set(dates))
             for m in ("TWSE", "TPEx")]

    def fetch(task):
        date, market = task
        url = official_url(market, date)
        raw_path = raw_dir / f"{market}_{date:%Y%m%d}.json.gz"
        legacy_raw_path = raw_dir / f"{market}_{date:%Y%m%d}.json"
        status = {"date": str(date.date()), "market": market, "url": url,
                  "raw_path": str(raw_path)}
        try:
            if raw_path.exists():
                content = gzip.decompress(raw_path.read_bytes())
            elif legacy_raw_path.exists():
                content = legacy_raw_path.read_bytes()
            else:
                request = Request(url, headers={"User-Agent": "Python-urllib/3.10"})
                class RedirectHandler(HTTPRedirectHandler):
                    http_error_308 = HTTPRedirectHandler.http_error_301

                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        return super().redirect_request(req, fp, 302 if code == 308 else code,
                                                        msg, headers, newurl)
                with build_opener(RedirectHandler).open(request, timeout=timeout) as response:
                    content = response.read()
            digest = content_sha256(content)
            frame = parse_official_day(json.loads(content), market, date,
                                       source_url=url, raw_sha256=digest, symbols=set(roster))
            if not raw_path.exists():
                raw_path.write_bytes(gzip.compress(content, mtime=0))
            status.update(status="OK", raw_sha256=digest,
                          compressed_sha256=content_sha256(raw_path.read_bytes()))
            records = {r["symbol"]: r for r in frame.to_dict("records")}
        except Exception as exc:
            status.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
            records = {}
        rows = []
        for symbol, expected_market in sorted(roster.items()):
            if expected_market != market:
                continue
            rows.append(records.get(symbol, {"date": date, "symbol": symbol,
                        "source": "MISSING_OFFICIAL", "source_url": url,
                        "quality_flags": "MISSING_OFFICIAL_ROW"}))
        return rows, status

    records, attempts = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rows, status in pool.map(fetch, tasks):
            records.extend(rows)
            attempts.append(status)
    frame = normalize_official_rows(records)
    frame.to_csv(output, index=False, date_format="%Y-%m-%d", float_format="%.17g")
    metadata = {"schema_version": 1, "status": "AVAILABLE" if frame.official_execution_available.all()
                else "BLOCK_CANONICAL_V4_INCOMPLETE_COVERAGE", "rows": len(frame),
                "available_rows": int(frame.official_execution_available.sum()),
                "normalized_sha256": content_sha256(output.read_bytes()),
                "universe_sha256": content_sha256(Path(universe_path).read_bytes()),
                "volume_unit": "shares", "trading_value_unit": "NTD",
                "raw_sha256_scope": "uncompressed HTTP response body",
                "compressed_sha256_scope": "deterministic gzip file bytes (mtime=0)",
                "universe_scope": "Retrospective fixed competition roster; historical transfers not inferred",
                "attempts": attempts}
    output.with_suffix(".manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", help="CSV containing a date column")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--universe", default="data/reference/universe_competition_20260731.csv")
    parser.add_argument("--output", default="outputs/v4/execution_data.csv")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.dates:
        dates = pd.to_datetime(pd.read_csv(args.dates)["date"]).tolist()
    elif args.start and args.end:
        dates = pd.bdate_range(args.start, args.end).tolist()
    else:
        parser.error("provide --dates or both --start and --end")
    result = build_execution_data(dates, args.universe, args.output, args.workers)
    print(json.dumps({k: v for k, v in result.items() if k != "attempts"}, indent=2))


if __name__ == "__main__":
    main()
