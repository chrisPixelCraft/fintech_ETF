"""Fetch and normalize point-in-time institutional-flow features for v2.

The signal definition is deliberately fixed here, before evaluating strategy
results. FinMind does not provide a publication timestamp for this dataset, so
``available_at`` is a derived same-day 19:30 Asia/Taipei assumption and is
labelled as such in every row and in the provenance manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UNIVERSE = ROOT / "data/extended/processed/universe_20241231.csv"
DEFAULT_MARKET = ROOT / "data/v2/market_daily.csv"
DEFAULT_CANONICAL = ROOT / "data/extended/processed/daily_canonical.csv"
DEFAULT_OUTPUT = ROOT / "data/v2_auxiliary"
DATASET = "TaiwanStockInstitutionalInvestorsBuySell"
BASE_URL = "https://api.finmindtrade.com/api/v4/data"
INCLUDED_NAMES = ("Foreign_Investor", "Investment_Trust")
ROLLING_SESSIONS = 5
DENOMINATOR_SCALE = 0.1
AVAILABILITY_BASIS = "DERIVED_ASSUMPTION_SAME_DAY_19_30_ASIA_TAIPEI_NOT_SOURCE_PUBLISHED"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def source_url(ticker: str, start: str, end: str) -> str:
    query = urlencode({
        "dataset": DATASET,
        "data_id": ticker,
        "start_date": start,
        "end_date": end,
    })
    return f"{BASE_URL}?{query}"


def validate_payload(raw: bytes, ticker: str) -> dict:
    payload = json.loads(raw)
    if payload.get("status") != 200 or payload.get("msg") != "success":
        raise ValueError(f"FinMind response status={payload.get('status')} msg={payload.get('msg')}")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("FinMind response data is not a list")
    wrong = {str(row.get("stock_id")) for row in rows if str(row.get("stock_id")) != ticker}
    if wrong:
        raise ValueError(f"response contains unexpected stock_id values: {sorted(wrong)}")
    return payload


def cached_raw(raw_dir: Path, ticker: str) -> Path | None:
    candidates = sorted(
        raw_dir.glob(f"{DATASET}_{ticker}_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def fetch_one(ticker: str, start: str, end: str, raw_dir: Path, seed_2330: Path) -> dict:
    url = source_url(ticker, start, end)
    existing = cached_raw(raw_dir, ticker)
    if existing is not None:
        raw = existing.read_bytes()
        validate_payload(raw, ticker)
        return {"ticker": ticker, "url": url, "raw": raw, "path": existing, "cache": True}

    if ticker == "2330" and seed_2330.exists():
        raw = seed_2330.read_bytes()
        validate_payload(raw, ticker)
    else:
        completed = subprocess.run(
            ["curl", "-L", "--max-time", "60", "-fsS", url],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        raw = completed.stdout
        validate_payload(raw, ticker)

    sha = digest(raw)
    path = raw_dir / f"{DATASET}_{ticker}_{sha[:16]}.json"
    if ticker == "2330" and seed_2330.exists() and seed_2330.read_bytes() == raw:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            shutil.copyfile(seed_2330, path)
    elif not path.exists():
        atomic_write(path, raw)
    return {"ticker": ticker, "url": url, "raw": raw, "path": path, "cache": False}


def load_volume(universe: pd.DataFrame, market_path: Path, canonical_path: Path) -> pd.DataFrame:
    symbol_to_ticker = {
        str(row.yahoo_symbol): str(row.ticker)
        for row in universe.itertuples(index=False)
    }

    market = pd.read_csv(market_path, dtype={"symbol": str}, low_memory=False)
    market["ticker"] = market["symbol"].map(symbol_to_ticker)
    market["date"] = pd.to_datetime(market["date"], errors="coerce")
    execution = pd.to_numeric(market.get("execution_volume"), errors="coerce")
    historical = pd.to_numeric(market.get("volume"), errors="coerce")
    market["resolved_volume"] = execution.where(execution.gt(0), historical)
    market = market.loc[
        market["ticker"].notna() & market["date"].notna() & market["resolved_volume"].gt(0),
        ["ticker", "date", "resolved_volume"],
    ].drop_duplicates(["ticker", "date"], keep="last")

    canonical = pd.read_csv(canonical_path, dtype={"symbol": str}, low_memory=False)
    canonical["ticker"] = canonical["symbol"].map(symbol_to_ticker)
    canonical["date"] = pd.to_datetime(canonical["date"], errors="coerce")
    canonical["canonical_volume"] = pd.to_numeric(canonical.get("volume"), errors="coerce")
    canonical = canonical.loc[
        canonical["ticker"].notna() & canonical["date"].notna() & canonical["canonical_volume"].gt(0),
        ["ticker", "date", "canonical_volume"],
    ].drop_duplicates(["ticker", "date"], keep="last")

    volume = market.merge(canonical, on=["ticker", "date"], how="outer")
    volume["volume"] = volume["resolved_volume"].where(
        volume["resolved_volume"].gt(0), volume["canonical_volume"]
    )
    volume["volume_source"] = np.where(
        volume["resolved_volume"].gt(0),
        "market_daily.execution_volume_else_volume",
        "daily_canonical.volume",
    )
    return volume.loc[volume["volume"].gt(0), ["ticker", "date", "volume", "volume_source"]]


def normalize_one(result: dict, ticker_to_symbol: dict[str, str], volume: pd.DataFrame,
                  retrieved_at: str) -> tuple[pd.DataFrame, dict]:
    ticker = result["ticker"]
    raw = result["raw"]
    rows = json.loads(raw)["data"]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(), {"raw_rows": 0, "published_dates": 0, "normalized_rows": 0,
                                "missing_volume_dates": 0}

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["buy"] = pd.to_numeric(frame["buy"], errors="coerce")
    frame["sell"] = pd.to_numeric(frame["sell"], errors="coerce")
    frame = frame.dropna(subset=["date", "buy", "sell", "name"])
    if ((frame["buy"] < 0) | (frame["sell"] < 0)).any():
        raise ValueError(f"{ticker}: negative buy/sell value")

    published_dates = frame[["date"]].drop_duplicates()
    included = frame[frame["name"].isin(INCLUDED_NAMES)].copy()
    included["net_buy"] = included["buy"] - included["sell"]
    daily = included.groupby("date", as_index=False)["net_buy"].sum()
    # A published date with neither included category is a source-observed zero.
    daily = published_dates.merge(daily, on="date", how="left").fillna({"net_buy": 0.0})
    daily["ticker"] = ticker
    daily = daily.merge(volume[volume["ticker"] == ticker], on=["ticker", "date"], how="left")
    missing_volume_dates = int(daily["volume"].isna().sum())
    daily = daily.dropna(subset=["volume"]).sort_values("date").reset_index(drop=True)

    daily["net_buy_5d"] = daily["net_buy"].rolling(ROLLING_SESSIONS, min_periods=ROLLING_SESSIONS).sum()
    daily["volume_5d"] = daily["volume"].rolling(ROLLING_SESSIONS, min_periods=ROLLING_SESSIONS).sum()
    daily["net_buy_ratio_5d"] = daily["net_buy_5d"] / daily["volume_5d"]
    daily["score"] = (daily["net_buy_ratio_5d"] / DENOMINATOR_SCALE).clip(-1.0, 1.0)
    daily = daily.dropna(subset=["score"])
    sha = digest(raw)
    daily["symbol"] = ticker_to_symbol[ticker]
    daily["available_at"] = daily["date"].dt.strftime("%Y-%m-%dT19:30:00+08:00")
    daily["available_at_basis"] = AVAILABILITY_BASIS
    daily["source_url"] = result["url"]
    daily["source_sha256"] = sha
    daily["retrieved_at"] = retrieved_at
    daily["data_status"] = "OBSERVED_SOURCE_DERIVED_SCORE"
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
    columns = [
        "date", "symbol", "ticker", "score", "net_buy_5d", "volume_5d",
        "net_buy_ratio_5d", "available_at", "available_at_basis", "source_url",
        "source_sha256", "retrieved_at", "data_status", "volume_source",
    ]
    metadata = {
        "raw_rows": int(len(rows)),
        "published_dates": int(len(published_dates)),
        "included_rows": int(len(included)),
        "normalized_rows": int(len(daily)),
        "missing_volume_dates": missing_volume_dates,
        "categories": sorted(frame["name"].astype(str).unique().tolist()),
    }
    return daily[columns], metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-09-21")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--market", type=Path, default=DEFAULT_MARKET)
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if date.fromisoformat(args.end) < date.fromisoformat(args.start):
        raise ValueError("end precedes start")
    if not 1 <= args.workers <= 4:
        raise ValueError("workers must be between 1 and 4")

    universe = pd.read_csv(args.universe, dtype=str)
    if 'ticker' not in universe:
        universe=universe.rename(columns={'code':'ticker','symbol':'yahoo_symbol'})
    universe["ticker"] = universe["ticker"].str.strip().str.zfill(4)
    universe = universe.drop_duplicates("ticker", keep="first")
    if len(universe) != 150:
        raise ValueError(f"expected 150 universe members, got {len(universe)}")
    tickers = universe["ticker"].tolist()
    ticker_to_symbol = dict(zip(universe["ticker"], universe["yahoo_symbol"]))
    volume = load_volume(universe, args.market, args.canonical)
    raw_dir = args.output / "raw/flow"
    raw_dir.mkdir(parents=True, exist_ok=True)
    seed_2330 = args.output / f"raw/{DATASET}_2330.json"
    retrieved_at = datetime.now(timezone.utc).isoformat()

    fetched, failures = {}, {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(fetch_one, ticker, args.start, args.end, raw_dir, seed_2330): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                fetched[ticker] = future.result()
            except Exception as exc:
                failures[ticker] = f"{type(exc).__name__}: {exc}"

    frames, per_ticker, normalization_failures = [], {}, {}
    raw_artifacts = []
    for ticker in tickers:
        result = fetched.get(ticker)
        if result is None:
            continue
        raw_artifacts.append({
            "ticker": ticker,
            "path": relative(result["path"]),
            "sha256": digest(result["raw"]),
            "source_url": result["url"],
            "cache_reused": bool(result["cache"]),
        })
        try:
            frame, metadata = normalize_one(result, ticker_to_symbol, volume, retrieved_at)
            per_ticker[ticker] = metadata
            if not frame.empty:
                frames.append(frame)
        except Exception as exc:
            normalization_failures[ticker] = f"{type(exc).__name__}: {exc}"

    columns = [
        "date", "symbol", "ticker", "score", "net_buy_5d", "volume_5d",
        "net_buy_ratio_5d", "available_at", "available_at_basis", "source_url",
        "source_sha256", "retrieved_at", "data_status", "volume_source",
    ]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)
    combined = combined[columns].sort_values(["date", "symbol"]).reset_index(drop=True)
    output_path = args.output / "institutional_pit.csv"
    csv_bytes = combined.to_csv(index=False).encode()
    atomic_write(output_path, csv_bytes)

    all_failures = {**failures, **normalization_failures}
    if not fetched:
        status = "UNKNOWN_FETCH_FAILED"
    elif all_failures:
        status = "FETCHED_PARTIAL_DERIVED_AVAILABILITY"
    else:
        status = "FETCHED_RESEARCH_SOURCE_DERIVED_AVAILABILITY"
    provenance = {
        "schema_version": "1.0",
        "created_at": retrieved_at,
        "query": {"dataset": DATASET, "start": args.start, "end": args.end,
                  "url_template": f"{BASE_URL}?dataset={DATASET}&data_id={{ticker}}&start_date={args.start}&end_date={args.end}"},
        "formula": {
            "freeze_status": "FROZEN_BEFORE_STRATEGY_RESULT_NO_TUNING",
            "included_investor_names": list(INCLUDED_NAMES),
            "excluded_dealer_names": ["Dealer_self", "Dealer_Hedging", "Foreign_Dealer_Self"],
            "net_buy": "sum(buy - sell) for Foreign_Investor and Investment_Trust",
            "score": "clip(rolling_5_sum(net_buy) / rolling_5_sum(volume) / 0.1, -1, 1)",
            "rolling_sessions": ROLLING_SESSIONS,
            "denominator_scale": DENOMINATOR_SCALE,
        },
        "availability": {
            "available_at": "source date at 19:30 Asia/Taipei",
            "basis": AVAILABILITY_BASIS,
            "caveat": "FinMind rows do not prove an original publication timestamp; the schedule is a conservative derived assumption.",
        },
        "inputs": {
            "universe": {"path": relative(args.universe), "sha256": digest(args.universe.read_bytes())},
            "market": {"path": relative(args.market), "sha256": digest(args.market.read_bytes())},
            "canonical_volume_fallback": {"path": relative(args.canonical), "sha256": digest(args.canonical.read_bytes())},
        },
        "status": status,
        "requested_tickers": len(tickers),
        "fetched_tickers": len(fetched),
        "normalized_tickers": len(per_ticker),
        "failures": all_failures,
        "per_ticker": per_ticker,
        "raw_artifacts": raw_artifacts,
        "output": {"path": relative(output_path), "rows": int(len(combined)), "sha256": digest(csv_bytes)},
        "source_status_merge": {
            "INSTITUTIONAL": {
                "status": status,
                "rows": int(len(combined)),
                "symbols": int(combined["symbol"].nunique()) if not combined.empty else 0,
                "path": relative(output_path),
                "sha256": digest(csv_bytes),
                "availability": AVAILABILITY_BASIS,
                "failure_count": len(all_failures),
                "fallback": "NEUTRAL_WHEN_MISSING",
            }
        },
        "trust": "CANDIDATE_SOURCE_ROWS_WITH_DERIVED_AVAILABILITY_ASSUMPTION",
    }
    provenance_path = args.output / "institutional_provenance.json"
    atomic_write(provenance_path, (json.dumps(provenance, indent=2, ensure_ascii=False) + "\n").encode())
    print(json.dumps(provenance["source_status_merge"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
