"""Generate the audited five-way report for selected A/B/C candidates.

This reader is fail-closed.  It never runs a strategy and writes no report or
figure until the new result directory contains a PASS audit and a complete,
matched five-model comparison.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/codex-matplotlib-cache")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/codex-cache")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "outputs/v2_best_final_20260922"
DEFAULT_REPORT = ROOT / "reports/v2_best_final.md"
MODELS = ("A", "B", "C", "v1_matched", "0050")
SELECTED = {"A": "p005", "B": "p005", "C": "p006"}
LABELS = {
    "A": "A 規則優先 p005",
    "B": "B 規則優先 p005",
    "C": "C 規則優先 p006",
    "v1_matched": "v1 同口徑（未調參）",
    "0050": "0050 同預算基準",
}
PLOT_LABELS = {
    "A": "A p005", "B": "B p005", "C": "C p006",
    "v1_matched": "v1 matched (untuned)", "0050": "0050 same-budget benchmark",
}
PARAMETERS = (
    "target_count", "replacement_margin", "max_replacements_per_day",
    "volatility_spike_ratio", "one_day_chase_return", "volume_low",
    "volume_high", "four_hour_mode", "returns", "ema", "macd",
    "score_profile", "sector_top_fraction", "sector_short_weight",
    "sector_fallback_mode", "c_alpha",
)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, keep_default_na=False, low_memory=False)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def normalized_selection(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("provenance.selected_candidates must be an object")
    selected = {}
    for strategy, value in raw.items():
        if isinstance(value, dict):
            value = value.get("candidate_id")
        selected[str(strategy)] = str(value)
    return selected


def max_drawdown(values: np.ndarray) -> float:
    return float(-(values / np.maximum.accumulate(values) - 1).min())


def validate_inputs(output: Path) -> dict[str, Any]:
    audit = read_json(output / "audit.json")
    if audit.get("status") != "PASS":
        raise ValueError("New five-way audit.json must exist and have status PASS")
    provenance = read_json(output / "provenance.json")
    if provenance.get("outputs_complete") is not True:
        raise ValueError("provenance outputs_complete is not true")
    if normalized_selection(provenance.get("selected_candidates")) != SELECTED:
        raise ValueError("Selected candidate mapping is not A/B=p005, C=p006")
    scope = next(
        (provenance.get(key) for key in (
            "selection_scope", "selection_basis", "selection_status"
        ) if provenance.get(key)),
        None,
    )
    if scope != "EX_POST_DEVELOPMENT":
        raise ValueError(f"Expected EX_POST_DEVELOPMENT provenance, got {scope!r}")

    summary = read_csv(output / "summary.csv")
    required_summary = {
        "model", "economic_total_return", "economic_max_drawdown",
        "economic_sharpe_zero_rf", "turnover_two_way", "costs",
        "hard_breach_days", "infeasible_executed_days", "trades",
        "trade_days", "sessions", "start", "end", "candidate_id",
        "average_stock_exposure",
    }
    if required_summary - set(summary):
        raise ValueError(f"summary.csv missing {sorted(required_summary - set(summary))}")
    if len(summary) != len(MODELS) or set(summary.model) != set(MODELS):
        raise ValueError("summary.csv must contain exactly A/B/C/v1_matched/0050")
    if summary.model.duplicated().any():
        raise ValueError("summary.csv contains duplicate models")
    summary = summary.set_index("model").loc[list(MODELS)].reset_index()
    for strategy, candidate_id in SELECTED.items():
        observed = str(summary.loc[summary.model.eq(strategy), "candidate_id"].iloc[0])
        if observed != candidate_id:
            raise ValueError(f"{strategy}: expected {candidate_id}, got {observed}")
    if not pd.to_numeric(summary.sessions).eq(417).all():
        raise ValueError("All five models must contain 417 sessions")
    if set(summary.start.astype(str)) != {"2025-01-02"} or set(summary.end.astype(str)) != {"2026-09-21"}:
        raise ValueError("Unexpected five-way comparison dates")

    monthly = read_csv(output / "monthly_comparison.csv")
    required_monthly = {
        "model", "month", "sessions", "through",
        "economic_return", "economic_max_drawdown",
    }
    if required_monthly - set(monthly):
        raise ValueError(f"monthly_comparison.csv missing {sorted(required_monthly - set(monthly))}")
    if monthly.duplicated(["model", "month"]).any():
        raise ValueError("Duplicate model/month rows")
    counts = monthly.groupby("model").size().to_dict()
    if counts != {model: 21 for model in MODELS}:
        raise ValueError(f"Expected 21 months for every model, got {counts}")
    if set(monthly.model) != set(MODELS) or len(monthly) != 105:
        raise ValueError("Monthly table must contain exactly 105 rows")
    september = monthly[monthly.month.astype(str).eq("2026-09")]
    if len(september) != 5 or set(september.through.astype(str)) != {"2026-09-21"}:
        raise ValueError("2026-09 must be a partial month through September 21")

    nav = read_csv(output / "economic_nav_comparison.csv")
    if list(nav.columns) != ["date", *MODELS] or len(nav) != 418:
        raise ValueError("Economic NAV must have date plus five models and 418 rows")
    if str(nav.date.iloc[0]) != "2024-12-31" or str(nav.date.iloc[-1]) != "2026-09-21":
        raise ValueError("Economic NAV initial/final dates mismatch")
    initial_cash = float(provenance["config"]["initial_cash"])
    values = nav[list(MODELS)].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(values.to_numpy()).all() or not np.allclose(
        values.iloc[0].to_numpy(), initial_cash, rtol=0, atol=1e-8
    ):
        raise ValueError("Economic NAV is nonfinite or does not share initial capital")

    for model in MODELS:
        row = summary[summary.model.eq(model)].iloc[0]
        wealth = values[model].to_numpy(float)
        if not math.isclose(
            float(row.economic_total_return), wealth[-1] / initial_cash - 1,
            rel_tol=0, abs_tol=1e-10,
        ):
            raise ValueError(f"{model}: summary return differs from NAV")
        if not math.isclose(
            float(row.economic_max_drawdown), max_drawdown(wealth),
            rel_tol=0, abs_tol=1e-10,
        ):
            raise ValueError(f"{model}: summary MDD differs from NAV")
        product = float(
            np.prod(1 + pd.to_numeric(
                monthly.loc[monthly.model.eq(model), "economic_return"], errors="raise"
            )) - 1
        )
        if not math.isclose(product, float(row.economic_total_return), rel_tol=0, abs_tol=1e-10):
            raise ValueError(f"{model}: monthly returns do not compound to full return")

    configs, equities, trades = {}, {}, {}
    for model in MODELS:
        folder = output / model
        configs[model] = read_json(folder / "config.json")
        read_json(folder / "metrics.json")
        equities[model] = read_csv(folder / "equity.csv")
        trades[model] = read_csv(folder / "trades.csv")
        if len(equities[model]) != 417:
            raise ValueError(f"{model}: equity session count mismatch")
    for strategy, candidate_id in SELECTED.items():
        config = configs[strategy]
        if str(config.get("tuning_candidate_id")) != candidate_id:
            raise ValueError(f"{strategy}: config candidate provenance mismatch")
        params = config.get("tuning_params")
        if not isinstance(params, dict) or set(params) != set(PARAMETERS):
            raise ValueError(f"{strategy}: incomplete explicit tuning_params")
    if not trades["A"].equals(trades["B"]):
        raise ValueError("Selected A/B are expected to have exact historical trades")

    audit_models = audit.get("models")
    if not isinstance(audit_models, dict) or set(audit_models) != set(MODELS):
        raise ValueError("audit.json must independently cover all five models")
    return {
        "audit": audit,
        "provenance": provenance,
        "summary": summary,
        "monthly": monthly,
        "nav": nav,
        "configs": configs,
        "equities": equities,
        "trades": trades,
    }


def percent(value: Any, digits: int = 2) -> str:
    return f"{float(value) * 100:.{digits}f}%"


def money(value: Any) -> str:
    return f"{float(value) / 1_000_000:.2f}M"


def number(value: Any, digits: int = 2) -> str:
    return f"{float(value):.{digits}f}"


def markdown_table(frame: pd.DataFrame, columns: list[tuple[str, str, Any]]) -> str:
    lines = [
        "| " + " | ".join(label for _, label, _ in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for _, record in frame.iterrows():
        rendered = []
        for key, _, formatter in columns:
            value = record[key]
            value = formatter(value) if formatter else str(value)
            rendered.append(value.replace("|", "\\|"))
        lines.append("| " + " | ".join(rendered) + " |")
    return "\n".join(lines)


def summary_display(summary: pd.DataFrame) -> pd.DataFrame:
    result = summary.copy()
    result["label"] = result.model.map(LABELS)
    result["candidate"] = result.candidate_id.astype(str).replace({"": "—"})
    return result


def cash_table(equities: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for model in MODELS:
        cash = pd.to_numeric(equities[model]["cash_ratio"], errors="raise")
        rows.append({
            "model": model,
            "label": LABELS[model],
            "mean_cash": float(cash.mean()),
            "min_cash": float(cash.min()),
            "max_cash": float(cash.max()),
            "positive_days": int(cash.gt(1e-12).sum()),
        })
    return pd.DataFrame(rows)


def parameter_table(configs: dict[str, dict[str, Any]]) -> pd.DataFrame:
    labels = {
        "target_count": "目標持股數",
        "replacement_margin": "換股分數門檻",
        "max_replacements_per_day": "每日一般替換上限",
        "volatility_spike_ratio": "波動尖峰比率",
        "one_day_chase_return": "單日追價上限",
        "volume_low": "量能下界",
        "volume_high": "量能上界",
        "four_hour_mode": "4H 模式",
        "returns": "日報酬視窗",
        "ema": "日 EMA",
        "macd": "日 MACD",
        "score_profile": "個股分數權重",
        "sector_top_fraction": "板塊保留比例",
        "sector_short_weight": "板塊短期權重",
        "sector_fallback_mode": "板塊 fallback",
        "c_alpha": "隔夜排名係數",
    }
    notes = {
        "four_hour_mode": "strict；4H EMA20、MACD 12/26/9、50 bars ready",
        "returns": "base = 20/50 sessions",
        "ema": "base = 20/50 spans",
        "macd": "base = 12/26/9",
        "score_profile": "base = 0.40/0.35/0.10/0.05/0.05/0.05",
        "sector_top_fraction": "A 不作用；B/C 作用",
        "sector_short_weight": "A 不作用；B/C 作用",
        "sector_fallback_mode": "A 不作用；B/C 作用",
        "c_alpha": "只在 C 作用；A/B 記錄值不代表啟用",
    }
    rows = []
    for parameter in PARAMETERS:
        rows.append({
            "parameter": parameter,
            "說明": labels[parameter],
            **{
                strategy: str(configs[strategy]["tuning_params"][parameter])
                for strategy in ("A", "B", "C")
            },
            "作用範圍／展開": notes.get(parameter, "三版共同"),
        })
    return pd.DataFrame(rows)


def nav_drawdown_plot(nav: pd.DataFrame, initial_cash: float, path: Path) -> None:
    colors = {
        "A": "#0f766e", "B": "#7c3aed", "C": "#ea580c",
        "v1_matched": "#64748b", "0050": "#111827",
    }
    dates = pd.to_datetime(nav.date)
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    a_values = pd.to_numeric(nav["A"], errors="raise").to_numpy(float)
    b_values = pd.to_numeric(nav["B"], errors="raise").to_numpy(float)
    if not np.array_equal(a_values, b_values):
        raise ValueError("A/B NAV must be exactly equal for the shared-line plot")
    plot_models = ("A", "C", "v1_matched", "0050")
    for model in plot_models:
        values = pd.to_numeric(nav[model], errors="raise").to_numpy(float)
        nav_return = (values / initial_cash - 1) * 100
        drawdown = (values / np.maximum.accumulate(values) - 1) * 100
        width = 2.2 if model in ("A", "B", "C") else 1.4
        style = "--" if model == "v1_matched" else "-"
        label = "A p005 = B p005" if model == "A" else PLOT_LABELS[model]
        axes[0].plot(dates, nav_return, color=colors[model], lw=width, ls=style, label=label)
        axes[1].plot(dates, drawdown, color=colors[model], lw=width, ls=style)
    axes[0].set_title("Five-way economic NAV comparison (after commission and sell tax)")
    axes[0].set_ylabel("Cumulative return (%)")
    axes[1].set_title("Drawdown from running economic NAV peak")
    axes[1].set_ylabel("Drawdown (%)")
    axes[1].set_xlabel("Date")
    for axis in axes:
        axis.grid(alpha=0.25)
    axes[0].legend(ncol=3, fontsize=8, frameon=False)
    fig.suptitle("2025-01-02 → 2026-09-21 · Initial capital TWD 1.0bn", y=0.995, fontsize=14)
    fig.subplots_adjust(top=0.90, hspace=0.30)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=175, bbox_inches="tight")
    plt.close(fig)


def monthly_plot(monthly: pd.DataFrame, path: Path) -> None:
    months = sorted(monthly.month.astype(str).unique())
    returns = np.array([
        monthly[monthly.model.eq(model)].set_index("month").loc[months, "economic_return"].astype(float).to_numpy() * 100
        for model in MODELS
    ])
    drawdowns = np.array([
        monthly[monthly.model.eq(model)].set_index("month").loc[months, "economic_max_drawdown"].astype(float).to_numpy() * 100
        for model in MODELS
    ])
    fig, axes = plt.subplots(2, 1, figsize=(18, 6.2), constrained_layout=True)
    return_bound = max(float(np.abs(returns).max()), 1.0)
    for axis, matrix, title, cmap, vmin, vmax in [
        (axes[0], returns, "Monthly economic return (%)", "RdBu_r", -return_bound, return_bound),
        (axes[1], drawdowns, "Monthly economic maximum drawdown (%)", "YlOrRd", 0, max(float(drawdowns.max()), 1.0)),
    ]:
        image = axis.imshow(matrix, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)
        axis.set_title(title)
        axis.set_xticks(range(len(months)), months, rotation=45, ha="right", fontsize=8)
        axis.set_yticks(range(len(MODELS)), [PLOT_LABELS[model] for model in MODELS], fontsize=8)
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                axis.text(j, i, f"{matrix[i, j]:.1f}", ha="center", va="center", fontsize=5.5)
        fig.colorbar(image, ax=axis, shrink=0.8, pad=0.01)
    fig.suptitle("All 21 development months · 2026-09 partial through Sep 21", fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=175, bbox_inches="tight")
    plt.close(fig)


def comparison_table_plot(summary: pd.DataFrame, path: Path) -> None:
    rows = []
    for row in summary.itertuples(index=False):
        candidate = str(row.candidate_id) if row.model in ("A", "B", "C") else "fixed"
        hard_breach = "N/A" if row.model == "0050" else str(int(row.hard_breach_days))
        rows.append([
            PLOT_LABELS[row.model], candidate,
            percent(row.economic_total_return), percent(row.economic_max_drawdown),
            f"{float(row.economic_sharpe_zero_rf):.3f}",
            percent(row.average_stock_exposure), f"{float(row.turnover_two_way):.2f}",
            f"{float(row.costs) / 1_000_000:.2f}", hard_breach, str(int(row.trade_days)),
        ])
    columns = ["Model", "Candidate", "Return", "MDD", "Sharpe", "Avg exposure", "Turnover", "Costs (TWD m)", "Hard breach", "Trade days"]
    fig, axis = plt.subplots(figsize=(16, 4.8))
    axis.axis("off")
    table = axis.table(
        cellText=rows, colLabels=columns, cellLoc="center", colLoc="center", loc="center",
        colWidths=[0.18, 0.08, 0.08, 0.07, 0.07, 0.09, 0.08, 0.09, 0.09, 0.08],
    )
    table.auto_set_font_size(False); table.set_fontsize(9.5); table.scale(1, 1.75)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#cbd5e1")
        if row == 0:
            cell.set_facecolor("#dbeafe"); cell.set_text_props(weight="bold")
        elif row % 2 == 0:
            cell.set_facecolor("#f8fafc")
    axis.set_title(
        "Audited five-way full-period comparison\n"
        "Economic NAV · 2025-01-02 → 2026-09-21",
        fontsize=15, pad=20,
    )
    fig.text(
        0.5, 0.035,
        "Hard breach = research proxy days; 0050 is a benchmark and is excluded from contest-rule interpretation",
        ha="center", fontsize=8.5, color="#475569",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def image_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def monthly_wide(monthly: pd.DataFrame, value: str) -> pd.DataFrame:
    frame = monthly.pivot(index="month", columns="model", values=value).reset_index()
    return frame[["month", *MODELS]]


def practical_answer(summary: pd.DataFrame) -> str:
    indexed = summary.set_index("model")
    a, c = indexed.loc["A"], indexed.loc["C"]
    return (
        "如果目前只能選一個進入下一輪紙上驗證，依既定 rule-first 規則選 "
        f"C p006。在這段完整開發期間，C 比 A/B 多 "
        f"{(float(c.economic_total_return) - float(a.economic_total_return)) * 100:.2f} pp 報酬，"
        f"但 MDD 也多 {(float(c.economic_max_drawdown) - float(a.economic_max_drawdown)) * 100:.2f} pp，"
        f"交易日由 {int(a.trade_days)} 增為 {int(c.trade_days)}。"
        "這只是下一個研究 follow-up 的優先順序，不是 LIVE 部署結論。"
    )


def render_markdown(output: Path, data: dict[str, Any], figures: dict[str, Path]) -> str:
    summary = summary_display(data["summary"])
    monthly_return = monthly_wide(data["monthly"], "economic_return")
    monthly_mdd = monthly_wide(data["monthly"], "economic_max_drawdown")
    params = parameter_table(data["configs"])
    cash = cash_table(data["equities"])
    config = data["provenance"]["config"]
    return_best = summary.loc[summary.economic_total_return.astype(float).idxmax()]
    mdd_best = summary.loc[summary.economic_max_drawdown.astype(float).idxmin()]
    benchmark = summary[summary.model.eq("0050")].iloc[0]
    return f"""# A/B/C 規則優先候選：五方最終比較

