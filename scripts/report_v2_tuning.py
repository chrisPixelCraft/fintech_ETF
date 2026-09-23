"""Generate the audited A/B/C tuning report and offline HTML explorer.

The generator is fail-closed: it reads completed artifacts only and refuses to
write any report unless ``tuning_audit.json`` says PASS and the full matched
192-trial table is present.  It never reruns, selects, or modifies a strategy.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import os
import sys
from collections import Counter
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
DEFAULT_OUTPUT = ROOT / "outputs/v2_abc_tuning_20260922"
DEFAULT_REPORT = ROOT / "reports/v2_abc_tuning.md"
REQUIRED_STRATEGIES = ("A", "B", "C")
REQUIRED_ROLES = ("INCUMBENT", "EX_POST_BEST", "WALK_FORWARD")
REQUIRED_SCOPES = ("FULL_DEVELOPMENT", "AFTER_SELECTION_START")
ROLE_LABELS_ZH = {
    "INCUMBENT": "原版",
    "EX_POST_BEST": "規則優先（事後）",
    "WALK_FORWARD": "向前選參數",
}
ROLE_ORDER = {role: index for index, role in enumerate(REQUIRED_ROLES)}
REQUIRED_METRICS = (
    "economic_total_return",
    "economic_max_drawdown",
    "turnover_two_way",
    "costs",
    "hard_breach_days",
    "infeasible_executed_days",
    "negative_cash_days",
    "raw_rule_breach_days",
)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, keep_default_na=False, low_memory=False)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def atomic_write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def assert_equal(actual: Any, expected: Any, label: str) -> None:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            if math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-12):
                return
        except (TypeError, ValueError):
            pass
    elif str(actual) == str(expected):
        return
    raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def validate_inputs(output: Path) -> dict[str, Any]:
    audit = read_json(output / "tuning_audit.json")
    if audit.get("status") != "PASS":
        raise ValueError("tuning_audit.json must exist and have status PASS")
    prefix_audit = read_json(output / "walk_forward_prefix_audit.json")
    if prefix_audit.get("status") != "PASS":
        raise ValueError("walk_forward_prefix_audit.json must have status PASS")
    required_prefix = {
        "cutpoint", "strategy", "sessions", "trades",
        "max_economic_nav_abs_error", "selection_rows_verified",
        "switch_days_rebuilt", "baseline_recovery", "interpretation",
    }
    if required_prefix - set(prefix_audit):
        raise ValueError(
            "walk_forward_prefix_audit.json missing "
            f"{sorted(required_prefix - set(prefix_audit))}"
        )
    if not prefix_audit["baseline_recovery"] or any(
        row.get("status") != "PASS" for row in prefix_audit["baseline_recovery"]
    ):
        raise ValueError("Prefix audit baseline recovery must all PASS")
    manifest = read_json(output / "tuning_manifest.json")
    if manifest.get("outputs_complete") is not True:
        raise ValueError("Tuning manifest is not complete")
    study = manifest.get("study") or read_json(
        output / "input_snapshot/config/v2_tuning_study.json"
    )
    expected = int(study.get("trial_budget", -1))
    if expected != 192:
        raise ValueError(f"Expected frozen 192-trial budget, got {expected}")
    if study.get("historical_period_is_development") is not True or study.get("unseen_test") is not False:
        raise ValueError("Study boundaries do not label the period as development")

    summary = read_csv(output / "trial_summary.csv")
    if len(summary) != expected:
        raise ValueError(f"Expected {expected} completed trial rows, got {len(summary)}")
    required = {
        "strategy", "candidate_id", "status", "design", "varied", *REQUIRED_METRICS,
        *study["spaces"].keys(),
    }
    # volume_range is a design label; concrete parameters are volume_low/high.
    required.discard("volume_range")
    missing = required - set(summary)
    if missing:
        raise ValueError(f"trial_summary.csv missing {sorted(missing)}")
    if not summary["status"].eq("COMPLETE").all():
        raise ValueError("All 192 trials must be COMPLETE")
    if summary.duplicated(["strategy", "candidate_id"]).any():
        raise ValueError("Duplicate strategy/candidate rows")
    counts = summary.groupby("strategy")["candidate_id"].nunique().to_dict()
    if counts != {strategy: 64 for strategy in REQUIRED_STRATEGIES}:
        raise ValueError(f"Expected fair 64/64/64 candidate counts, got {counts}")

    candidates = {str(row["candidate_id"]): row for row in study["candidates"]}
    if len(candidates) != 64:
        raise ValueError("Frozen config must contain 64 unique candidates")
    for row in summary.itertuples(index=False):
        cid = str(row.candidate_id)
        if cid not in candidates:
            raise ValueError(f"Unknown candidate in summary: {cid}")
        declared = candidates[cid]["params"]
        for key, expected_value in declared.items():
            assert_equal(getattr(row, key), expected_value, f"{row.strategy}/{cid}/{key}")
    for strategy in REQUIRED_STRATEGIES:
        if set(summary.loc[summary.strategy.eq(strategy), "candidate_id"].astype(str)) != set(candidates):
            raise ValueError(f"{strategy}: candidate IDs are not matched")

    comparison = read_csv(output / "comparison.csv")
    required_comparison = {"strategy", "role", "candidate_id", "scope", *REQUIRED_METRICS}
    if required_comparison - set(comparison):
        raise ValueError(f"comparison.csv missing {sorted(required_comparison - set(comparison))}")
    expected_keys = {
        (strategy, role, scope)
        for strategy in REQUIRED_STRATEGIES
        for role in REQUIRED_ROLES
        for scope in REQUIRED_SCOPES
    }
    actual_keys = set(zip(comparison.strategy, comparison.role, comparison.scope))
    if actual_keys != expected_keys or len(comparison) != len(expected_keys):
        raise ValueError("comparison.csv must contain exactly 3 strategies × 3 roles × 2 scopes")

    schedule = read_csv(output / "schedule.csv")
    if len(schedule) != 12 or schedule.groupby("strategy").size().to_dict() != {
        strategy: 4 for strategy in REQUIRED_STRATEGIES
    }:
        raise ValueError("schedule.csv must contain four choices for each strategy")
    monthly = read_csv(output / "monthly_comparison.csv")
    nav = read_csv(output / "nav_comparison.csv")
    if monthly.empty or nav.empty:
        raise ValueError("Monthly or NAV comparison is empty")

    return {
        "audit": audit,
        "prefix_audit": prefix_audit,
        "manifest": manifest,
        "study": study,
        "summary": summary,
        "comparison": comparison,
        "schedule": schedule,
        "monthly": monthly,
        "nav": nav,
    }


def valid_trials(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[pd.to_numeric(frame["negative_cash_days"]).eq(0)].copy()


def primary_sort(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(
        [
            "hard_breach_days",
            "infeasible_executed_days",
            "economic_total_return",
            "economic_max_drawdown",
            "candidate_id",
        ],
        ascending=[True, True, False, True, True],
    )


def pareto_ids(frame: pd.DataFrame) -> list[str]:
    valid = valid_trials(frame)
    ids = valid["candidate_id"].astype(str).tolist()
    points = {
        str(row.candidate_id): np.array(
            [
                float(row.hard_breach_days),
                float(row.infeasible_executed_days),
                -float(row.economic_total_return),
                float(row.economic_max_drawdown),
            ]
        )
        for row in valid.itertuples(index=False)
    }
    return sorted(
        cid
        for cid in ids
        if not any(
            np.all(points[other] <= points[cid]) and np.any(points[other] < points[cid])
            for other in ids
            if other != cid
        )
    )


def winner_rows(summary: pd.DataFrame, audit: dict[str, Any]) -> pd.DataFrame:
    rows = []
    audited = audit.get("ex_post_diagnostics", {})
    for strategy in REQUIRED_STRATEGIES:
        frame = summary[summary.strategy.eq(strategy)].copy()
        valid = valid_trials(frame)
        primary = primary_sort(valid).iloc[0]
        return_best = valid.sort_values(
            ["economic_total_return", "economic_max_drawdown", "candidate_id"],
            ascending=[False, True, True],
        ).iloc[0]
        incumbent = frame[frame.candidate_id.astype(str).eq("p000")].iloc[0]
        pareto = pareto_ids(frame)
        if strategy in audited:
            if str(primary.candidate_id) != str(audited[strategy].get("primary_best")):
                raise ValueError(f"{strategy}: independently computed primary winner disagrees with audit")
            if str(return_best.candidate_id) != str(audited[strategy].get("return_best")):
                raise ValueError(f"{strategy}: independently computed return winner disagrees with audit")
            if pareto != sorted(str(x) for x in audited[strategy].get("candidate_ids", [])):
                raise ValueError(f"{strategy}: independently computed Pareto set disagrees with audit")
        rows.append({
            "strategy": strategy,
            "incumbent": str(incumbent.candidate_id),
            "primary_best": str(primary.candidate_id),
            "return_best": str(return_best.candidate_id),
            "same_winner": str(primary.candidate_id) == str(return_best.candidate_id),
            "primary_return": float(primary.economic_total_return),
            "return_best_return": float(return_best.economic_total_return),
            "primary_hard_breaches": int(primary.hard_breach_days),
            "return_best_hard_breaches": int(return_best.hard_breach_days),
            "pareto_count": len(pareto),
            "pareto_ids": ", ".join(pareto),
        })
    return pd.DataFrame(rows)


def cash_evidence(output: Path) -> pd.DataFrame:
    locations = {
        "INCUMBENT": "baseline_replay",
        "EX_POST_BEST": "ex_post_best",
        "WALK_FORWARD": "walk_forward",
    }
    rows = []
    for role, folder in locations.items():
        for strategy in REQUIRED_STRATEGIES:
            equity = read_csv(output / folder / strategy / "equity.csv")
            cash = pd.to_numeric(equity["cash_ratio"], errors="raise")
            rows.append({
                "strategy": strategy,
                "role": role,
                "actual_cash_mean": float(cash.mean()),
                "actual_cash_min": float(cash.min()),
                "actual_cash_max": float(cash.max()),
                "days_cash_positive": int(cash.gt(1e-12).sum()),
                "sessions": len(equity),
            })
    return pd.DataFrame(rows)


def sector_evidence(output: Path) -> dict[str, Any]:
    snapshots = read_csv(output / "baseline_replay/B/snapshots.csv")
    records = []
    for raw in snapshots["metadata"]:
        try:
            record = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if "sector_status" in record:
            records.append(record)
    if not records:
        return {"statuses": {}, "known_min": None, "known_max": None,
                "unknown_min": None, "unknown_max": None, "sessions": 0}
    known = [int(row["sector_known_count"]) for row in records if "sector_known_count" in row]
    unknown = [int(row["sector_unknown_count"]) for row in records if "sector_unknown_count" in row]
    return {
        "statuses": dict(Counter(str(row["sector_status"]) for row in records)),
        "known_min": min(known) if known else None,
        "known_max": max(known) if known else None,
        "unknown_min": min(unknown) if unknown else None,
        "unknown_max": max(unknown) if unknown else None,
        "sessions": len(records),
    }


def trade_signature(path: Path) -> tuple[str, int]:
    trades = read_csv(path)
    columns = [
        column for column in ("date", "signal_date", "symbol", "trade_symbol", "shares")
        if column in trades
    ]
    if {"date", "signal_date", "symbol", "shares"} - set(columns):
        raise ValueError(f"Trade file lacks decision identity columns: {path}")
    canonical = trades[columns].copy()
    canonical = canonical.sort_values(columns).reset_index(drop=True)
    payload = canonical.to_csv(index=False, lineterminator="\n").encode()
    return hashlib.sha256(payload).hexdigest(), len(canonical)


def trade_differences(output: Path, study: dict[str, Any]) -> pd.DataFrame:
    """Compare matched candidate trade decisions, independent of signal metadata."""
    rows = []
    for candidate in study["candidates"]:
        cid = str(candidate["candidate_id"])
        signatures = {}
        counts = {}
        for strategy in REQUIRED_STRATEGIES:
            signature, count = trade_signature(
                output / "trials" / strategy / cid / "trades.csv"
            )
            signatures[strategy], counts[strategy] = signature, count
        rows.append({
            "candidate_id": cid,
            "trade_diff_A_B": signatures["A"] != signatures["B"],
            "trade_diff_B_C": signatures["B"] != signatures["C"],
            "trade_rows_A": counts["A"],
            "trade_rows_B": counts["B"],
            "trade_rows_C": counts["C"],
        })
    return pd.DataFrame(rows)


def monthly_digest(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    selected = monthly[monthly["role"].eq("WALK_FORWARD")].copy()
    for strategy in REQUIRED_STRATEGIES:
        frame = selected[selected.strategy.eq(strategy)].copy()
        if frame.empty:
            raise ValueError(f"Missing WALK_FORWARD monthly rows for {strategy}")
        frame["economic_return"] = pd.to_numeric(frame["economic_return"], errors="raise")
        best = frame.loc[frame.economic_return.idxmax()]
        worst = frame.loc[frame.economic_return.idxmin()]
        rows.append({
            "strategy": strategy,
            "months": len(frame),
            "positive_months": int(frame.economic_return.gt(0).sum()),
            "best_month": str(best.month),
            "best_return": float(best.economic_return),
            "worst_month": str(worst.month),
            "worst_return": float(worst.economic_return),
        })
    return pd.DataFrame(rows)


def all_trial_monthly(
    output: Path, study: dict[str, Any], summary: pd.DataFrame
) -> pd.DataFrame:
    """Collect the declared five-column monthly ledger for all completed trials."""
    rows: list[pd.DataFrame] = []
    expected_months: list[str] | None = None
    candidate_ids = [str(row["candidate_id"]) for row in study["candidates"]]
    for strategy in REQUIRED_STRATEGIES:
        for candidate_id in candidate_ids:
            monthly = read_csv(
                output / "trials" / strategy / candidate_id / "monthly.csv"
            )
            required = {"month", "economic_return", "economic_max_drawdown"}
            if required - set(monthly):
                raise ValueError(
                    f"{strategy}/{candidate_id}/monthly.csv missing "
                    f"{sorted(required - set(monthly))}"
                )
            if monthly["month"].duplicated().any():
                raise ValueError(f"Duplicate monthly rows for {strategy}/{candidate_id}")
            months = monthly["month"].astype(str).tolist()
            if expected_months is None:
                expected_months = months
            elif months != expected_months:
                raise ValueError(
                    f"Monthly coverage differs for {strategy}/{candidate_id}"
                )
            selected = monthly[
                ["month", "economic_return", "economic_max_drawdown"]
            ].copy()
            selected.insert(0, "candidate_id", candidate_id)
            selected.insert(0, "strategy", strategy)
            rows.append(selected)
    result = pd.concat(rows, ignore_index=True)
    if len(expected_months or []) != 21:
        raise ValueError(
            f"Expected 21 full-development months, got {len(expected_months or [])}"
        )
    expected_rows = len(summary) * len(expected_months or [])
    if len(result) != expected_rows:
        raise ValueError(
            f"Expected {expected_rows} all-trial monthly rows, got {len(result)}"
        )
    if result.duplicated(["strategy", "candidate_id", "month"]).any():
        raise ValueError("Duplicate strategy/candidate/month rows in monthly detail")
    if result[["economic_return", "economic_max_drawdown"]].isna().any().any():
        raise ValueError("Monthly return or MDD contains missing values")
    return result


def percent(value: Any, digits: int = 2) -> str:
    return f"{float(value) * 100:.{digits}f}%"


def number(value: Any, digits: int = 3) -> str:
    return f"{float(value):.{digits}f}"


def money(value: Any) -> str:
    return f"{float(value) / 1_000_000:.2f}M"


def comparison_order(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["_role_order"] = result["role"].map(ROLE_ORDER)
    return result.sort_values(["strategy", "_role_order"]).drop(columns="_role_order")


def primary_parameter_changes(
    summary: pd.DataFrame, study: dict[str, Any], winners: pd.DataFrame
) -> pd.DataFrame:
    parameter_names = list(study["candidates"][0]["params"])
    rows = []
    for strategy in REQUIRED_STRATEGIES:
        strategy_rows = summary[summary.strategy.eq(strategy)].copy()
        strategy_rows.index = strategy_rows.candidate_id.astype(str)
        incumbent = strategy_rows.loc["p000"]
        candidate_id = str(
            winners.loc[winners.strategy.eq(strategy), "primary_best"].iloc[0]
        )
        selected = strategy_rows.loc[candidate_id]
        changes = []
        for name in parameter_names:
            before, after = incumbent[name], selected[name]
            if str(before) != str(after):
                changes.append(f"{name}: {before} → {after}")
        rows.append({
            "strategy": strategy,
            "candidate_id": candidate_id,
            "changes": "; ".join(changes) if changes else "與原版相同",
            "unchanged": "其餘參數沿用原版",
        })
    return pd.DataFrame(rows)


def lead_finding(comparison: pd.DataFrame, summary: pd.DataFrame) -> str:
    full = comparison[comparison.scope.eq("FULL_DEVELOPMENT")]
    gains = []
    for strategy in REQUIRED_STRATEGIES:
        incumbent = full[
            full.strategy.eq(strategy) & full.role.eq("INCUMBENT")
        ].iloc[0]
        walk_forward = full[
            full.strategy.eq(strategy) & full.role.eq("WALK_FORWARD")
        ].iloc[0]
        if float(walk_forward.economic_total_return) > float(incumbent.economic_total_return):
            gains.append((strategy, incumbent, walk_forward))
    zero_hard = int(pd.to_numeric(summary.hard_breach_days).eq(0).sum())
    if len(gains) == 1:
        strategy, incumbent, walk_forward = gains[0]
        return (
            f"{len(summary)} 個試驗已全部完成；其中 {zero_hard} 個候選的 "
            "hard-breach days 為零。WALK_FORWARD 只有 "
            f"{strategy} 的經濟報酬高於原版 "
            f"{(float(walk_forward.economic_total_return) - float(incumbent.economic_total_return)) * 100:.2f} pp，"
            f"但成本增加 {money(float(walk_forward.costs) - float(incumbent.costs))} NTD、"
            f"hard-breach days 增加 {int(walk_forward.hard_breach_days - incumbent.hard_breach_days)}，"
            f"雙向換手增加 {float(walk_forward.turnover_two_way - incumbent.turnover_two_way):.2f}。"
        )
    improved = "、".join(item[0] for item in gains) or "沒有版本"
    return (
        f"{len(summary)} 個試驗已全部完成；其中 {zero_hard} 個候選的 "
        f"hard-breach days 為零。WALK_FORWARD 中只有 {improved} 的經濟報酬高於原版。"
    )


def markdown_table(frame: pd.DataFrame, columns: list[tuple[str, str, Any]]) -> str:
    lines = [
        "| " + " | ".join(label for _, label, _ in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for row in frame.itertuples(index=False):
        values = []
        data = row._asdict()
        for key, _, formatter in columns:
            value = data[key]
            rendered = formatter(value) if formatter else str(value)
            values.append(rendered.replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def comparison_plot(comparison: pd.DataFrame, path: Path) -> None:
    roles = list(REQUIRED_ROLES)
    colors = {"INCUMBENT": "#64748b", "EX_POST_BEST": "#dc2626", "WALK_FORWARD": "#2563eb"}
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
    for column, title, scale, axis in [
        ("economic_total_return", "Economic NAV total return", 100.0, axes[0, 0]),
        ("economic_max_drawdown", "Economic NAV maximum drawdown", 100.0, axes[0, 1]),
        ("turnover_two_way", "Two-way turnover", 1.0, axes[1, 0]),
        ("hard_breach_days", "Hard-breach days", 1.0, axes[1, 1]),
    ]:
        frame = comparison[comparison.scope.eq("FULL_DEVELOPMENT")]
        x = np.arange(len(REQUIRED_STRATEGIES)); width = 0.24
        for offset, role in enumerate(roles):
            values = [
                float(frame[(frame.strategy.eq(strategy)) & (frame.role.eq(role))][column].iloc[0]) * scale
                for strategy in REQUIRED_STRATEGIES
            ]
            bars = axis.bar(x + (offset - 1) * width, values, width, label=role.replace("_", " "), color=colors[role])
            labels = [f"{value:.1f}" if scale == 100.0 or column == "turnover_two_way" else f"{value:.0f}" for value in values]
            axis.bar_label(bars, labels=labels, fontsize=7, padding=2, rotation=90)
        axis.set_title(title)
        axis.set_xticks(x, REQUIRED_STRATEGIES)
        axis.grid(axis="y", alpha=0.25)
        axis.margins(y=0.18)
        if scale == 100.0:
            axis.set_ylabel("Percent")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.suptitle(
        "Audited strategy comparison — full development period",
        fontsize=15,
        y=0.985,
    )
    fig.legend(
        handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.948),
        ncol=3, frameon=False,
    )
    fig.subplots_adjust(top=0.86, hspace=0.30, wspace=0.18)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def search_plot(summary: pd.DataFrame, winners: pd.DataFrame, path: Path) -> None:
    colors = {"A": "#0f766e", "B": "#7c3aed", "C": "#ea580c"}
    fig, axis = plt.subplots(figsize=(11, 7), constrained_layout=True)
    for strategy in REQUIRED_STRATEGIES:
        frame = summary[summary.strategy.eq(strategy)]
        sizes = 22 + 4 * np.sqrt(np.maximum(pd.to_numeric(frame.turnover_two_way), 0))
        axis.scatter(
            frame.economic_max_drawdown.astype(float) * 100,
            frame.economic_total_return.astype(float) * 100,
            s=sizes,
            alpha=0.55,
            color=colors[strategy],
            label=f"Strategy {strategy}",
            edgecolors="none",
        )
        row = winners[winners.strategy.eq(strategy)].iloc[0]
        for cid, marker, label in [
            (row.primary_best, "D", f"{strategy} primary"),
            (row.return_best, "X", f"{strategy} return-best"),
        ]:
            point = frame[frame.candidate_id.astype(str).eq(str(cid))].iloc[0]
            axis.scatter(
                float(point.economic_max_drawdown) * 100,
                float(point.economic_total_return) * 100,
                marker=marker,
                s=125,
                color=colors[strategy],
                edgecolor="black",
                linewidth=0.8,
                label=label,
                zorder=5,
            )
    axis.set_xlabel("Economic NAV maximum drawdown (%)")
    axis.set_ylabel("Economic NAV total return (%)")
    axis.set_title(
        f"All {len(summary)} development candidates\n"
        "Point size reflects two-way turnover"
    )
    axis.grid(alpha=0.25)
    axis.legend(ncol=3, fontsize=8, frameon=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def sensitivity_plot(summary: pd.DataFrame, study: dict[str, Any], path: Path) -> None:
    design = {str(row["candidate_id"]): row for row in study["candidates"]}
    ids = [
        cid for cid, row in design.items()
        if row["design"] == "one_factor"
    ]
    if len(ids) != 25:
        raise ValueError(f"Expected 25 one-factor candidates, got {len(ids)}")
    labels = []
    for cid in ids:
        row = design[cid]
        varied = row["varied"]
        if varied == "volume_range":
            value = f"{row['params']['volume_low']}/{row['params']['volume_high']}"
        else:
            value = row["params"][varied]
        labels.append(f"{cid} {varied}={value}")

    returns = np.zeros((len(ids), 3)); breaches = np.zeros((len(ids), 3))
    for column, strategy in enumerate(REQUIRED_STRATEGIES):
        frame = summary[summary.strategy.eq(strategy)].copy()
        frame.index = frame.candidate_id.astype(str)
        baseline = frame.loc["p000"]
        for row, cid in enumerate(ids):
            returns[row, column] = (
                float(frame.loc[cid, "economic_total_return"])
                - float(baseline["economic_total_return"])
            ) * 100
            breaches[row, column] = (
                float(frame.loc[cid, "hard_breach_days"])
                - float(baseline["hard_breach_days"])
            )
    bound = max(float(np.abs(returns).max()), 0.01)
    breach_bound = max(float(np.abs(breaches).max()), 1.0)
    fig, axes = plt.subplots(1, 2, figsize=(12, 11), constrained_layout=True)
    for axis, matrix, title, limit, fmt in [
        (axes[0], returns, "Return change vs incumbent (pp)", bound, ".1f"),
        (axes[1], breaches, "Hard-breach day change", breach_bound, ".0f"),
    ]:
        image = axis.imshow(matrix, aspect="auto", cmap="RdBu_r", vmin=-limit, vmax=limit)
        axis.set_title(title)
        axis.set_xticks(range(3), REQUIRED_STRATEGIES)
        axis.set_yticks(range(len(labels)), labels if axis is axes[0] else [""] * len(labels), fontsize=7)
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                axis.text(j, i, format(matrix[i, j], fmt), ha="center", va="center", fontsize=6)
        fig.colorbar(image, ax=axis, shrink=0.65)
    fig.suptitle(
        f"One-factor-at-a-time sensitivity ({len(ids)} predeclared probes)",
        fontsize=14,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def table_comparison_plot(
    comparison: pd.DataFrame,
    summary: pd.DataFrame,
    winners: pd.DataFrame,
    path: Path,
) -> None:
    """Render the four decision roles for each strategy over one full period."""
    full = comparison[comparison.scope.eq("FULL_DEVELOPMENT")]
    rows = []
    role_specs = (
        ("Incumbent", "INCUMBENT"),
        ("Return max (ex-post)", None),
        ("Rule-first (ex-post)", "EX_POST_BEST"),
        ("Walk-forward", "WALK_FORWARD"),
    )
    for strategy in REQUIRED_STRATEGIES:
        diagnostic = winners[winners.strategy.eq(strategy)].iloc[0]
        for label, comparison_role in role_specs:
            if comparison_role is None:
                candidate_id = str(diagnostic.return_best)
                source = summary[
                    summary.strategy.eq(strategy)
                    & summary.candidate_id.astype(str).eq(candidate_id)
                ].iloc[0]
            else:
                source = full[
                    full.strategy.eq(strategy) & full.role.eq(comparison_role)
                ].iloc[0]
                candidate_id = str(source.candidate_id)
            rows.append([
                strategy,
                label,
                candidate_id,
                f"{float(source.economic_total_return) * 100:.2f}%",
                f"{float(source.economic_max_drawdown) * 100:.2f}%",
                str(int(source.hard_breach_days)),
                f"{float(source.turnover_two_way):.2f}",
            ])
    columns = ["Strategy", "Role", "Candidate", "Return", "MDD", "Hard breach", "Turnover"]
    period_start = str(summary["start"].astype(str).min())
    period_end = str(summary["end"].astype(str).max())
    fig, axis = plt.subplots(figsize=(12.5, 6.6))
    axis.axis("off")
    table = axis.table(
        cellText=rows,
        colLabels=columns,
        cellLoc="center",
        colLoc="center",
        loc="center",
        colWidths=[0.08, 0.23, 0.11, 0.12, 0.12, 0.14, 0.12],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.0, 1.65)
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#cbd5e1")
        if row == 0:
            cell.set_facecolor("#dbeafe")
            cell.set_text_props(weight="bold")
        elif (row - 1) // 4 % 2 == 1:
            cell.set_facecolor("#f8fafc")
        if row > 0 and column == 0 and (row - 1) % 4 == 0:
            cell.set_text_props(weight="bold")
    axis.set_title(
        "Full-development numeric comparison\n"
        f"{period_start} → {period_end}\n"
        "Economic NAV after commission and sell tax",
        fontsize=15,
        pad=20,
    )
    fig.text(
        0.5,
        0.025,
        "Hard breach = research proxy days; not official warnings",
        ha="center",
        va="bottom",
        fontsize=9,
        color="#475569",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def image_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def full_table_columns(study: dict[str, Any], summary: pd.DataFrame) -> list[str]:
    params = list(study["candidates"][0]["params"])
    metrics = [
        "economic_total_return", "economic_max_drawdown", "sharpe_zero_rf",
        "turnover_two_way", "costs", "hard_breach_days", "infeasible_executed_days",
        "negative_cash_days", "raw_rule_breach_days", "trades", "trade_days",
    ]
    return [
        "strategy", "candidate_id", "economic_total_return", "economic_max_drawdown",
        "hard_breach_days", "selection_flags", "trade_diff_A_B", "trade_diff_B_C",
        "turnover_two_way", "costs", "design", "varied", "parameter_scope", "status",
        *params,
        *[
            column for column in metrics
            if column in summary and column not in {
                "economic_total_return", "economic_max_drawdown", "hard_breach_days",
                "turnover_two_way", "costs",
            }
        ],
        "trade_rows_A", "trade_rows_B", "trade_rows_C",
    ]


def prepare_full_table(
    summary: pd.DataFrame,
    study: dict[str, Any],
    trade_flags: pd.DataFrame,
    winners: pd.DataFrame,
) -> pd.DataFrame:
    frame = summary.copy()
    frame["candidate_id"] = frame["candidate_id"].astype(str)
    flags = trade_flags.copy()
    flags["candidate_id"] = flags["candidate_id"].astype(str)
    frame = frame.merge(flags, on="candidate_id", how="left", validate="many_to_one")
    selection_flags = {}
    for row in winners.itertuples(index=False):
        pareto = {value.strip() for value in str(row.pareto_ids).split(",") if value.strip()}
        for candidate_id in frame.loc[frame.strategy.eq(row.strategy), "candidate_id"]:
            labels = []
            if candidate_id == "p000":
                labels.append("incumbent")
            if candidate_id == str(row.primary_best):
                labels.append("primary")
            if candidate_id == str(row.return_best):
                labels.append("return-best")
            if candidate_id in pareto:
                labels.append("pareto")
            selection_flags[(row.strategy, candidate_id)] = ", ".join(labels)
    frame["selection_flags"] = [
        selection_flags[(row.strategy, row.candidate_id)]
        for row in frame[["strategy", "candidate_id"]].itertuples(index=False)
    ]
    frame["parameter_scope"] = frame["strategy"].map({
        "A": "core only; sector/C params inactive",
        "B": "core + sector; c_alpha inactive",
        "C": "core + sector + c_alpha",
    })
    return frame[full_table_columns(study, frame)].sort_values(["strategy", "candidate_id"])


def html_table(frame: pd.DataFrame, table_id: str) -> str:
    numeric = {
        column for column in frame
        if pd.api.types.is_numeric_dtype(frame[column])
    }
    headers = "".join(
        f'<th class="key-col key-{i}"><button type="button" onclick="sortTable(\'{table_id}\',{i},{str(column in numeric).lower()})">'
        f"{html.escape(column)} <span>↕</span></button></th>"
        for i, column in enumerate(frame.columns)
    )
    body = []
    for row in frame.itertuples(index=False, name=None):
        cells = []
        for column, value in zip(frame.columns, row):
            if column in {"economic_total_return", "economic_max_drawdown"}:
                display = percent(value, 3)
                sort_value = float(value)
            elif column == "costs":
                display = money(value)
                sort_value = float(value)
            elif column in numeric:
                display = number(value, 5) if isinstance(value, (float, np.floating)) else str(value)
                sort_value = float(value)
            else:
                display = str(value)
                sort_value = display.lower()
            cells.append(f'<td class="key-col key-{len(cells)}" data-value="{html.escape(str(sort_value))}">{html.escape(display)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<div class="table-wrap"><table id="{table_id}"><thead><tr>{headers}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def search_boundaries(study: dict[str, Any]) -> str:
    rows = []
    for name, values in study["spaces"].items():
        rows.append(f"<tr><td>{html.escape(name)}</td><td>{html.escape(', '.join(map(str, values)))}</td></tr>")
    return '<table class="compact"><thead><tr><th>Parameter</th><th>Predeclared values</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>"


def formatted_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["role"] = result["role"].map(ROLE_LABELS_ZH).fillna(result["role"])
    for column in ("economic_total_return", "economic_max_drawdown"):
        result[column] = result[column].map(lambda value: percent(value, 2))
    result["turnover_two_way"] = result["turnover_two_way"].map(lambda value: number(value, 2))
    result["costs"] = result["costs"].map(money)
    for column in ("hard_breach_days", "infeasible_executed_days"):
        result[column] = result[column].map(lambda value: str(int(float(value))))
    return result


def render_markdown(
    output: Path,
    data: dict[str, Any],
    winners: pd.DataFrame,
    cash: pd.DataFrame,
    sector: dict[str, Any],
    trade_flags: pd.DataFrame,
    monthly_summary: pd.DataFrame,
) -> str:
    comparison = data["comparison"]
    full = comparison_order(comparison[comparison.scope.eq("FULL_DEVELOPMENT")])
    after = comparison_order(comparison[comparison.scope.eq("AFTER_SELECTION_START")])
    schedule = data["schedule"].sort_values(["selection_cutoff", "strategy"])
    study = data["study"]
    audit = data["audit"]
    prefix_audit = data["prefix_audit"]
    summary = data["summary"]
    trial_count = len(summary)
    candidate_count = int(summary.groupby("strategy").candidate_id.nunique().iloc[0])
    month_count = int(data["monthly"]["month"].astype(str).nunique())
    monthly_rows = trial_count * month_count
    ofat = sum(row["design"] == "one_factor" for row in study["candidates"])
    joint = sum(row["design"] == "joint_stratified" for row in study["candidates"])
    statuses = ", ".join(f"{key}: {value}" for key, value in sector["statuses"].items()) or "無可讀 metadata"
    params = pd.DataFrame([
        {"candidate_id": str(row["candidate_id"]), **row["params"]}
        for row in study["candidates"]
    ])
    trade = trade_flags.merge(params[["candidate_id", "sector_fallback_mode", "c_alpha"]], on="candidate_id", validate="one_to_one")
    ab_total = int(trade.trade_diff_A_B.sum()); bc_total = int(trade.trade_diff_B_C.sum())
    baseline = trade[trade.sector_fallback_mode.eq("baseline")]
    total_eligible = trade[trade.sector_fallback_mode.eq("total_eligible")]
    period_start = str(summary["start"].astype(str).min())
    period_end = str(summary["end"].astype(str).max())
    after_start = str(schedule["effective_trade_date"].astype(str).min())
    initial_cash = float(data["manifest"]["config"]["initial_cash"])
    commission = float(data["manifest"]["config"]["commission"])
    sell_tax = float(data["manifest"]["config"]["sell_tax"])
    lead = lead_finding(comparison, summary)
    primary_params = primary_parameter_changes(summary, study, winners)

    return f"""# A/B/C 參數研究審核報告

