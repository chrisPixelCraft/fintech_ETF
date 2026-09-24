#!/usr/bin/env python3
"""Build the v3 parameter-expansion report from independently verified rows."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_24d import aggregate, file_hash

PERIODS = {
    "development": "開發：2010–2018",
    "validation": "驗證：2019–2022",
    "holdout": "凍結後歷史評估：2023–2024",
    "recent": "近期壓力：2025–2026/09",
    "seasonal": "10–11 月類比：2010–2025",
}


def read(path):
    return json.loads(Path(path).read_text())


def csv(path):
    return pd.read_csv(path, float_precision="round_trip")


def pct(value):
    return "—" if pd.isna(value) else f"{100 * value:.2f}%"


def comparison(out, labels):
    """Keep failures in denominators; additionally compare the same passed cells."""
    records, common_records = [], []
    for phase in PERIODS:
        rows = csv(out / f"{phase}.csv")
        summaries = aggregate(rows).set_index("candidate_id")
        expected = set(labels.values())
        if not expected.issubset(summaries.index):
            raise ValueError(f"Missing comparison candidates: {phase}")
        passed_sets = []
        for candidate in expected:
            group = rows[rows.candidate_id.eq(candidate)]
            accepted = group.measured_pass & group.complete_period & group.episode_return.notna()
            passed_sets.append(set(group.loc[accepted, "episode_id"]))
        common_ids = set.intersection(*passed_sets)
        for label, candidate in labels.items():
            records.append(dict(period=phase, strategy=label, candidate_id=candidate,
                                **summaries.loc[candidate].to_dict()))
            group = rows[rows.candidate_id.eq(candidate) & rows.episode_id.isin(common_ids)]
            common_records.append(dict(
                period=phase, strategy=label, candidate_id=candidate,
                common_passed=len(group), attempted=int(summaries.loc[candidate, "attempted"]),
                median_24d_return=group.episode_return.median(),
                p25_24d_return=group.episode_return.quantile(.25),
                p10_24d_return=group.episode_return.quantile(.10),
            ))
    table = pd.DataFrame(records)
    # Artificial penalty scores are not returns; omit them from human comparisons.
    table = table.drop(columns=[name for name in table if name.startswith("penalized_") or name == "failed_episode_penalty_return"])
    return table, pd.DataFrame(common_records)


def result_tables(table):
    lines = []
    for phase, title in PERIODS.items():
        lines += [f"**{title}**", "",
                  "| 策略 | Median 24D | P25 | P10 | Worst | Positive | Median MDD | 完整／全部 | 通過／全部 |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for row in table[table.period.eq(phase)].itertuples():
            lines.append(
                f"| {row.strategy} | {pct(row.median_24d_return)} | {pct(row.p25_24d_return)} "
                f"| {pct(row.p10_24d_return)} | {pct(row.worst_24d_return)} "
                f"| {pct(row.positive_episode_rate)} | {pct(row.median_mdd)} "
                f"| {int(row.completed)}/{int(row.attempted)} | {int(row.passed)}/{int(row.attempted)} |"
            )
        lines.append("")
    return lines


def render(out):
    # Imported at runtime so unit tests of population handling stay lightweight.
    from scripts.verify_24d_round3 import verify

    verified = verify(out)
    selection = read(out / "final_selection.json")
    labels = {
        "基準": "x0352_daily_baseline",
        "前輪開發代表": selection["prior_leader_id"],
        "本輪開發代表": selection["diagnostic_leader_id"],
    }
    if selection.get("candidate_id") and selection["candidate_id"] not in labels.values():
        labels["採用研究候選"] = selection["candidate_id"]
    table, common = comparison(out, labels)
    candidates = read(out / "candidates.json")
    lines = ["# v3：第三輪分層參數搜尋", "",
             f'本輪新增 **{len(candidates)} 組參數**，不重複前兩輪 {selection["previous_candidate_count"]} 組；另重跑兩組既有對照。結論為 **`{selection["status"]}`**；正式提交狀態為 **`{selection["formal_status"]}`**。開發期代表只是診斷對象，不等於合格候選。', "",
             "依[調參規格](../docs/v3_tuning.md)與[逐日規則](../docs/v2_double_check_rules.md)擴大搜尋，保留原始帳務、持股、現金、權重及整張限制。每個窗口從 10 億元、零持股開始，固定 24 個交易日；D−1 決策、次日開盤價近似成交，手續費雙邊各 0.1425%，賣出稅 0.3%。", "",
             "## 比較結果", "",
             "報酬、回撤與正報酬率只取完整且已量測通過的窗口。失敗保留於全部嘗試分母，失格後的部分淨值不當作 24 日報酬。不同策略通過窗口可能不同，不能只看條件式報酬判定優勝。", ""]
    lines += result_tables(table)
    val = table[table.period.eq("validation")].set_index("strategy")
    current, previous = val.loc["本輪開發代表"], val.loc["前輪開發代表"]
    lines += [f"本輪代表在驗證期通過 {int(current.passed)}/{int(current.attempted)}，前輪代表為 {int(previous.passed)}/{int(previous.attempted)}；條件式 median 分別為 {pct(current.median_24d_return)}、{pct(previous.median_24d_return)}。採用與否以全部門檻及凍結紀錄為準，不能只以這些條件式報酬宣稱取得可用的新最佳策略。", ""]
    lines += ["完整 mean、worst MDD、turnover、形成 20 檔持股的天數與通過率見[比較 CSV](24d_round3_comparison.csv)。", "",
              "**共同通過窗口的 Median 24D**", "",
              "| 期間 | 共同窗口 | " + " | ".join(labels) + " |",
              "|---|---:|" + "---:|" * len(labels)]
    for phase, title in PERIODS.items():
        group = common[common.period.eq(phase)].set_index("strategy")
        lines.append(f'| {title} | {int(group.iloc[0].common_passed)}/{int(group.iloc[0].attempted)} | '
                     + " | ".join(pct(group.loc[label, "median_24d_return"]) for label in labels) + " |")
    lines += ["", "共同樣本仍排除了失敗窗口，只能用於條件式診斷，不能代表全部起始日期的實際表現；[共同樣本 CSV](24d_round3_common.csv)保留 P25、P10 與分母。", "",
              "## 搜尋與採用", "",
              "開發期為 2010–2018，驗證期為 2019–2022。採用必須在完整開發及驗證期間達到 100% 已量測通過、100% 完整，並通過預先固定的報酬與穩定性條件。篩選失敗的參數可供診斷，不能因此放寬採用門檻。", ""]
    memberships = csv(out / "phase_memberships.csv")
    descriptions = {"uniform": "規格範圍均勻抽樣", "stratified_entry_replacement": "持股數 × 換股數分層"}
    lines += ["| 搜尋家族 | 新增參數 |", "|---|---:|"]
    for family, group in memberships.groupby("family", sort=False):
        lines.append(f"| {descriptions[family]} | {group.candidate_id.nunique()} |")
    lines += ["", "家族皆使用原規格範圍；調整進場篩選不放寬任何帳務或合規規則。固定 seed 為 2409202603，前輪對照僅由開發排名決定。分層覆蓋 5 種持股數 × 4 種換股數；其餘參數在原範圍抽樣。這是有界搜尋，並非窮舉全部參數，也不能由混合參數抽樣推論單一參數的因果效果。", "",
              "| 階段 | 參數組數 | 窗口嘗試 | 通過窗口 |", "|---|---:|---:|---:|"]
    for phase, label in [("baseline_reproduction", "基準重現"), ("bottleneck", "困難窗口"), ("screen", "共同 12 窗口"),
                         ("development", "完整開發"), ("validation", "完整驗證")]:
        rows = csv(out / f"{phase}.csv")
        lines.append(f"| {label} | {rows.candidate_id.nunique()} | {len(rows)} | {int(rows.measured_pass.sum())} |")
    hard = csv(out / "bottleneck.csv")
    new_ids = {candidate["candidate_id"] for candidate in candidates}
    new_hard = hard[hard.candidate_id.isin(new_ids)]
    lines += ["", f"先重現 154 個基準開發／驗證窗口，再以已知困難窗口 2015-08-03 篩選，新增參數通過 **{int(new_hard.measured_pass.sum())}/{len(new_hard)}**。單一失敗足以排除 100% 通過資格，但不能當作跨月份平均表現。依預定診斷預算，前 12 組新參數進入共同 12 個開發窗口，前 6 組再完整重跑 107 個開發與 47 個驗證窗口；兩組對照同場重跑。", "",
              f'停止原因：`{selection["stop_reason"]}`。凍結時間：`{selection["frozen_at"]}`。診斷代表由開發資料決定，保留期與近期結果不回流選參。', ""]
    if not hard.measured_pass.any():
        lines += [f"困難窗口全部不通過，條件式報酬統計沒有值；完整跑完者同分時，排序以接近基準程度與參數 ID 決定，不能稱作報酬前 12 名。後續六組只作有界診斷，可能未涵蓋條件式報酬更高的設定；但本輪 {len(candidates)} 組皆已在必要開發窗口失敗，沒有因此漏掉 100% 通過的合格設定。", ""]
    strata = memberships[memberships.family.eq("stratified_entry_replacement")].merge(
        new_hard[["candidate_id", "measured_pass", "complete_period", "raw_rule_breach_days"]],
        on="candidate_id", validate="one_to_one")
    strata["no_raw_breach"] = strata.raw_rule_breach_days.eq(0)
    coverage = strata.groupby(["target_count", "max_replacements_per_day"]).agg(
        attempted=("candidate_id", "size"), completed=("complete_period", "sum"),
        passed=("measured_pass", "sum"), no_raw_breach=("no_raw_breach", "sum")).reset_index()
    lines += ["**分層覆蓋：無原始超限／嘗試組數**", "",
              "| 目標持股數 | 不換股 | 最多 1 檔 | 最多 2 檔 | 最多 4 檔 |", "|---|---:|---:|---:|---:|"]
    for count in sorted(coverage.target_count.unique()):
        group = coverage[coverage.target_count.eq(count)].set_index("max_replacements_per_day")
        lines.append(f"| {count} | " + " | ".join(
            f"{int(group.loc[n, 'no_raw_breach'])}/{int(group.loc[n, 'attempted'])}" for n in [0, 1, 2, 4]) + " |")
    lines += ["", "此表只檢查困難月份的原始規則超限，未包含零股持倉等全部採用門檻；不能視為正式合規率。其餘参数也同時變動，不是單因子因果實驗。[分層明細](24d_round3_strata.csv)保存完整與通過組數。", ""]
    reasons = new_hard.failure_reasons.fillna("")
    round_only = int((reasons.eq("FAIL_ROUND_LOT") & new_hard.raw_rule_breach_days.eq(0)).sum())
    odd_holdings = 0
    for candidate in sorted(new_ids):
        folder = out / "ledgers" / "bottleneck" / candidate
        trades = pd.read_parquet(folder / "trades.parquet", columns=["shares"])
        holdings = pd.read_parquet(folder / "holdings.parquet", columns=["shares"])
        if not trades.shares.mod(1000).eq(0).all():
            raise ValueError(f"Unexpected odd-lot execution: {candidate}")
        odd_holdings += int(holdings.shares.mod(1000).ne(0).any())
    names = {"FAIL_ROUND_LOT": "僅零股持倉門檻", "FAIL_CASH;FAIL_HOLDING_COUNT;FAIL_ROUND_LOT": "現金、檔數、零股持倉",
             "FAIL_CASH;FAIL_HOLDING_COUNT": "現金、檔數", "": "無失敗原因"}
    lines += ["**困難窗口的失敗分類**", "", "| 失敗原因 | 新參數組數 |", "|---|---:|"]
    lines += [f"| {names.get(reason, reason)} | {count} |" for reason, count in reasons.value_counts().sort_index().items()]
    lines += ["",
              f"新增 {len(new_hard)} 組中，{int(new_hard.complete_period.sum())} 組跑完窗口，{round_only} 組沒有原始現金／檔數／權重等超限日，但仍未通過保守的零股持倉門檻。逐筆帳本中，全部成交股數皆為 1,000 股倍數；{odd_holdings} 組有零股持倉殘餘。公司行動後的零股持倉，不等於送出了零股委託；官方處理契約未明，這輪保留不通過判定。", "",
              "部分設定能避開此窗口的現金／檔數超限，仍不足以解決公司行動與持倉規則的證據缺口；不能由單窗結果聲稱找到了全面最佳策略。後續應先核對官方公司行動處理契約，再決定是否值得擴大參數搜尋。[逐窗口結果](../outputs/24d_round3/bottleneck.csv)保留各類原因，表格依完整原因組合分類，每組參數只計一次。", "",
              "| 對照 | 參數設定 |", "|---|---|"]
    for label, candidate in labels.items():
        lines.append(f"| {label} | [`{candidate}`](../outputs/24d_round3/configs/{candidate}.json) |")
    lines += ["", "[凍結紀錄](../outputs/24d_round3/final_selection.json)與[搜尋設定](../config/24d_round3_study.json)保存搜尋邊界、採用條件與來源；[驗證排名](../outputs/24d_round3/validation_ranking.csv)保留全部入圍結果。", "",
              "## 近期滾動壓力", "",
              "| 策略 | Median 24D | P25 | P10 | Worst | 通過／全部 |", "|---|---:|---:|---:|---:|---:|"]
    rolling = aggregate(csv(out / "rolling_recent.csv")).set_index("candidate_id")
    for label, candidate in labels.items():
        if candidate not in rolling.index:
            continue
        row = rolling.loc[candidate]
        lines.append(f"| {label} | {pct(row.median_24d_return)} | {pct(row.p25_24d_return)} | {pct(row.p10_24d_return)} | {pct(row.worst_24d_return)} | {int(row.passed)}/{int(row.attempted)} |")
    lines += ["", "最近 126 個完整滾動起點僅供凍結後壓力測試，重疊窗口不是獨立樣本，報酬仍只計完整且通過窗口。", "",
              "## 注意事項", "",
              "2023–2024 與近期資料已在前輪研究被觀察；本輪只做選參隔離，不能聲稱首次未見 holdout。重複研究同一驗證期也有適應性選擇偏誤；基準沿用後期選出的參數，亦非當年的事前策略。2026 年白名單回套歷史另有存活與成分前視偏誤。", "",
              "Yahoo 資料截至 2026-09-23，9 月月初尚無完整 24 日窗口。Active Share、官方結算與公司行動處理仍缺官方證據；日線開盤價與無市場衝擊的 10 億元成交只是研究假設。因此本地帳務稽核通過不等於正式比賽每日合規，仍禁止提交。原 309.16% v2 的固定參數與歷史結果保留。", "",
              "## 重現", "", "核對本次封存結果：", "", "```bash",
              "python scripts/verify_24d_round3.py --rebuild",
              "python scripts/report_24d_round3.py --verify", "```", "",
              "使用相同凍結資料另建輸出重跑，再驗證該輸出：", "", "```bash",
              "python scripts/expand_24d_round3.py --workers 4 --output outputs/24d_round3_replay",
              "python scripts/verify_24d_round3.py --output outputs/24d_round3_replay --rebuild", "```", "",
              f'已驗證 {verified["groups"]} 組帳本、{verified["attempts"]} 次窗口嘗試。詳見[驗證紀錄](../outputs/24d_round3/verification.json)與[SHA256 清單](../outputs/24d_round3/result_manifest.json)。', ""]
    return "\n".join(lines), table, common, coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/24d_round3")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    prose, table, common, coverage = render(args.output)
    targets = {
        ROOT / "reports/24d_round3.md": prose,
        ROOT / "reports/24d_round3_comparison.csv": table.to_csv(index=False),
        ROOT / "reports/24d_round3_common.csv": common.to_csv(index=False),
        ROOT / "reports/24d_round3_strata.csv": coverage.to_csv(index=False),
    }
    for path, content in targets.items():
        if args.verify:
            if path.read_text() != content:
                raise ValueError(f"Report mismatch: {path}")
        else:
            path.write_text(content)
    print(json.dumps(dict(status="PASS", mode="verify" if args.verify else "write",
                          files={str(p.relative_to(ROOT)): file_hash(p) for p in targets}), indent=2))


if __name__ == "__main__":
    main()
