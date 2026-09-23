"""Build the third-round research report from complete saved trial ledgers."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from src.double_check_tuning import ZERO_COUNTS, choose, eligible

OUTPUT = ROOT / "outputs/v2_double_check_structural"
PARENT = ROOT / "outputs/v2_double_check_refinement"
REPORT = ROOT / "reports/v2_double_check_structural_report.md"
STUDY = ROOT / "config/v2_double_check_structural.json"
TRACKS = ("official_ex_post", "historical_pit")
FINAL = "structural_selected"


def read(path):
    return json.loads(Path(path).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pct(value):
    return f"{float(value):.2%}"


def annual(equity_path):
    equity = pd.read_csv(equity_path, usecols=["date", "economic_nav"])
    require(len(equity) == 417 and equity.date.iloc[0] == "2025-01-02"
            and equity.date.iloc[-1] == "2026-09-21", "Unexpected annual period")
    nav_2025 = float(equity.loc[equity.date.str.startswith("2025"), "economic_nav"].iloc[-1])
    nav_2026 = float(equity.economic_nav.iloc[-1])
    require(nav_2025 > 0 and nav_2026 > 0, "Invalid economic NAV")
    return nav_2025 / 1e9 - 1, nav_2026 / nav_2025 - 1


def checked_final(root, track, selected):
    folder = root / track / "final" / FINAL
    receipt = read(folder / "receipt.json")
    require({"config.json", "metrics.json", "equity.csv", "independent_audit.json"} <= set(receipt),
            "Selected final receipt incomplete")
    for name, digest in receipt.items():
        require(sha(folder / name) == digest, "Selected final receipt mismatch: " + track + "/" + name)
    config, metrics, audit = (read(folder / name) for name in
                              ("config.json", "metrics.json", "independent_audit.json"))
    require(config.get("full_tuning_params") == selected["params"], "Final parameters differ")
    require(metrics.get("official_compliance") == "UNKNOWN_BLOCK_SUBMISSION"
            and audit.get("independent_audit") == "PASS", "Final research audit/official scope differs")
    return folder, metrics


def make_report(root=OUTPUT, report=REPORT):
    root, report = Path(root), Path(report)
    require(not report.exists(), "Preserve existing report: " + str(report))
    require(not (root / "failure.json").exists(), "Refinement has failure marker")
    manifest, study = read(root / "manifest.json"), read(STUDY)
    audit, selected, grid = (read(root / name) for name in
                             ("study_audit.json", "selection.json", "grid.json"))
    parent_audit = read(PARENT / "audit.json")
    require(manifest.get("outputs_complete") is True and manifest.get("study") == study
            and manifest.get("study_sha256") == sha(STUDY), "Study incomplete or design differs")
    require(parent_audit.get("status") == "PASS"
            and sha(PARENT / "audit.json") == study["parent_audit_sha256"], "Parent audit differs")
    require(audit.get("status") == "PASS" and audit.get("verified_trials") == 160
            and audit.get("submission_status") == "BLOCK_SUBMISSION", "Producer trial audit incomplete")
    require(manifest.get("completed_trials") == manifest.get("expected_trials") == 160,
            "Incomplete trial count")
    require(len(grid.get("grid", [])) == grid.get("raw_grid_combinations") == 81
            and grid.get("reused_parent_count") == 1
            and grid.get("new_candidate_count") == 80, "Grid differs")
    require(selected.get("selected_on") == "official_ex_post"
            and selected.get("scope") == "EX_POST_DEVELOPMENT_ONLY"
            and selected.get("official_compliance") == "UNKNOWN_BLOCK_SUBMISSION"
            and selected.get("submission_status") == "BLOCK_SUBMISSION", "Selection scope differs")

    trials = pd.read_csv(root / "trials.csv")
    require(len(trials) == 160 and set(trials.track) == set(TRACKS), "Trial table incomplete")
    by_track = {}
    for track in TRACKS:
        rows = trials.loc[trials.track.eq(track)].to_dict("records")
        require(len(rows) == len({r["candidate_id"] for r in rows}) == 80,
                "Duplicate or missing trial")
        require(all(r["status"] == "COMPLETE" and eligible(r) == bool(r["eligible"])
                    and bool(r["research_eligible"]) == bool(r["eligible"]) for r in rows),
                "Eligibility/completion mismatch")
        by_track[track] = rows
    parent_folder = PARENT / "official_ex_post/final/refinement_selected"
    parent_metrics = read(parent_folder / "metrics.json")
    parent_row = dict(parent_metrics, status="COMPLETE", candidate_id="x0352", track="official_ex_post")
    require(eligible(parent_row), "Audited parent fails strict research gate")
    winner = choose([*by_track["official_ex_post"], parent_row])
    require(winner is not None and winner["candidate_id"] == selected["candidate_id"],
            "Selection differs from strict ranking")
    require(selected.get("source") == ("prior" if winner["candidate_id"] == "x0352" else "new"),
            "Selection source differs")
    final = {track: checked_final(root, track, selected) for track in TRACKS}
    require(all(audit["final"][track].get("independent_audit") == "PASS" for track in TRACKS),
            "Final independent replay audit incomplete")

    controls = {
        "本輪獲選 v2": annual(final["official_ex_post"][0] / "equity.csv"),
        "原 x0352": annual(parent_folder / "equity.csv"),
        "v1": annual(ROOT / "outputs/full_tuned_v2/official_ex_post/final/v1_matched/equity.csv"),
        "0050": annual(ROOT / "outputs/full_tuned_v2/official_ex_post/final/0050/equity.csv"),
    }
    passed = sorted((r for r in by_track["official_ex_post"] if eligible(r)),
                    key=lambda r: (-r["total_return"], r["max_drawdown"],
                                   r["turnover_two_way"], r["candidate_id"]))
    lines = [
        "# v2 double-check：第四輪局部結構搜尋結果", "",
        "**研究候選；正式 policy 仍為 `BLOCK_SUBMISSION`。** 獨立發布稽核結果見"
        "[稽核檔](../outputs/v2_double_check_structural/audit.json)。Active Share、官方帳本與平台收件"
        "證據不能靠回測補足。", "",
        f"本輪調整目標持股數、現金緩衝、報酬觀察窗和 EMA 觀察窗，完整覆蓋四軸 81 個位置；"
        f"1 組沿用已稽核的 `x0352`，80 組新設定各在兩池回放，"
        f"共 160 次新試驗。連同前三輪累計 1,569 組不同設定。官方事後池有 {len(passed)}／80 "
        f"組新候選通過已量測門檻。最終選擇 `{selected['candidate_id']}`"
        f"（{'新候選' if selected['source'] == 'new' else '維持原 x0352'}）。", "",
        "## 與原候選比較", "",
        "| 股票池 | 候選 | 帳面總報酬 | 最大回撤 | 已量測門檻合格 | 超過當日總成交量的成交 | 最大成交量參與率 |",
        "|---|---|---:|---:|---|---:|---:|",
    ]
    challenger = selected["candidate_id"] if selected["source"] == "new" else (
        passed[0]["candidate_id"] if passed else None)
    for track in TRACKS:
        old = read(PARENT / track / "final/refinement_selected/metrics.json")
        rows = [("原 x0352", old)]
        if challenger is not None:
            new = (final[track][1] if selected["source"] == "new" else
                   next(row for row in by_track[track] if row["candidate_id"] == challenger))
            rows.append((challenger, new))
        for label, row in rows:
            test = dict(row, candidate_id=label, status="COMPLETE")
            lines.append(f"| {track} | `{label}` | {pct(row['total_return'])} | "
                         f"{pct(row['max_drawdown'])} | {'是' if eligible(test) else '否'} | "
                         f"{int(row['trades_above_100pct_daily_volume'])} | "
                         f"{float(row['max_daily_volume_participation']):.2f}× |")
    if selected["source"] == "prior" and challenger is not None:
        lines.append("\n比較表中的新候選是本輪官方事後池報酬最高的合格新設定；它未勝過 `x0352`，因此未換參。")
    lines += ["", "僅官方事後池決定參數，歷史池原樣重播。成交量參與率不是官方明定門檻，"
              "但超過當日總量的模擬成交不能視為真實可成交收益。", "",
              "## 年度經濟淨值比較", "",
              "依各日 `economic_nav` 計算；2026 年截至 9 月 21 日，並非全年或年化報酬。", "",
              "| 期間 | 本輪獲選 v2 | 原 x0352 | v1 | 0050 |",
              "|---|---:|---:|---:|---:|"]
    for index, period in enumerate(("2025 年", "2026 年截至 9 月 21 日")):
        lines.append("| " + period + " | " + " | ".join(pct(values[index]) for values in controls.values()) + " |")
    lines += ["", "v2 與 v1 使用官方事後股票池，0050 是同期間 ETF 基準。v1 有已量測違規，"
              "0050 不符合競賽個股與持股檔數要求；兩者只供研究對照。", "",
              "## 新候選合格表", "",
              "| 排名 | 候選 | 報酬 | 最大回撤 | 雙邊換手率 |", "|---:|---|---:|---:|---:|"]
    for rank, row in enumerate(passed[:10], 1):
        lines.append(f"| {rank} | `{row['candidate_id']}` | {pct(row['total_return'])} | "
                     f"{pct(row['max_drawdown'])} | {float(row['turnover_two_way']):.2f} |")
    if not passed:
        lines.append("| — | 無新合格候選 | — | — | — |")
    lines += ["", "不合格原因可重疊。官方事後池新候選的逐項問題數：", "",
              "| 已量測門檻 | 有問題候選數 |", "|---|---:|"]
    for field in ZERO_COUNTS:
        lines.append(f"| `{field}` | {sum(float(row[field]) > 0 for row in by_track['official_ex_post'])} |")
    lines += ["", "## 獲選參數與解讀", "",
              "| 本輪調整軸 | 獲選值 |", "|---|---:|"]
    for axis in study["axes"]:
        value = ([selected["params"]["return_short"], selected["params"]["return_long"]]
                 if axis == "return_pair" else
                 [selected["params"]["ema_fast"], selected["params"]["ema_slow"]]
                 if axis == "ema_pair" else selected["params"][axis])
        lines.append(f"| `{axis}` | {value} |")
    lines += ["", "[四軸結構搜尋規格](../docs/v2_double_check_structural_protocol.md)以外的參數固定於 `x0352`。"
              "本輪維持八項零計數、417 個交易日完整性及無失格條件；不合格的高報酬組合不入選。", "",
              "2025-01-02～2026-09-21 的 417 日皆已參與開發。2026 年公布的官方股票池回填至 2025 年，"
              "含成分股前視偏誤；歷史池也非未見測試。重複選參進一步增加過度擬合風險。"
              "均價成交模型沒有滑價與市場衝擊，帳面報酬不可當作實際可成交報酬。", "",
              "[試驗表](../outputs/v2_double_check_structural/trials.csv) · "
              "[網格位置](../outputs/v2_double_check_structural/grid.json) · "
              "[獲選設定](../outputs/v2_double_check_structural/selection.json) · "
              "[月度淨值](../outputs/v2_double_check_structural/monthly.csv) · "
              "[逐條規則](../docs/v2_double_check_rules.md)", "",
    ]
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x") as stream:
        stream.write("\n".join(lines))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    print(make_report(args.output, args.report))


if __name__ == "__main__":
    main()