## 研究範圍與主要結論

比較期間為 **{period_start}–{period_end}**，初始本金 **{initial_cash / 100_000_000:.2f} 億 NTD**。報酬與回撤一律使用已扣 {commission * 100:.4f}% 手續費及 {sell_tax * 100:.2f}% 賣出稅的 economic NAV。

{lead}

本報告只在 `{html.escape(str(output.name))}/tuning_audit.json` 為 **PASS** 後生成。{trial_count} 筆結果全部來自已反覆檢視的開發期間，沒有 pristine unseen test；任何事後贏家、純收益最高候選或 Pareto 候選都不能直接推為 LIVE。

選擇主規則先排除負現金，再依序最小化 hard-breach days、infeasible executed days，之後才最大化 economic return、最小化 MDD。因此 **return-best 不等於 primary best**，除非兩者剛好是同一候選。

- [離線互動報告](v2_abc_tuning.html)
- [完整 {trial_count} 候選 CSV](../outputs/{output.name}/trial_summary.csv)
- [全部候選完整開發期月度明細 CSV（{monthly_rows} 列）](../outputs/{output.name}/all_trial_monthly.csv)
- [審核 JSON](../outputs/{output.name}/tuning_audit.json)
- [Walk-forward 獨立前綴稽核](../outputs/{output.name}/walk_forward_prefix_audit.json)