## 結果界線

比較期間為 **2025-01-02–2026-09-21**，初始本金 **{float(config['initial_cash']) / 100_000_000:.2f} 億 NTD**。所有績效使用已扣 {float(config['commission']) * 100:.4f}% 手續費及 {float(config['sell_tax']) * 100:.2f}% 賣出稅的 economic NAV；2026-09 只統計至 9 月 21 日。

同一期內，累積報酬最高的是 **{return_best.label}（{percent(return_best.economic_total_return)}）**，最大回撤最低的是 **{mdd_best.label}（{percent(mdd_best.economic_max_drawdown)}）**。這是開發資料的事後比較，不是 unseen test，也不是 LIVE 建議。

- [HTML 完整報告](v2_best_final.html)
- [五方摘要 CSV](../outputs/{output.name}/summary.csv)
- [21 個月報 CSV](../outputs/{output.name}/monthly_comparison.csv)
- [新結果 audit](../outputs/{output.name}/audit.json)
- [新結果 provenance](../outputs/{output.name}/provenance.json)
- [先前 192-trial 調參報告](v2_abc_tuning.md)

## 五方全期比較

{markdown_table(summary, [
    ('label','模型',None), ('candidate','候選',None),
    ('economic_total_return','總報酬',percent), ('economic_max_drawdown','MDD',percent),
    ('economic_sharpe_zero_rf','Sharpe',lambda x:number(x,3)),
    ('average_stock_exposure','平均股票曝險',percent),
    ('turnover_two_way','雙向換手',number),
    ('costs','成本（NTD 百萬）',lambda x:number(float(x) / 1_000_000,2)),
    ('hard_breach_days','Hard breach',lambda x:str(int(float(x)))),
    ('infeasible_executed_days','不可行成交日',lambda x:str(int(float(x)))),
    ('trades','成交筆數',lambda x:str(int(float(x)))), ('trade_days','交易日',lambda x:str(int(float(x)))),
])}

