"""Fetch minimal, timestamped v2 overnight inputs and preserve raw provenance.

Yahoo chart data are used as a research source, not as an official exchange
archive. Historical daily bars do not publish per-row availability timestamps,
so this script assigns a documented conservative schedule after each completed
session. It never claims that derived timestamp came from Yahoo.

Earnings, institutional flow and TAIFEX night-session data remain UNKNOWN until
a stable point-in-time source and publication timing are implemented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/v2_auxiliary_signals.json"
DEFAULT_OUTPUT = ROOT / "data/v2_auxiliary"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def curl(url: str) -> bytes:
    completed = subprocess.run(
        ["curl", "-L", "--max-time", "60", "-fsS", "-A", "Mozilla/5.0", url],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def yahoo_url(symbol: str, start: date, end: date) -> str:
    period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    encoded = urllib.parse.quote(symbol, safe="")
    return (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )


def availability(session: pd.Timestamp, kind: str) -> tuple[pd.Timestamp, str]:
    if kind == "us_equity":
        local = datetime(session.year, session.month, session.day, 16, 30, tzinfo=ZoneInfo("America/New_York"))
        return pd.Timestamp(local).tz_convert("UTC"), "DERIVED_CONSERVATIVE_US_SESSION_CLOSE_PLUS_30M"
    if kind == "fx_daily":
        utc = datetime(session.year, session.month, session.day, tzinfo=timezone.utc) + timedelta(days=1, minutes=30)
        return pd.Timestamp(utc), "DERIVED_CONSERVATIVE_UTC_DAY_CLOSE_PLUS_30M"
    raise ValueError(f"Unsupported availability policy: {kind}")


def parse_yahoo(
    raw: bytes, instrument: str, spec: dict, url: str, retrieved_at: str
) -> pd.DataFrame:
    payload = json.loads(raw)
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ValueError(str(chart["error"]))
    result = (chart.get("result") or [None])[0]
    if not result:
        raise ValueError("Yahoo response has no result")
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adjusted = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or quote.get("close") or []
    closes = quote.get("close") or []
    if not (len(timestamps) == len(closes) == len(adjusted)):
        raise ValueError("Yahoo arrays have inconsistent lengths")
    source_hash = sha256(raw)
    rows = []
    for stamp, close, adjclose in zip(timestamps, closes, adjusted):
        if close is None or adjclose is None or not np.isfinite([close, adjclose]).all():
            continue
        source_time = pd.Timestamp(stamp, unit="s", tz="UTC")
        if spec["kind"] == "us_equity":
            session = source_time.tz_convert("America/New_York").normalize().tz_localize(None)
        else:
            session = source_time.normalize().tz_localize(None)
        available_at, basis = availability(session, spec["kind"])
        rows.append({
            "instrument": instrument,
            "source_symbol": spec["source_symbol"],
            "session_date": str(session.date()),
            "close": float(close),
            "adjusted_close": float(adjclose),
            "source_timestamp_utc": source_time.isoformat(),
            "available_at": available_at.isoformat(),
            "available_at_basis": basis,
            "source_url": url,
            "source_sha256": source_hash,
            "retrieved_at": retrieved_at,
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("Yahoo response contains no finite bars")
    frame = frame.sort_values("session_date")
    frame["return1"] = frame.adjusted_close.pct_change(fill_method=None)
    return frame.dropna(subset=["return1"]).reset_index(drop=True)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    temp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if end < start:
        raise ValueError("end precedes start")
    config_raw = args.config.read_bytes()
    config = json.loads(config_raw)
    output, raw_dir = args.output, args.output / "raw"
    output.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    frames, sources, artifacts = [], {}, []
    for instrument, spec in config["overnight_instruments"].items():
        symbol = spec.get("source_symbol")
        if spec["kind"] not in {"us_equity", "fx_daily"} or not symbol:
            sources[instrument] = {
                "status": "UNKNOWN",
                "reason": "NO_VERIFIED_POINT_IN_TIME_FETCH_IMPLEMENTED",
            }
            continue
        url = yahoo_url(symbol, start, end)
        try:
            raw = curl(url)
            if raw.lstrip().startswith(b"<"):
                raise ValueError("Endpoint returned HTML, not chart JSON")
            digest = sha256(raw)
            raw_path = raw_dir / f"{instrument.lower()}_{digest[:16]}.json"
            if not raw_path.exists():
                atomic_write(raw_path, raw)
            frame = parse_yahoo(raw, instrument, spec, url, retrieved_at)
            frame = frame[(pd.to_datetime(frame.session_date).dt.date >= start)
                          & (pd.to_datetime(frame.session_date).dt.date <= end)]
            frames.append(frame)
            sources[instrument] = {
                "status": "FETCHED_RESEARCH_SOURCE",
                "rows": int(len(frame)),
                "source_url": url,
                "raw_path": str(raw_path.relative_to(ROOT)),
                "sha256": digest,
                "availability": "DERIVED_CONSERVATIVE_SCHEDULE_NOT_SOURCE_PUBLISHED",
            }
            artifacts.append({"path": str(raw_path.relative_to(ROOT)), "sha256": digest})
        except Exception as exc:
            sources[instrument] = {"status": "UNKNOWN_FETCH_FAILED", "reason": f"{type(exc).__name__}: {exc}"}
    columns = [
        "instrument", "source_symbol", "session_date", "close", "adjusted_close", "return1",
        "source_timestamp_utc", "available_at", "available_at_basis", "source_url",
        "source_sha256", "retrieved_at",
    ]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)
    combined = combined[columns].sort_values(["instrument", "session_date"]).reset_index(drop=True)
    csv_bytes = combined.to_csv(index=False).encode()
    atomic_write(output / "overnight_daily.csv", csv_bytes)
    sources["EARNINGS"] = {
        "status": "UNKNOWN",
        "reason": "NO_VERIFIED_POINT_IN_TIME_EARNINGS_SOURCE_CONFIGURED",
        "fallback": "NEUTRAL",
    }
    sources["INSTITUTIONAL"] = {
        "status": "UNKNOWN",
        "reason": "NO_VERIFIED_POINT_IN_TIME_PER_STOCK_FLOW_SOURCE_CONFIGURED",
        "fallback": "NEUTRAL",
    }
    status = {
        "schema_version": "1.0",
        "retrieved_at": retrieved_at,
        "config_sha256": sha256(config_raw),
        "sources": sources,
    }
    atomic_write(output / "source_status.json", (json.dumps(status, indent=2, ensure_ascii=False) + "\n").encode())
    manifest = {
        "schema_version": "1.0",
        "retrieved_at": retrieved_at,
        "start": args.start,
        "end": args.end,
        "config_path": str(args.config.relative_to(ROOT)),
        "config_sha256": sha256(config_raw),
        "normalized": {
            "path": str((output / "overnight_daily.csv").relative_to(ROOT)),
            "rows": int(len(combined)),
            "sha256": sha256(csv_bytes),
        },
        "raw_artifacts": artifacts,
        "trust": "CANDIDATE_RESEARCH_SOURCE_WITH_DERIVED_AVAILABILITY",
    }
    atomic_write(output / "manifest.json", (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode())
    print(json.dumps({"rows": len(combined), "sources": sources}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr.decode(errors="replace"), file=sys.stderr)
        raise