## 完整開發期比較

{markdown_table(full, [
    ('strategy','版本',None), ('role','角色',lambda x: ROLE_LABELS_ZH.get(str(x), str(x))),
    ('candidate_id','候選',None), ('economic_total_return','總報酬',percent),
    ('economic_max_drawdown','MDD',percent), ('turnover_two_way','雙向換手',lambda x:number(x,2)),
    ('costs','成本',money), ('hard_breach_days','Hard breach',lambda x:str(int(float(x)))),
])}

`EX_POST_BEST` 是依完整開發資料套用 primary 合規代理規則後的贏家；`WALK_FORWARD` 是單一連續帳本，在四個 cutoff 只用當時以前的開發資料選參數。它仍是 retrospective walk-forward，不是未見 OOS。

![Audited comparison](../outputs/{output.name}/comparison.png)

![Full-period numeric table](../outputs/{output.name}/table_comparison.png)

## Selection-start 之後（{after_start}–{period_end}）

{markdown_table(after, [
    ('strategy','版本',None), ('role','角色',lambda x: ROLE_LABELS_ZH.get(str(x), str(x))),
    ('candidate_id','候選',None), ('economic_total_return','總報酬',percent),
    ('economic_max_drawdown','MDD',percent), ('turnover_two_way','雙向換手',lambda x:number(x,2)),
    ('hard_breach_days','Hard breach',lambda x:str(int(float(x)))),
])}