**{practical_answer(data['summary'])}**

A 與 B 的逐筆歷史成交完全相同，所以這組已選候選沒有證據顯示 B 的板塊門控增加價值。C 相對 A/B 同時改變一般替換上限並啟用隔夜排名，不能把 C 的差異單獨歸因給隔夜訊號。

![Five-way numeric comparison](../outputs/{output.name}/{figures['table'].name})

![Economic NAV and drawdown](../outputs/{output.name}/{figures['nav'].name})

五方使用相同完整期間、初始本金、手續費與賣出稅假設。`v1 同口徑` 沿用固定、未針對這次 192-trial 搜尋調參的 v1 規則，只統一成交、資金與資料口徑；它沒有和 A/B/C 取得相同調參機會。`0050` 是同預算買入持有基準，不符合 20–30 檔持股等比賽策略規則。它的 hard-breach days 為 {int(benchmark.hard_breach_days)}，不可行成交日為 {int(benchmark.infeasible_executed_days)}（含 2025 分割停牌情境），兩者都不能被解讀為官方競賽合規或違規認定。

## 21 個月報

月報酬：

{markdown_table(monthly_return, [('month','月份',None), *[(model,LABELS[model],percent) for model in MODELS]])}

月內最大回撤：

{markdown_table(monthly_mdd, [('month','月份',None), *[(model,LABELS[model],percent) for model in MODELS]])}

