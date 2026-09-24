"""Official daily value/share-volume execution, isolated from signal generation.

TWSE MI_INDEX and TPEx dailyQuotes expose individual shares and NTD.
TPEx monthly tradingStock rounds both to thousands; it is deliberately not used.
No official quote is ever reconstructed from Yahoo or a rounded average field.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

import numpy as np
import pandas as pd

OFFICIAL_DAILY_AVERAGE = "OFFICIAL_DAILY_AVERAGE"
DAILY_OPEN_RESEARCH_PROXY = "DAILY_OPEN_RESEARCH_PROXY"
COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume",
           "trading_value", "average_execution_price", "source", "quality_flags",
           "official_execution_available", "source_url", "raw_sha256"]


def _number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return np.nan


def _date(value):
    text = str(value).strip().replace("-", "/")
    if "/" in text:
        parts = text.split("/")
        if int(parts[0]) < 1911:
            parts[0] = str(int(parts[0]) + 1911)
        text = "/".join(parts)
    return pd.Timestamp(text).normalize()


def normalize_official_rows(rows: Iterable[dict]) -> pd.DataFrame:
    """Validate normalized units and retain invalid observations with explicit flags.

    Callers must supply volume in shares, trading_value in NTD, and official
    source identity. Missing, nonfinite, and nonpositive inputs fail closed.
    """
    result = []
    for record in rows:
        row = dict(record)
        row["date"] = _date(row["date"])
        row["symbol"] = str(row["symbol"])
        flags = [x for x in str(row.get("quality_flags", "")).split("|") if x]
        for name in ("open", "high", "low", "close", "volume", "trading_value"):
            row[name] = _number(row.get(name))
        source_ok = row.get("source") in {"TWSE_OFFICIAL", "TPEX_OFFICIAL"}
        if not source_ok:
            flags.append("MISSING_OFFICIAL_SOURCE")
        if ((row["symbol"].endswith(".TW") and row.get("source") == "TPEX_OFFICIAL")
                or (row["symbol"].endswith(".TWO") and row.get("source") == "TWSE_OFFICIAL")):
            flags.append("WRONG_OFFICIAL_MARKET")
            source_ok = False
        for name in ("volume", "trading_value"):
            if not np.isfinite(row[name]) or row[name] <= 0:
                flags.append("INVALID_" + name.upper())
        valid = source_ok and all(np.isfinite(row[k]) and row[k] > 0
                                  for k in ("volume", "trading_value"))
        price = row["trading_value"] / row["volume"] if valid else np.nan
        if valid and (not np.isfinite(price) or price <= 0):
            flags.append("INVALID_AVERAGE")
            valid = False
        if valid and np.isfinite(row["low"]) and np.isfinite(row["high"]):
            if not row["low"] <= price <= row["high"]:
                flags.append("AVERAGE_OUTSIDE_OHLC_RANGE")
        row["average_execution_price"] = price if valid else np.nan
        row["official_execution_available"] = bool(valid)
        row["quality_flags"] = "|".join(sorted(set(flags)))
        row.setdefault("source_url", "")
        row.setdefault("raw_sha256", "")
        row.setdefault("source", "MISSING_OFFICIAL")
        result.append(row)
    frame = pd.DataFrame(result, columns=COLUMNS)
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Duplicate official date/symbol observations")
    return frame.sort_values(["date", "symbol"]).reset_index(drop=True)


def official_url(market: str, date) -> str:
    date = pd.Timestamp(date)
    if market == "TWSE":
        return ("https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json"
                f"&date={date:%Y%m%d}&type=ALLBUT0999")
    if market == "TPEx":
        return ("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
                f"?date={date:%Y/%m/%d}&id=&response=json")
    raise ValueError(f"Unsupported market: {market}")


def parse_official_day(payload: dict, market: str, date, *, source_url="", raw_sha256="", symbols=None):
    """Parse header-identified, exact-unit official daily all-security tables.

    Reject stale response dates and unexpected schemas instead of guessing units.
    Historical market transfers are not imputed from a current listing roster.
    """
    if market not in {"TWSE", "TPEx"}:
        raise ValueError(f"Unsupported market: {market}")
    expected = _date(date)
    if "date" not in payload or _date(payload["date"]) != expected:
        raise ValueError("Official response date does not match request")
    names = ({"symbol": "證券代號", "open": "開盤價", "high": "最高價",
              "low": "最低價", "close": "收盤價", "volume": "成交股數",
              "trading_value": "成交金額"} if market == "TWSE" else
             {"symbol": "代號", "open": "開盤", "high": "最高", "low": "最低",
              "close": "收盤", "volume": "成交股數", "trading_value": "成交金額(元)"})
    candidates = [t for t in payload.get("tables", [])
                  if all(v in t.get("fields", []) for v in names.values())
                  and (market != "TPEx" or t.get("title", "上櫃股票行情") == "上櫃股票行情")]
    if len(candidates) != 1:
        raise ValueError("Missing or ambiguous official exact-unit daily table")
    table = candidates[0]
    fields = table["fields"]
    rows = []
    for values in table["data"]:
        row = {k: values[fields.index(v)] for k, v in names.items()}
        row["symbol"] += ".TW" if market == "TWSE" else ".TWO"
        if symbols is not None and row["symbol"] not in symbols:
            continue
        row.update(date=expected, source="TWSE_OFFICIAL" if market == "TWSE" else "TPEX_OFFICIAL",
                   source_url=source_url, raw_sha256=raw_sha256)
        rows.append(row)
    return normalize_official_rows(rows)


def read_execution_table(path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"symbol": str}, keep_default_na=False)
    # Recompute prices from raw normalized quantities; never trust cached prices.
    return normalize_official_rows(frame.to_dict("records"))


def resolve_execution_prices(table, date, symbols, mode, proxy_open=None) -> pd.Series:
    """Return explicit execution prices; missing canonical prices remain NaN."""
    symbols = list(symbols)
    if mode in {DAILY_OPEN_RESEARCH_PROXY, "open_proxy"}:
        if proxy_open is None:
            raise ValueError("Open proxy mode requires explicit proxy_open")
        result = pd.Series(proxy_open, dtype=float).reindex(symbols)
    elif mode in {OFFICIAL_DAILY_AVERAGE, "official_average"}:
        if table is None or table.empty:
            return pd.Series(np.nan, index=symbols, name="execution_price", dtype=float)
        day = table.loc[pd.to_datetime(table["date"]).dt.normalize() == _date(date)]
        if day["symbol"].duplicated().any():
            raise ValueError("Duplicate official quotes")
        # Revalidation prevents an edited cached average from becoming execution.
        day = normalize_official_rows(day.to_dict("records"))
        result = day.set_index("symbol")["average_execution_price"].reindex(symbols)
    else:
        raise ValueError(f"Unknown execution mode: {mode}")
    result = pd.to_numeric(result, errors="coerce").astype(float)
    return result.where(np.isfinite(result) & (result > 0)).rename("execution_price")


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