這個切片只改變報告區間。因為 cutoff 與候選已在同一批歷史資料上設計，它不是新的驗證集。

## Ex-post 診斷

{markdown_table(winners, [
    ('strategy','版本',None), ('primary_best','規則優先',None),
    ('return_best','純收益最高',None),
    ('primary_return','Primary 報酬',percent), ('return_best_return','Return-best 報酬',percent),
    ('primary_hard_breaches','Primary breach',None), ('return_best_hard_breaches','Return-best breach',None),
    ('pareto_count','Pareto 數',None),
])}

Pareto 與贏家旗標已放入 HTML 完整表；候選明細可由完整 CSV 與 audit JSON 追溯。

規則優先候選的參數差異：

{markdown_table(primary_params, [
    ('strategy','版本',None), ('candidate_id','候選',None),
    ('changes','相對原版變更',None), ('unchanged','其餘參數',None),
])}

上限只限制一般替換；強制退出、補足持股與風控修正仍會交易，0 不代表停止交易。

![All candidate search](../outputs/{output.name}/search_scatter.png)

## 成交層級介入

同一 candidate ID 的核心參數完全相同。逐候選比較 `date / signal_date / symbol / trade_symbol / signed shares` 後，A/B 有 {ab_total}/{candidate_count} 組產生真正不同成交，B/C 有 {bc_total}/{candidate_count} 組不同。A/B 在 `baseline` fallback 為 {int(baseline.trade_diff_A_B.sum())}/{len(baseline)}，在 `total_eligible` 為 {int(total_eligible.trade_diff_A_B.sum())}/{len(total_eligible)}。完整總表的 `trade_diff_A_B`、`trade_diff_B_C` 可逐列篩選；這比只看到 gate metadata 更能證明策略介入是否落到成交。