![Monthly return and MDD](../outputs/{output.name}/{figures['monthly'].name})

月報酬逐月銜接 economic NAV；月內 MDD 只看該月起點至月底的路徑。2026-09 是截至 9 月 21 日的 partial month。

## 最終候選的完整參數

{markdown_table(params, [
    ('parameter','參數',None), ('說明','中文',None), ('A','A p005',None),
    ('B','B p005',None), ('C','C p006',None), ('作用範圍／展開','作用範圍／實值',None),
])}

A/B 的 p005 只把每日一般替換上限由 2 改為 0；C 的 p006 改為 1。這個上限只限制一般替換，強制退出、補足持股與風控修正仍會交易，0 不代表停止交易。C 相對 A/B 同時改變替換上限並啟用隔夜排名，因此五方結果不能被解讀為隔夜訊號的單一因果效果。

這三個候選來自先前 192 組開發資料搜尋，選擇規則先排除負現金，再最小化 hard-breach days 與 infeasible executed days，之後才比較報酬與 MDD。這次重跑固定候選，不再次選參數；但歷史期間相同，所以證據仍是 **EX_POST_DEVELOPMENT**。

## 實際現金與限制

策略目標現金為 0，不代表實際現金為 0。價格上漲緩衝、費稅、整張交易及無法成交都會留下現金。

