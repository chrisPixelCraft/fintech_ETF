"""Render the retained strategy comparisons; --verify checks exact saved outputs.

This checks retained receipts, ledger arithmetic and comparison membership. It
does not reconstruct the discarded parameter search or independently replay trades.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPARISONS = ROOT / "outputs/comparisons"
TRACKS = ("official_ex_post", "historical_pit")
CANDIDATES = ("x0352", "x0454", "mx0010")
ZERO_COUNTS = (
    "measured_hard_breach_days", "no_valid_plan_days", "unfilled_orders",
    "simulated_warning_days", "stale_held_price_days", "hold_without_envelope_days",
    "execution_price_bound_breaches", "raw_rule_breach_days",
)
EPISODE_FIELDS = (
    "track candidate_id episode kind start end total_return max_drawdown "
    "annualized_volatility sharpe_zero_rf economic_total_return economic_max_drawdown "
    "economic_annualized_volatility economic_sharpe_zero_rf final_nav sessions "
    "transaction_costs commission_cost sell_tax_cost turnover_two_way "
    "max_daily_volume_participation trades simulated_warning_days disqualified "
    "complete_period rejected_trade_count measured_hard_breach_days raw_rule_breach_days "
    "cash_breach_days holding_count_breach_days active_cap_breach_days overdue_passive_cap_days "
    "stale_held_price_days execution_price_bound_breaches unfilled_orders no_valid_plan_days "
    "hold_without_envelope_days official_compliance independent_audit research_eligible"
).split()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def read(path):
    return json.loads(path.read_text())


def rows(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def same_number(actual, expected, label):
    require(math.isfinite(float(actual)) and math.isfinite(float(expected))
            and math.isclose(float(actual), float(expected), rel_tol=1e-10, abs_tol=1e-8), label)


def authenticate(folder):
    receipt = read(folder / "receipt.json")
    require({"config.json", "equity.csv", "metrics.json", "independent_audit.json"}
            <= set(receipt), f"Incomplete receipt: {folder}")
    for name, digest in receipt.items():
        require(Path(name).name == name, f"Invalid receipt filename: {name}")
        require(hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest,
                f"Receipt mismatch: {folder / name}")


def equity(folder):
    frame = rows(folder / "equity.csv")
    dates = [r["date"] for r in frame]
    require(dates and dates == sorted(set(dates)), f"Invalid dates: {folder}")
    for row in frame:
        for field in ("nav", "economic_nav"):
            require(math.isfinite(float(row[field])) and float(row[field]) > 0,
                    f"Invalid NAV: {folder}")
    return frame


def qualified(audit):
    return (audit["independent_audit"] == "PASS"
            and audit["complete_period"] is True and audit["disqualified"] is False
            and all(audit[k] == 0 for k in ZERO_COUNTS))


def csv_text(records, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue()


def collect():
    best = ROOT / "outputs/best_v2/official_ex_post"
    authenticate(best)
    calendar = [r["date"] for r in equity(best)]
    require(len(calendar) == 417 and calendar[0] == "2025-01-02"
            and calendar[-1] == "2026-09-21", "Full-period calendar differs")
    annual, totals = [], {}
    for model in ("x0352", "v1", "0050"):
        folder = best if model == "x0352" else COMPARISONS / "annual" / model
        frame = equity(folder)
        require([r["date"] for r in frame] == calendar, f"Annual calendar differs: {model}")
        initial = read(folder / "config.json")["initial_cash"]
        require(initial == 1e9, "Initial capital differs")
        year_end = float([r for r in frame if r["date"].startswith("2025-")][-1]["economic_nav"])
        annual.extend((dict(model=model, period="2025", return_economic=year_end / initial - 1),
                       dict(model=model, period="2026_YTD",
                            return_economic=float(frame[-1]["economic_nav"]) / year_end - 1)))
        totals[model] = float(frame[-1]["nav"]) / initial - 1
        same_number(totals[model], read(folder / "metrics.json")["total_return"],
                    f"Total return differs: {model}")

    windows = {"full": ("continuous", calendar[0], calendar[-1]),
               "contest_2025": ("reset_calendar_analogue", "2025-10-26", "2025-11-27")}
    for month in [f"2025-{m:02}" for m in range(1, 13)] + [f"2026-{m:02}" for m in range(1, 9)]:
        index = next(i for i, day in enumerate(calendar) if day.startswith(month))
        windows["m25_" + month] = ("reset_25_sessions", calendar[index], calendar[index + 24])
    expected = {f"{t}/{c}/{e}/receipt.json" for t in TRACKS for c in CANDIDATES for e in windows}
    actual = {str(p.relative_to(COMPARISONS / "monthly"))
              for p in (COMPARISONS / "monthly").rglob("receipt.json")}
    require(actual == expected, "Missing or unexpected retained monthly run")
    episodes = []
    for track in TRACKS:
        for cid in CANDIDATES:
            params = None
            for episode in sorted(windows):
                folder = COMPARISONS / "monthly" / track / cid / episode
                authenticate(folder)
                cfg, audit = read(folder / "config.json"), read(folder / "independent_audit.json")
                kind, start, end = windows[episode]
                require(cfg["tuning_candidate_id"] == cid and cfg["universe_mode"] == track,
                        f"Candidate identity differs: {folder}")
                require(cfg["start"] == start and cfg["end"] == end and cfg["initial_cash"] == 1e9,
                        f"Window config differs: {folder}")
                if params is None:
                    params = cfg["full_tuning_params"]
                require(cfg["full_tuning_params"] == params, f"Parameters changed by window: {folder}")
                frame = equity(folder)
                intended = [d for d in calendar if start <= d <= end]
                actual_dates = [r["date"] for r in frame]
                require(actual_dates == intended[:len(actual_dates)]
                        and (actual_dates == intended or audit["disqualified"] is True),
                        f"Window calendar differs: {folder}")
                require(audit["complete_period"] == (actual_dates == intended),
                        f"Window completeness differs: {folder}")
                require(audit["sessions"] == len(frame) and audit["independent_audit"] == "PASS"
                        and audit["official_compliance"] == "UNKNOWN_BLOCK_SUBMISSION",
                        f"Audit identity differs: {folder}")
                same_number(audit["total_return"], float(frame[-1]["nav"]) / 1e9 - 1,
                            f"Book return differs: {folder}")
                same_number(audit["economic_total_return"], float(frame[-1]["economic_nav"]) / 1e9 - 1,
                            f"Economic return differs: {folder}")
                record = dict(track=track, candidate_id=cid, episode=episode, kind=kind,
                              start=start, end=end, **audit, research_eligible=qualified(audit))
                episodes.append({key: record[key] for key in EPISODE_FIELDS})
    return annual, totals, episodes


def render(annual, totals, episodes):
    pct = lambda n: f"{n:.2%}"
    get = lambda t, c, e: next(r for r in episodes
                              if (r["track"], r["candidate_id"], r["episode"]) == (t, c, e))
    show = lambda r: pct(r["economic_total_return"]) if r["research_eligible"] else "不合格"
    lines = ["# v2 x0352 與保留對照", "",
        f"**保留主策略為 `x0352`：2025-01-02–2026-09-21 帳面總報酬 {pct(totals['x0352'])}。** "
        "這是開發期回測，正式提交仍為 `BLOCK_SUBMISSION`。月度對照未提供足以替換主策略的證據。", "",
        "## 全期與年度比較", "",
        "以下使用官方事後股票池，417 個交易日、起始本金 10 億元。年度欄採 `economic_nav`；"
        "2026 年只到 9 月 21 日，並非全年或年化報酬。", "",
        "| 策略 | 全期帳面報酬 | 2025 年 | 2026 年至 9/21 |",
        "|---|---:|---:|---:|"]
    for cid in ("x0352", "v1", "0050"):
        values = [r["return_economic"] for r in annual if r["model"] == cid]
        lines.append(f"| `{cid}` | {pct(totals[cid])} | {pct(values[0])} | {pct(values[1])} |")
    lines += ["", "v1 有已量測違規；0050 為單一 ETF，不符合競賽個股及持股檔數限制，均僅供研究對照。"
        "年度報酬以年末經濟淨值除以前期年末淨值計算，2025 年以前述起始本金為基準。", "",
        "[年度 CSV](../outputs/comparisons/annual.csv) · "
        "[x0352 帳本](../outputs/best_v2/official_ex_post/equity.csv) · "
        "[v1 帳本](../outputs/comparisons/annual/v1/equity.csv) · "
        "[0050 帳本](../outputs/comparisons/annual/0050/equity.csv)", "",
        "## 25 交易日重新建倉比較", "",
        "三組參數各自固定，每窗從 10 億元與空持倉開始，保留之前的暖機資料。起點為各月首個交易日，"
        "結束日可能跨月；窗口可能重疊。下表為官方事後池的經濟報酬，只對完整、未失格且八項已量測門檻全零的窗口顯示數值。", "",
        "| 月初起點 | `x0352` | `x0454` | `mx0010` |", "|---|---:|---:|---:|"]
    for episode in sorted({r["episode"] for r in episodes if r["kind"] == "reset_25_sessions"}):
        lines.append("| " + episode[4:] + " | " + " | ".join(
            show(get("official_ex_post", cid, episode)) for cid in CANDIDATES) + " |")
    lines += ["", "未合格窗口保留於分母及 [132 筆逐窗 CSV](../outputs/comparisons/monthly.csv)。"
        "這些是重新建倉結果，與全年持續持倉的總報酬回答不同問題。", "",
        "| 股票池 | 策略 | 冷啟動合格窗 | 全期研究門檻 | 2025 賽期類比 |",
        "|---|---|---:|---|---:|"]
    for track, label in (("official_ex_post", "官方事後"), ("historical_pit", "歷史股票池")):
        for cid in CANDIDATES:
            resets = [r for r in episodes if r["track"] == track and r["candidate_id"] == cid
                      and r["kind"] == "reset_25_sessions"]
            full = get(track, cid, "full")
            lines.append(f"| {label} | `{cid}` | {sum(r['research_eligible'] for r in resets)}/20 | "
                         f"{'通過' if full['research_eligible'] else '不合格'} | "
                         f"{show(get(track, cid, 'contest_2025'))} |")
    lines += ["", "賽期類比只回放 2025-10-26–11-27，實際首個交易日為 10/27；"
        "2026 正式賽期尚未發生。x0454 的歷史池全期回放未通過；mx0010 的兩池全期回放均未通過，"
        "不能只憑較多冷啟動合格窗口認定它們優於 x0352。", "",
        "## 證據範圍與限制", "",
        "本報告由保留的帳本、設定及原始單次稽核收據重建，包含失敗窗口。"
        "驗證命令核對收據、日期、淨值計算、窗口成員及報表一致性；不重新執行已刪除的完整參數搜尋，"
        "也不重新證明原搜尋排名或全域最佳。x0352 是先前選出而保留的主策略。", "",
        "兩池與日期均參與過開發，並非樣本外測試。官方事後股票池存在前視偏誤；"
        "模擬成交未計滑價或市場衝擊，且有成交量超過當日市場成交量的情況，不能視為可實現收益。"
        "Active Share、正式結算與平台收件仍缺證據；帳本稽核通過不等於官方全部規則通過。", "",
        "2010–2024 沒有可驗證的完整策略比較：原始日內觀測僅始於 2024 年晚期，"
        "早年價格與當時可知股票池亦不完整。此處不補造歷史報酬。", "",
        "重建：`python scripts/report_best_v2.py`；核對：`python scripts/report_best_v2.py --verify`。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    annual, totals, episodes = collect()
    artifacts = {
        COMPARISONS / "annual.csv": csv_text(annual, ["model", "period", "return_economic"]),
        COMPARISONS / "monthly.csv": csv_text(episodes, EPISODE_FIELDS),
        ROOT / "reports/comparison.md": render(annual, totals, episodes),
    }
    for path, content in artifacts.items():
        if args.verify:
            require(path.read_text() == content, f"Saved comparison differs: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    print(json.dumps(dict(status="PASS", annual_rows=len(annual), retained_monthly_runs=len(episodes),
                          submission_status="BLOCK_SUBMISSION", mode="verify" if args.verify else "write")))


if __name__ == "__main__":
    main()