## Retrospective schedule

{markdown_table(schedule, [
    ('strategy','版本',None), ('selection_cutoff','Selection cutoff',None),
    ('effective_trade_date','生效交易日',None), ('candidate_id','候選',None),
    ('selection_scope','範圍',None),
])}

WALK_FORWARD 月度摘要：

{markdown_table(monthly_summary, [
    ('strategy','版本',None), ('months','月數',None), ('positive_months','正報酬月',None),
    ('best_month','最佳月',None), ('best_return','最佳月報酬',percent),
    ('worst_month','最差月',None), ('worst_return','最差月報酬',percent),
])}

獨立 prefix replay 在 `{prefix_audit['cutpoint']}` 截斷 {prefix_audit['strategy']}，重建 {prefix_audit['sessions']} 個 session、{prefix_audit['trades']} 筆成交、{prefix_audit['selection_rows_verified']} 筆選擇列與 {prefix_audit['switch_days_rebuilt']} 個切換日；最大 economic NAV 絕對差為 {float(prefix_audit['max_economic_nav_abs_error']):.3g} NTD。這支持排程與單一連續帳本的因果執行語意，但單一前綴仍不能證明供應商資料在原始時點可得，也不是 prospective OOS。

## 敏感度與搜尋邊界

每個版本公平使用相同的 {candidate_count} 組核心候選：1 個 incumbent、{ofat} 個 one-factor-at-a-time probes、{joint} 個事前分層 joint bundles。OFAT 每次只改一個核心因素，可以作局部單因子描述；joint bundle 同時改多項參數，只能描述整體組合，不能作單一因果歸因。A 的 sector/C 參數不作用，B 的 `c_alpha` 不作用，C 才使用全部條件參數。