{markdown_table(cash, [
    ('label','模型',None), ('mean_cash','平均現金',percent), ('min_cash','最低現金',percent),
    ('max_cash','最高現金',percent), ('positive_days','正現金日',None),
])}

Hard-breach days 是研究代理指標，不是官方違規次數。歷史 ETF holdings、歷史白名單與 Active Share 尚未驗證；0050 也不是競賽合規策略。Audit PASS 只支持已保存帳本、算術與記錄時序，不能證明市場資料的原始發布版本、容量可成交性或官方競賽認證。所有結果維持 research shadow 與 BLOCK_SUBMISSION。

## 程式入口與重跑

- [v2_A_best.py](../v2_A_best.py)
- [v2_B_best.py](../v2_B_best.py)
- [v2_C_best.py](../v2_C_best.py)
- [共用引擎](../src/v2_best.py)
- [五方批次執行](../scripts/run_v2_best.py)
- [獨立結果稽核](../scripts/audit_v2_best.py)

請使用新的輸出目錄，保留本次已稽核結果：

```bash
python3 scripts/run_v2_best.py --output outputs/v2_best_final_REPRO
python3 scripts/audit_v2_best.py --output outputs/v2_best_final_REPRO
python3 scripts/report_v2_best.py --output outputs/v2_best_final_REPRO --report reports/v2_best_final_repro.md
```
"""


def html_table(frame: pd.DataFrame, columns: list[str]) -> str:
    return frame[columns].to_html(index=False, classes="data", border=0, escape=True)


def render_html(output: Path, data: dict[str, Any], figures: dict[str, Path]) -> str:
    summary = summary_display(data["summary"])
    shown = summary[[
        "label", "candidate", "economic_total_return", "economic_max_drawdown",
        "economic_sharpe_zero_rf", "average_stock_exposure", "turnover_two_way", "costs", "hard_breach_days",
        "infeasible_executed_days", "trades", "trade_days",
    ]].copy()
    for column in ("economic_total_return", "economic_max_drawdown"):
        shown[column] = shown[column].map(percent)
    shown["economic_sharpe_zero_rf"] = shown["economic_sharpe_zero_rf"].map(lambda value: number(value, 3))
    shown["average_stock_exposure"] = shown["average_stock_exposure"].map(percent)
    shown["turnover_two_way"] = shown["turnover_two_way"].map(number)
    shown["costs"] = shown["costs"].map(lambda value: number(float(value) / 1_000_000, 2))
    shown = shown.rename(columns={"costs": "成本（NTD 百萬）"})
    params = parameter_table(data["configs"])
    cash = cash_table(data["equities"])
    for column in ("mean_cash", "min_cash", "max_cash"):
        cash[column] = cash[column].map(percent)
    monthly_return = monthly_wide(data["monthly"], "economic_return")
    monthly_mdd = monthly_wide(data["monthly"], "economic_max_drawdown")
    for frame in (monthly_return, monthly_mdd):
        for model in MODELS:
            frame[model] = frame[model].map(percent)
    config = data["provenance"]["config"]
    best_return = summary.loc[summary.economic_total_return.astype(float).idxmax()]
    best_mdd = summary.loc[summary.economic_max_drawdown.astype(float).idxmin()]
    benchmark = summary[summary.model.eq("0050")].iloc[0]
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>A/B/C 規則優先候選：五方最終比較</title>
<style>
:root{{--ink:#172033;--muted:#657087;--line:#d9deea;--paper:#fff;--soft:#f5f7fb;--accent:#2457d6;--warn:#9a3412}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--soft);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1380px;margin:auto;background:white;padding:38px 46px 70px;box-shadow:0 0 28px #1e293b12}} h1{{font-size:30px;margin:0 0 8px}} h2{{font-size:21px;margin:34px 0 10px;border-top:1px solid var(--line);padding-top:24px}} p{{max-width:1000px}} .lede{{font-size:17px}} .notice{{border-left:4px solid var(--warn);background:#fff7ed;padding:12px 16px}} a{{color:var(--accent)}} img{{max-width:100%;border:1px solid var(--line);border-radius:8px;background:white;margin:8px 0 18px}} .wrap{{overflow:auto;border:1px solid var(--line);border-radius:7px;margin:10px 0 18px}} table.data{{border-collapse:collapse;width:100%;font-size:12px}} th,td{{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;white-space:nowrap}} th{{background:#eaf0ff;position:sticky;top:0}} tr:nth-child(even){{background:#fafbfe}} .links{{display:flex;gap:14px;flex-wrap:wrap}} @media(max-width:800px){{main{{padding:24px 16px}}}}
</style></head><body><main><h1>A/B/C 規則優先候選：五方最終比較</h1>
<p class="lede">2025-01-02–2026-09-21；初始本金 {float(config['initial_cash']) / 100_000_000:.2f} 億 NTD。Economic NAV 已扣 {float(config['commission']) * 100:.4f}% 手續費與 {float(config['sell_tax']) * 100:.2f}% 賣出稅。</p>
<p><b>同期累積報酬最高：</b>{html.escape(str(best_return.label))}（{percent(best_return.economic_total_return)}）；<b>最大回撤最低：</b>{html.escape(str(best_mdd.label))}（{percent(best_mdd.economic_max_drawdown)}）。</p>
<p class="notice">這是同一開發期間的事後比較，不是 unseen test 或 LIVE 建議。2026-09 只統計至 9 月 21 日。</p>
<p class="links"><a href="../outputs/{html.escape(output.name)}/summary.csv">摘要 CSV</a><a href="../outputs/{html.escape(output.name)}/monthly_comparison.csv">月報 CSV</a><a href="../outputs/{html.escape(output.name)}/audit.json">Audit</a><a href="../outputs/{html.escape(output.name)}/provenance.json">Provenance</a><a href="v2_abc_tuning.html">192-trial 調參報告</a></p>
<h2>五方全期比較</h2><div class="wrap">{html_table(shown, list(shown.columns))}</div>
<p><b>{html.escape(practical_answer(data['summary']))}</b></p>
<p>A 與 B 的逐筆歷史成交完全相同，所以這組已選候選沒有證據顯示 B 的板塊門控增加價值。C 同時改變一般替換上限並啟用隔夜排名，不能把差異單獨歸因給隔夜訊號。</p>
<img alt="Five-way numeric comparison" src="{image_uri(figures['table'])}"><img alt="Economic NAV and drawdown" src="{image_uri(figures['nav'])}">
<p>五方使用相同完整期間、初始本金、手續費與賣出稅假設。v1 同口徑是固定、未參與 192-trial 調參的基線，因此沒有取得和 A/B/C 相同的調參機會。0050 是同預算買入持有基準，不符合 20–30 檔持股規則；它的 hard-breach days 為 {int(benchmark.hard_breach_days)}、不可行成交日為 {int(benchmark.infeasible_executed_days)}（含 2025 分割停牌情境），不能解讀為官方競賽合規或違規認定。</p>
<h2>21 個月報</h2><h3>月報酬</h3><div class="wrap">{html_table(monthly_return, list(monthly_return.columns))}</div><h3>月內最大回撤</h3><div class="wrap">{html_table(monthly_mdd, list(monthly_mdd.columns))}</div><img alt="Monthly returns and drawdowns" src="{image_uri(figures['monthly'])}"><p>月報酬逐月銜接 economic NAV；月內 MDD 只看該月起點至月底。2026-09 是截至 9 月 21 日的 partial month。</p>
<h2>最終候選完整參數</h2><div class="wrap">{html_table(params, list(params.columns))}</div><p>A/B p005 的每日一般替換上限為 0，C p006 為 1；強制退出、補足持股與風控修正仍會交易。C 同時改變替換上限並啟用隔夜排名，不能把五方差異解讀成隔夜訊號的單一因果效果。</p><p>候選由先前 192 組開發資料依規則優先選出；本次固定重跑、不再次選參數，但仍是 EX_POST_DEVELOPMENT。</p>
<h2>實際現金與限制</h2><p>策略現金目標為 0，不代表實際現金為 0。</p><div class="wrap">{html_table(cash, list(cash.columns))}</div><p>Hard-breach days 是研究代理指標，不是官方違規次數。歷史 ETF holdings、歷史白名單與 Active Share 未驗證。Audit PASS 僅支持已保存帳本、算術與記錄時序；所有結果維持 research shadow 與 BLOCK_SUBMISSION。</p>
<h2>程式入口與重跑</h2><p class="links"><a href="../v2_A_best.py">v2_A_best.py</a><a href="../v2_B_best.py">v2_B_best.py</a><a href="../v2_C_best.py">v2_C_best.py</a><a href="../src/v2_best.py">共用引擎</a><a href="../scripts/run_v2_best.py">五方批次執行</a><a href="../scripts/audit_v2_best.py">獨立結果稽核</a></p><p>使用新的輸出目錄，保留本次已稽核結果：</p><pre><code>python3 scripts/run_v2_best.py --output outputs/v2_best_final_REPRO
python3 scripts/audit_v2_best.py --output outputs/v2_best_final_REPRO
python3 scripts/report_v2_best.py --output outputs/v2_best_final_REPRO --report reports/v2_best_final_repro.md</code></pre>
</main></body></html>"""


