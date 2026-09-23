"""Fail-closed coverage audit for a 2010–2024 x0352 annual replay.

This tool checks whether the frozen strategy has the historical inputs needed
for a full-year replay. It never invents annual returns from missing data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
AUDIT = ROOT / "reports/x0352_2010_2024_coverage.json"
REPORT = ROOT / "reports/x0352_2010_2024_report.md"
FINAL_CONFIG = ROOT / "outputs/v2_double_check_structural/official_ex_post/final/structural_selected/config.json"
SOURCES = {
    "historical_daily": "data/v2/market_daily.csv",
    "official_ex_post_daily": "data/tuning_2nd/official_universe/processed/daily.csv",
    "historical_hourly": "data/extended/processed/hourly_canonical.csv",
    "official_ex_post_hourly": "data/tuning_2nd/official_universe/processed/hourly.csv",
    "corporate_actions": "data/extended/processed/official_corporate_actions.csv",
}
PINNED = (
    "outputs/v2_double_check_structural/audit.json",
    "outputs/v2_double_check_structural/selection.json",
    "outputs/v2_double_check_structural/official_ex_post/final/structural_selected/config.json",
    "data/extended/processed/universe_20241231.csv",
    "data/tuning_2nd/official_universe/processed/universe.csv",
)
YEARS = tuple(range(2010, 2025))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def scan_dates(path):
    first = last = None
    rows = 0
    sessions = {year: set() for year in YEARS}
    with Path(path).open(newline="") as stream:
        reader = csv.DictReader(stream)
        if "date" not in (reader.fieldnames or ()):
            raise ValueError("Missing date column: " + str(path))
        for row in reader:
            date = row["date"]
            if len(date) != 10 or date[4] != "-" or date[7] != "-":
                raise ValueError("Invalid date in " + str(path))
            first = date if first is None or date < first else first
            last = date if last is None or date > last else last
            rows += 1
            year = int(date[:4])
            if year in sessions:
                sessions[year].add(date)
    if not rows:
        raise ValueError("Empty source: " + str(path))
    return dict(first=first, last=last, rows=rows,
                sessions={str(year): len(days) for year, days in sessions.items()})


def build():
    from scripts.verify_double_check_structural import verify_release

    release = verify_release(ROOT / "outputs/v2_double_check_structural")
    selected = read(ROOT / "outputs/v2_double_check_structural/selection.json")
    config = read(FINAL_CONFIG)
    if (release.get("status") != "PASS" or release.get("selected_candidate") != "x0352"
            or release.get("submission_status") != "BLOCK_SUBMISSION"
            or selected.get("candidate_id") != "x0352"
            or config.get("full_tuning_params") != selected.get("params")
            or config.get("warmup_sessions") != 200
            or config.get("four_hour_mode") != "coverage_only"
            or config.get("match_4h_coverage") is not True):
        raise ValueError("Frozen x0352 release or its data requirements changed")
    coverage = {name: scan_dates(ROOT / relative) for name, relative in SOURCES.items()}
    if (coverage["official_ex_post_daily"]["first"] != "2024-01-02"
            or coverage["official_ex_post_hourly"]["first"] != "2024-10-01"
            or coverage["historical_hourly"]["sessions"]["2024"] != 62
            or coverage["official_ex_post_hourly"]["sessions"]["2024"] != 62):
        raise ValueError("Official or historical data coverage changed; reassess annual replay")
    historical_universe = list(csv.DictReader(
        (ROOT / "data/extended/processed/universe_20241231.csv").open(newline="")))
    official_universe = list(csv.DictReader(
        (ROOT / "data/tuning_2nd/official_universe/processed/universe.csv").open(newline="")))
    historical_as_of = sorted({row["as_of"] for row in historical_universe})
    historical_known = sorted({row["known_at_assumption"] for row in historical_universe})
    official_known = sorted({row["known_at"] for row in official_universe})
    if not historical_as_of or not historical_known or not official_known:
        raise ValueError("Universe availability evidence missing")
    if (min(historical_known) < "2024-12-31"
            or min(official_known) < "2026-01-01"):
        raise ValueError("Universe availability changed; reassess annual replay")
    years = []
    for year in YEARS:
        key = str(year)
        daily_count = coverage["historical_daily"]["sessions"][key]
        hourly_count = coverage["historical_hourly"]["sessions"][key]
        if year < 2024:
            reasons = ["NO_DAILY_MARKET_DATA", "NO_FOUR_HOUR_DATA", "NO_AS_OF_UNIVERSE"]
            if daily_count or hourly_count:
                raise ValueError("Historical source changed; full backtest is required")
        else:
            reasons = ["NO_PRE_2024_200_SESSION_WARMUP", "FOUR_HOUR_DATA_STARTS_2024_10_01",
                       "HISTORICAL_UNIVERSE_KNOWN_AFTER_2024_TRADING"]
            if (coverage["historical_daily"]["first"] != "2024-01-02"
                    or coverage["historical_hourly"]["first"] != "2024-10-01"
                    or min(historical_as_of) != "2024-12-31"):
                raise ValueError("2024 coverage changed; full backtest is required")
        years.append(dict(year=year, annual_return=None, status="UNVERIFIABLE",
                          daily_sessions=daily_count, hourly_sessions=hourly_count,
                          reasons=reasons))
    input_paths = sorted(set(SOURCES.values()) | set(PINNED) | {
        "scripts/audit_x0352_history_coverage.py"})
    return dict(status="BLOCKED_INSUFFICIENT_HISTORICAL_DATA",
                scope="2010_2024_COVERAGE_ONLY_NO_ANNUAL_RETURNS",
                selected_candidate="x0352", formal_submission="BLOCK_SUBMISSION",
                strategy_warmup_sessions=200, requires_observed_four_hour_data=True,
                historical_universe_as_of=historical_as_of,
                historical_universe_known_at=historical_known,
                official_universe_known_at=official_known,
                coverage=coverage, years=years,
                input_sha256={name: sha(ROOT / name) for name in input_paths})


def report_text(audit):
    coverage = audit["coverage"]
    lines = [
        "# x0352：2010–2024 逐年報酬查核", "",
        "**結果：目前無法計算 2010–2024 任一年的完整策略報酬。** 這是固定 `x0352` "
        "參數與原交易規則的資料覆蓋檢查，不是回測績效。`309.16%` 是 2025–2026 已用於選參的"
        "完整期間帳面報酬，不能推成單年或更早年份報酬。", "",
        "| 年度 | 年投報率 | 判定 | 主要缺口 |", "|---:|---:|---|---|",
    ]
    for row in audit["years"]:
        gap = ("缺當年日線、四小時資料與當時可知股票池" if row["year"] < 2024
               else "缺前一年暖機、1–9 月四小時資料；股票池年底才可知")
        lines.append(f"| {row['year']} | — | 無法驗證 | {gap} |")
    lines += ["", "**結論：** 不能判定 x0352 在這 15 年是否每年都高報酬。不能以 0%、"
              "2025–2026 報酬或替代策略填入空格。", "",
              "**注意事項：** 本地兩套日線均從 "
              f"{coverage['historical_daily']['first']} 開始，兩套四小時資料均從 "
              f"{coverage['historical_hourly']['first']} 開始；2024 年只有 "
              f"{coverage['historical_hourly']['sessions']['2024']} 個四小時資料交易日。"
              "x0352 需要 200 個交易日暖機與真實四小時觀測；歷史股票池到 2024-12-31 收盤後才可建，"
              "競賽名單則到 2026 年才可知。沿用未來名單會造成前視偏誤。正式 Active Share、官方帳本"
              "與平台收件也仍缺證據，正式 `plan` 保持 `BLOCK_SUBMISSION`。", "",
              "證據與來源雜湊見[覆蓋稽核](x0352_2010_2024_coverage.json)。"
              "證交所[公開個股日成交資料](https://www.twse.com.tw/zh/trading/historical/stock-day.html)"
              "自 2010 年提供，但日線不足以取代原策略所需的四小時資料；"
              "[歷史盤中資料產品](https://eshop.twse.com.tw/en/product/detail/000000006795e6940167a19f47a10046)"
              "另有取得條件。要做逐年回測，仍須補足逐年當時可知股票池、公司行動與 200 日暖機，"
              "再以同一帳本及每日門檻逐年獨立稽核。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    audit = build()
    rendered = report_text(audit)
    if args.write:
        if AUDIT.exists() or REPORT.exists():
            raise FileExistsError("Preserve existing history coverage result")
        AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        REPORT.write_text(rendered)
    else:
        if read(AUDIT) != audit or REPORT.read_text() != rendered:
            raise ValueError("Saved history coverage audit/report differs from source evidence")
    print(json.dumps(dict(status=audit["status"], years=len(audit["years"]),
                          annual_returns_available=0, formal_submission="BLOCK_SUBMISSION")))


if __name__ == "__main__":
    main()