![OFAT sensitivity](../outputs/{output.name}/sensitivity.png)

完整搜尋空間、每筆參數與結果請使用 HTML 全表或 CSV。全表包含 core/conditional parameters、return、MDD、turnover、cost、hard breaches，以及其他合規欄位。

## 限制與合規狀態

Audit 為 `{audit.get('status')}`；{trial_count} 個候選全數完成，A/B/C 各 {candidate_count}。報告以合規代理主規則為優先，也沒有依結果事後縮窄參數範圍。

Strategic cash target 是 `{data['manifest']['config'].get('cash_target')}`，這不保證實際現金為零。九個比較帳本的實際平均 cash ratio 範圍為 {percent(cash.actual_cash_mean.min())}–{percent(cash.actual_cash_mean.max())}，最大觀察值為 {percent(cash.actual_cash_max.max())}。

Baseline B sector metadata 為 {statuses}；已知分類範圍 {sector['known_min']}–{sector['known_max']}，未知範圍 {sector['unknown_min']}–{sector['unknown_max']}。板塊覆蓋仍是 partial，不能把未知分類當作板塊證據。Historical ETF holdings / Active Share 尚未驗證，因此所有結果維持 research shadow 與 BLOCK_SUBMISSION，不能視為正式認證或實盤可成交績效。