def run(output: Path, report: Path) -> None:
    data = validate_inputs(output)
    figures = {
        "nav": output / "nav_drawdown.png",
        "monthly": output / "monthly_return_mdd.png",
        "table": output / "comparison_table.png",
    }
    initial_cash = float(data["provenance"]["config"]["initial_cash"])
    nav_drawdown_plot(data["nav"], initial_cash, figures["nav"])
    monthly_plot(data["monthly"], figures["monthly"])
    comparison_table_plot(data["summary"], figures["table"])
    markdown = render_markdown(output, data, figures)
    html_text = render_html(output, data, figures)
    atomic_write(report, markdown)
    atomic_write(report.with_suffix(".html"), html_text)
    print(json.dumps({
        "status": "GENERATED_FROM_NEW_AUDIT_PASS",
        "markdown": str(report.relative_to(ROOT)),
        "html": str(report.with_suffix('.html').relative_to(ROOT)),
        "figures": [str(path.relative_to(ROOT)) for path in figures.values()],
        "models": list(MODELS),
        "months_per_model": 21,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    report = args.report if args.report.is_absolute() else ROOT / args.report
    try:
        run(output, report)
    except Exception as error:
        print(f"REPORT BLOCKED: {type(error).__name__}: {error}", file=sys.stderr)
        raise