2026-09 只涵蓋至 9 月 21 日。Hard-breach days 是研究用代理指標，不是官方違規次數；Historical ETF holdings、歷史白名單與 Active Share 尚未驗證。64×3 個候選是事前界定的廣泛有限搜尋，不是窮舉，也不能宣稱全域最佳。
"""


def render_html(
    output: Path,
    data: dict[str, Any],
    winners: pd.DataFrame,
    cash: pd.DataFrame,
    sector: dict[str, Any],
    trade_flags: pd.DataFrame,
    monthly_summary: pd.DataFrame,
    full_table: pd.DataFrame,
    figures: dict[str, Path],
) -> str:
    comparison = data["comparison"]
    full = comparison_order(comparison[comparison.scope.eq("FULL_DEVELOPMENT")])
    after = comparison_order(comparison[comparison.scope.eq("AFTER_SELECTION_START")])
    schedule = data["schedule"].sort_values(["selection_cutoff", "strategy"])
    study = data["study"]
    audit = data["audit"]
    prefix_audit = data["prefix_audit"]
    statuses = ", ".join(f"{key}: {value}" for key, value in sector["statuses"].items()) or "無可讀 metadata"
    params = pd.DataFrame([
        {"candidate_id": str(row["candidate_id"]), **row["params"]}
        for row in study["candidates"]
    ])
    trade = trade_flags.merge(params[["candidate_id", "sector_fallback_mode", "c_alpha"]], on="candidate_id", validate="one_to_one")
    ab_total = int(trade.trade_diff_A_B.sum()); bc_total = int(trade.trade_diff_B_C.sum())
    baseline = trade[trade.sector_fallback_mode.eq("baseline")]
    total_eligible = trade[trade.sector_fallback_mode.eq("total_eligible")]
    trial_count = len(full_table)
    candidate_count = len(trade_flags)
    month_count = int(data["monthly"]["month"].astype(str).nunique())
    monthly_rows = trial_count * month_count
    ofat_count = sum(row["design"] == "one_factor" for row in study["candidates"])
    joint_count = sum(row["design"] == "joint_stratified" for row in study["candidates"])
    period_start = str(data["summary"]["start"].astype(str).min())
    period_end = str(data["summary"]["end"].astype(str).max())
    after_start = str(schedule["effective_trade_date"].astype(str).min())
    initial_cash = float(data["manifest"]["config"]["initial_cash"])
    commission = float(data["manifest"]["config"]["commission"])
    sell_tax = float(data["manifest"]["config"]["sell_tax"])
    lead = lead_finding(comparison, data["summary"])
    primary_params = primary_parameter_changes(data["summary"], study, winners)

    def simple_table(frame: pd.DataFrame, columns: list[str]) -> str:
        return frame[columns].to_html(index=False, classes="compact", border=0, escape=True)

    strategy_column = int(full_table.columns.get_loc("strategy"))
    design_column = int(full_table.columns.get_loc("design"))

    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A/B/C 參數研究審核報告</title>
<style>
:root{{--ink:#172033;--muted:#657087;--line:#d9deea;--paper:#fff;--soft:#f5f7fb;--accent:#2457d6;--warn:#9a3412}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--soft);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1420px;margin:auto;background:var(--paper);padding:38px 46px 70px;box-shadow:0 0 28px #1e293b12}}
h1{{font-size:30px;margin:0 0 8px}} h2{{font-size:21px;margin:34px 0 10px;border-top:1px solid var(--line);padding-top:24px}}
p,li{{max-width:980px}} .lede{{font-size:17px;color:#334155}} .notice{{border-left:4px solid var(--warn);background:#fff7ed;padding:12px 16px;max-width:1060px}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;margin:22px 0}} .card{{padding:16px;background:var(--soft);border:1px solid var(--line);border-radius:8px}} .card b{{display:block;font-size:22px}}
a{{color:var(--accent)}} img{{max-width:100%;height:auto;border:1px solid var(--line);border-radius:8px;background:white}}
.table-wrap{{overflow:auto;max-height:72vh;border:1px solid var(--line);border-radius:7px}} table{{border-collapse:separate;border-spacing:0;width:100%;font-size:12px}} th,td{{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;white-space:nowrap}} th{{position:sticky;top:0;background:#eaf0ff;z-index:4}} th button{{all:unset;cursor:pointer;font-weight:700}} tbody tr:nth-child(even){{background:#fafbfe}} tbody tr:hover td{{background:#fff7d6}}
#trials .key-0{{position:sticky;left:0;min-width:58px;background:#f4f7ff;z-index:3}} #trials .key-1{{position:sticky;left:58px;min-width:78px;background:#f4f7ff;z-index:3}} #trials .key-2{{position:sticky;left:136px;min-width:125px;background:#f4f7ff;z-index:3}} #trials .key-3{{position:sticky;left:261px;min-width:138px;background:#f4f7ff;z-index:3}} #trials .key-4{{position:sticky;left:399px;min-width:115px;background:#f4f7ff;z-index:3}} #trials thead .key-col{{z-index:6;background:#dfe8ff}}
table.compact{{font-size:13px;width:auto;max-width:100%;display:block;overflow:auto}} table.compact th{{position:static}}
.filters{{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}} input,select{{padding:8px 10px;border:1px solid #b8c0d1;border-radius:6px;background:white}} .muted{{color:var(--muted)}} details{{margin:12px 0}} summary{{cursor:pointer;font-weight:700}}
@media(max-width:800px){{main{{padding:24px 16px}}.cards{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<h1>A/B/C 參數研究審核報告</h1>
<p class="lede">{period_start}–{period_end}；初始本金 {initial_cash / 100_000_000:.2f} 億 NTD。所有報酬與回撤均為已扣 {commission * 100:.4f}% 手續費及 {sell_tax * 100:.2f}% 賣出稅的 economic NAV。</p>
<p><b>{html.escape(lead)}</b></p>
<p>完整 {trial_count} 候選、三版公平各 {candidate_count} 組。Audit：<b>{html.escape(str(audit.get('status')))}</b>。</p>
<p class="notice"><b>開發資料界線：</b>所有日期都已用於設計或選擇，沒有 pristine unseen test。EX_POST_BEST、return-best、Pareto 與 retrospective walk-forward 都不能直接推為 LIVE。</p>
<div class="cards"><div class="card">候選總數<b>{trial_count}</b></div><div class="card">每版候選<b>{candidate_count}</b></div><div class="card">OFAT probes<b>{ofat_count}</b></div><div class="card">Joint bundles<b>{joint_count}</b></div></div>
<p><a href="../outputs/{html.escape(output.name)}/trial_summary.csv">下載完整候選 CSV</a> · <a href="../outputs/{html.escape(output.name)}/all_trial_monthly.csv">下載全部候選完整開發期月度明細（{monthly_rows} 列）</a> · <a href="../outputs/{html.escape(output.name)}/tuning_audit.json">查看 audit JSON</a> · <a href="../outputs/{html.escape(output.name)}/walk_forward_prefix_audit.json">查看 prefix audit</a></p>

<h2>如何選擇</h2>
<p>Primary 規則先要求有效會計與非負現金，再依序最小化 hard-breach days、infeasible executed days，之後才最大化 economic return、最小化 MDD。收益最高不等於 primary winner；兩者只在候選相同時重合。</p>

<h2>完整開發期比較</h2>
{simple_table(formatted_comparison(full), ['strategy','role','candidate_id','economic_total_return','economic_max_drawdown','turnover_two_way','costs','hard_breach_days','infeasible_executed_days'])}
<p class="muted">INCUMBENT 是 p000；EX_POST_BEST 使用完整開發資料依 primary 規則選出；WALK_FORWARD 使用單一連續帳本與 past-only cutoff，但仍屬 retrospective development replay。</p>
<img alt="Audited strategy comparison" src="{image_uri(figures['comparison'])}">
<img alt="Full-period numeric comparison table" src="{image_uri(figures['table'])}">

<h2>Selection-start 之後（{after_start}–{period_end}）</h2>
{simple_table(formatted_comparison(after), ['strategy','role','candidate_id','economic_total_return','economic_max_drawdown','turnover_two_way','costs','hard_breach_days','infeasible_executed_days'])}
<p class="muted">這只是報告切片；cutoff 與候選仍來自同一批已檢視歷史資料。</p>

<h2>Ex-post 診斷</h2>
{simple_table(winners.assign(primary_return=winners.primary_return.map(percent),return_best_return=winners.return_best_return.map(percent)), ['strategy','primary_best','return_best','primary_return','return_best_return','primary_hard_breaches','return_best_hard_breaches','pareto_count'])}
<p>純收益最高與 Pareto 集均為 ex-post 診斷，不是 LIVE 建議。Pareto 與贏家旗標可在 HTML 完整表篩選，詳細候選則由完整 CSV 與 audit JSON 追溯。</p>
<h3>規則優先候選的參數差異</h3>
{simple_table(primary_params, ['strategy','candidate_id','changes','unchanged'])}
<p>上限只限制一般替換；強制退出、補足持股與風控修正仍會交易，0 不代表停止交易。</p>
<img alt="All candidate search" src="{image_uri(figures['search'])}">

<h2>成交層級介入</h2>
<p>同一 candidate ID 的核心參數完全相同。逐候選比較成交日期、訊號日、標的與 signed shares 後，A/B 有 <b>{ab_total}/{candidate_count}</b> 組產生真正不同成交，B/C 有 <b>{bc_total}/{candidate_count}</b> 組不同。A/B 在 baseline fallback 為 {int(baseline.trade_diff_A_B.sum())}/{len(baseline)}，在 total_eligible 為 {int(total_eligible.trade_diff_A_B.sum())}/{len(total_eligible)}。完整總表保留逐候選差異旗標，避免把 gate metadata 誤當成有效策略介入。</p>

<h2>Retrospective schedule</h2>
{simple_table(schedule, ['strategy','selection_cutoff','effective_signal_date','effective_trade_date','candidate_id','selection_scope'])}
<h3>WALK_FORWARD 月度摘要</h3>
{simple_table(monthly_summary.assign(best_return=monthly_summary.best_return.map(percent),worst_return=monthly_summary.worst_return.map(percent)), list(monthly_summary.columns))}
<p>獨立 prefix replay 在 <b>{html.escape(str(prefix_audit['cutpoint']))}</b> 截斷 {html.escape(str(prefix_audit['strategy']))}，重建 {int(prefix_audit['sessions'])} 個 session、{int(prefix_audit['trades'])} 筆成交、{int(prefix_audit['selection_rows_verified'])} 筆選擇列與 {int(prefix_audit['switch_days_rebuilt'])} 個切換日；最大 economic NAV 絕對差為 {float(prefix_audit['max_economic_nav_abs_error']):.3g} NTD。這支持排程與單一連續帳本的因果執行語意；單一前綴仍不能證明供應商資料在原始時點可得，也不是 prospective OOS。</p>

<h2>敏感度與搜尋邊界</h2>
<p>{ofat_count} 個 OFAT probes 每次只改一個核心因素，可作局部單因子描述。{joint_count} 個 joint bundles 同時改多項參數，只能描述整體組合，不能作單一因果歸因。A 的 sector/C 參數不作用；B 的 c_alpha 不作用；C 才使用全部條件參數。</p>
<img alt="OFAT sensitivity" src="{image_uri(figures['sensitivity'])}">
<details><summary>顯示預宣告搜尋空間</summary>{search_boundaries(study)}</details>

<h2>限制與現金解讀</h2>
<p>Strategic cash target 是 {html.escape(str(data['manifest']['config'].get('cash_target')))}，不代表實際現金必為零。九個帳本的實際平均 cash ratio 範圍為 {percent(cash.actual_cash_mean.min())}–{percent(cash.actual_cash_mean.max())}，最大值為 {percent(cash.actual_cash_max.max())}。</p>
<p>Baseline B sector metadata：{html.escape(statuses)}；已知分類範圍 {sector['known_min']}–{sector['known_max']}，未知範圍 {sector['unknown_min']}–{sector['unknown_max']}。Sector 證據仍是 partial。2026-09 只涵蓋至 9 月 21 日。</p>
<p>Hard-breach days 是研究用代理指標，不是官方違規次數；Historical ETF holdings、歷史白名單與 Active Share 仍未驗證。64×3 個候選是事前界定的廣泛有限搜尋，不是窮舉，也不能宣稱全域最佳。所有結果維持 research shadow 與 BLOCK_SUBMISSION。</p>

<h2>完整 {trial_count} 候選</h2>
<p>下表內嵌全部候選、完整 core/conditional parameters 與 return、MDD、turnover、cost、hard breach 等欄位。點欄名排序；搜尋與下拉選單可組合使用。</p>
<div class="filters"><input id="query" type="search" placeholder="搜尋任意欄位" oninput="filterRows()"><select id="strategy" onchange="filterRows()"><option value="">全部版本</option><option>A</option><option>B</option><option>C</option></select><select id="design" onchange="filterRows()"><option value="">全部設計</option><option>incumbent</option><option>one_factor</option><option>joint_stratified</option></select><span id="shown" class="muted"></span></div>
{html_table(full_table, 'trials')}

<script>
let directions={{}};
function sortTable(id,col,numeric){{const table=document.getElementById(id),body=table.tBodies[0],rows=[...body.rows],key=id+':'+col;directions[key]=!(directions[key]??false);const asc=directions[key];rows.sort((a,b)=>{{let x=a.cells[col].dataset.value,y=b.cells[col].dataset.value;if(numeric){{x=parseFloat(x);y=parseFloat(y);return asc?x-y:y-x}}return asc?x.localeCompare(y):y.localeCompare(x)}});rows.forEach(r=>body.appendChild(r));}}
function filterRows(){{const q=document.getElementById('query').value.toLowerCase(),s=document.getElementById('strategy').value,d=document.getElementById('design').value,rows=[...document.querySelectorAll('#trials tbody tr')];let n=0;rows.forEach(r=>{{const ok=(!q||r.textContent.toLowerCase().includes(q))&&(!s||r.cells[{strategy_column}].textContent===s)&&(!d||r.cells[{design_column}].textContent===d);r.style.display=ok?'':'none';if(ok)n++}});document.getElementById('shown').textContent='顯示 '+n+' / '+rows.length;}}
filterRows();
</script></main></body></html>"""


def run(output: Path, report_path: Path) -> None:
    data = validate_inputs(output)
    winners = winner_rows(data["summary"], data["audit"])
    cash = cash_evidence(output)
    sector = sector_evidence(output)
    trade_flags = trade_differences(output, data["study"])
    month_summary = monthly_digest(data["monthly"])
    monthly_detail = all_trial_monthly(output, data["study"], data["summary"])
    figures = {
        "comparison": output / "comparison.png",
        "search": output / "search_scatter.png",
        "sensitivity": output / "sensitivity.png",
        "table": output / "table_comparison.png",
    }
    comparison_plot(data["comparison"], figures["comparison"])
    search_plot(data["summary"], winners, figures["search"])
    sensitivity_plot(data["summary"], data["study"], figures["sensitivity"])
    table_comparison_plot(data["comparison"], data["summary"], winners, figures["table"])
    full_table = prepare_full_table(
        data["summary"], data["study"], trade_flags, winners
    )
    markdown = render_markdown(output, data, winners, cash, sector, trade_flags, month_summary)
    html_text = render_html(output, data, winners, cash, sector, trade_flags, month_summary, full_table, figures)
    atomic_write_csv(output / "all_trial_monthly.csv", monthly_detail)
    atomic_write(report_path, markdown)
    atomic_write(report_path.with_suffix(".html"), html_text)
    print(json.dumps({
        "status": "GENERATED_FROM_AUDIT_PASS",
        "markdown": str(report_path.relative_to(ROOT)),
        "html": str(report_path.with_suffix('.html').relative_to(ROOT)),
        "figures": [str(path.relative_to(ROOT)) for path in figures.values()],
        "trials": len(full_table),
        "monthly_rows": len(monthly_detail),
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
