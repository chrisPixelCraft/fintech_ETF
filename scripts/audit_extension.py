"""Independent ledger and multi-cutoff audit of the continuous 2025–2026 run.

Reconstructs positions from initial cash, corporate actions and executed trades.
Saved holdings and metrics are assertions to check, never the reconstruction input.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from src.backtest import aggregate_four_hour, file_hash, run_backtest


VARIANTS = {
    "v1": {},
    "without_4h_direction": {"use_4h": False, "match_4h_coverage": True},
    "without_turnover_margin": {"replacement_margin": 0.},
}
CUTOFFS = ["2025-06-30", "2025-12-31", "2026-06-30"]


def close(actual, expected, label):
    assert np.isfinite(actual) and np.isfinite(expected), f"Nonfinite {label}"
    assert np.isclose(actual, expected, rtol=1e-12, atol=.001), (label, actual, expected)


def read_csv(path):
    try:
        return pd.read_csv(path, dtype={"symbol": str, "date": str, "signal_date": str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def input_path(provenance, key, suffix):
    if key in provenance.get("inputs", {}):
        return ROOT / provenance["inputs"][key]
    matches = [p for p in provenance["hashes"] if p.endswith(suffix)]
    assert len(matches) == 1, f"Ambiguous {key} input: {matches}"
    return ROOT / matches[0]


def annual_segments(equity, initial):
    previous_cash_nav = previous_economic_nav = float(initial)
    result = []
    for year, rows in equity.groupby(pd.to_datetime(equity.date).dt.year, sort=True):
        ending = rows.iloc[-1]
        result.append({
            "year": int(year), "sessions": len(rows),
            "start": rows.date.iloc[0], "end": rows.date.iloc[-1],
            "cash_nav_return": float(ending["nav"] / previous_cash_nav - 1),
            "economic_nav_return": float(ending.economic_nav / previous_economic_nav - 1),
            "start_economic_nav": previous_economic_nav,
            "end_economic_nav": float(ending.economic_nav),
        })
        previous_cash_nav = float(ending["nav"])
        previous_economic_nav = float(ending.economic_nav)
    close(np.prod([1 + x["economic_nav_return"] for x in result]),
          float(equity.economic_nav.iloc[-1]) / initial, "annual wealth chaining")
    return result


def audit_source_alignment(daily, hourly, universe, config):
    """Reject a known provider failure: flat zero-volume daily bars with intraday trades."""
    market = daily[daily.symbol.isin(universe.symbol) & daily.date.between(config["start"], config["end"])].copy()
    intraday_volume = hourly.groupby(["date", "symbol"]).volume.sum().rename("intraday_volume")
    paired = market.merge(intraday_volume, on=["date", "symbol"], how="left")
    contradictory = paired[(paired.volume == 0) & (paired.intraday_volume > 0)]
    assert contradictory.empty, "SOURCE_ZERO_VOLUME_CONTRADICTION: " + str(contradictory[["date", "symbol"]].to_dict("records"))
    # Independently verified TAIFEX notices: effective economic units at resumption,
    # not Yahoo's suspension-start event timestamps with synthetic flat prices.
    verified_events = [
        ("2371.TW", "2025-06-12", "2025-06-20", "2025-06-23", .95, .5),
        ("3481.TW", "2024-08-15", "2024-08-23", "2024-08-26", .88, 1.2),
        ("2327.TW", "2025-08-14", "2025-08-22", "2025-08-25", 4., 0.),
        ("5904.TWO", "2026-07-30", "2026-08-07", "2026-08-10", 10., 0.),
    ]
    for symbol, suspended_from, suspended_to, effective, ratio, old_share_cash in verified_events:
        issuer = daily[daily.symbol == symbol]
        assert not len(issuer[issuer.date.between(suspended_from, suspended_to)]), f"Synthetic suspension rows: {symbol}"
        event = issuer[issuer.date == effective]
        assert len(event) == 1, f"Missing effective corporate-action row: {symbol}"
        close(float(event.iloc[0].split), ratio, f"{symbol} effective conversion ratio")
        close(float(event.iloc[0].dividend), old_share_cash, f"{symbol} old-share capital refund")
    counts = market.groupby("date").symbol.nunique()
    assert "2026-07-10" not in set(counts.index), "Verified market closure retained"
    return {"status": "PASS", "daily_zero_positive_intraday_rows": 0,
            "verified_resumption_action_events": len(verified_events),
            "minimum_daily_entity_coverage": int(counts.min()),
            "sessions_by_year": {str(k): int(v) for k, v in counts.groupby(counts.index.str[:4]).size().items()},
            "scope": "Cross-sectional coverage and zero-volume contradiction checks; positive volume does not certify every quote."}


def audit_strategy(path, daily, universe, canonical_config, expected_changes):
    settings = json.loads((path / "config.json").read_text())
    assert settings == {**canonical_config, **expected_changes}, f"Unexpected strategy change: {path.name}"
    equity, positions, fills, orders = [read_csv(path / f"{name}.csv") for name in ("equity", "holdings", "trades", "orders")]
    assert len(equity) and not equity.date.duplicated().any()
    assert equity.date.is_monotonic_increasing
    symbols = set(universe.symbol)
    data = daily[daily.symbol.isin(symbols)]
    market_days = sorted(data.loc[data.date.between(settings["start"], settings["end"]), "date"].unique())
    assert equity.date.tolist() == market_days, f"Incomplete equity calendar: {path.name}"
    assert not positions.duplicated(["date", "symbol"]).any()
    assert set(positions.symbol) <= symbols
    assert not len(positions[(positions.symbol == "2888.TW") & (positions.date >= "2025-07-24")]), \
        f"UNSUPPORTED_MULTI_ASSET_MERGER: {path.name} holds 2888 after conversion"
    if len(fills):
        assert (fills.signal_date < fills.date).all(), "Same-day or future-informed trade"
        assert np.allclose(fills.shares % settings["lot_size"], 0), "Non-lot execution"
        assert not fills.duplicated(["date", "symbol"]).any()
    if len(orders):
        assert np.allclose(orders.shares % settings["lot_size"], 0), "Non-lot order"
        assert not orders.duplicated(["signal_date", "symbol"]).any()
    lookup_orders = orders.set_index(["signal_date", "symbol"]) if len(orders) else pd.DataFrame()
    holdings, marks = {}, {}
    cash = float(settings["initial_cash"])
    receivable = 0.
    total_costs = total_slippage = total_turnover = 0.
    prior_nav = float(settings["initial_cash"])
    previous_day = sorted(data.loc[data.date < market_days[0], "date"].unique())[-1]
    for idx, row in equity.iterrows():
        day = row.date
        bars = data[data.date == day].set_index("symbol")
        for symbol, old_quantity in list(holdings.items()):
            if symbol in bars.index:
                receivable += old_quantity * float(bars.at[symbol, "dividend"])
                holdings[symbol] = old_quantity * float(bars.at[symbol, "split"])
        today = fills[fills.date == day] if len(fills) else fills
        fees = notional = 0.
        for trade in today.itertuples():
            symbol, quantity = trade.symbol, float(trade.shares)
            assert trade.signal_date == previous_day, f"Order did not execute in next session: {day} {symbol}"
            assert (trade.signal_date, symbol) in lookup_orders.index
            close(quantity, float(lookup_orders.at[(trade.signal_date, symbol), "shares"]), "fixed planned quantity")
            assert symbol in bars.index and bars.at[symbol, "volume"] > 0
            if settings["execution"] == "next_open":
                execution_price = float(bars.at[symbol, "open"]) * (1 + np.sign(quantity) * settings["slippage_bps"] / 10000.)
                slippage = abs(quantity) * abs(execution_price - float(bars.at[symbol, "open"]))
            else:
                execution_price = float(bars.at[symbol, "turnover"] / bars.at[symbol, "volume"])
                slippage = 0.
            close(float(trade.price), execution_price, "execution price")
            gross = abs(quantity) * execution_price
            commission = gross * settings["commission"]
            tax = gross * settings["sell_tax"] if quantity < 0 else 0.
            for actual, expected, label in [
                (trade.notional, gross, "notional"), (trade.fee, commission, "commission"),
                (trade.tax, tax, "sell tax"), (trade.slippage_cost, slippage, "slippage"),
                (trade.volume_participation, abs(quantity) / bars.at[symbol, "volume"], "participation"),
            ]:
                close(float(actual), float(expected), f"{day} {symbol} {label}")
            cash -= quantity * execution_price + commission + tax
            holdings[symbol] = holdings.get(symbol, 0.) + quantity
            assert holdings[symbol] >= -1e-6, f"Short holding: {day} {symbol}"
            if abs(holdings[symbol]) < 1e-6:
                del holdings[symbol]
            fees += commission + tax
            notional += gross
            total_slippage += slippage
        marks.update(bars.close.to_dict())
        stale = len(set(holdings) - set(bars.index))
        if idx == len(equity) - 1:
            cash += receivable
            receivable = 0.
        nav = cash + sum(quantity * marks[symbol] for symbol, quantity in holdings.items())
        for actual, expected, label in [
            (row.cash, cash, "cash"), (row["nav"], nav, "NAV"),
            (row.economic_nav, nav + receivable, "economic NAV"),
            (row.dividend_receivable, receivable, "dividend receivable"),
            (row.costs, fees, "daily costs"), (row.traded_notional, notional, "daily traded notional"),
            (row.turnover, notional / prior_nav, "turnover"),
            (row.cash_ratio, cash / nav, "cash ratio"),
        ]:
            close(float(actual), float(expected), f"{path.name} {day} {label}")
        assert int(row.holdings) == len(holdings)
        assert int(row.stale_count) == stale
        saved = positions[positions.date == day].set_index("symbol")
        assert set(saved.index) == set(holdings), f"Missing/extra position: {day}"
        for symbol, quantity in holdings.items():
            close(float(saved.at[symbol, "shares"]), quantity, f"{day} {symbol} reconstructed shares")
            close(float(saved.at[symbol, "close"]), float(marks[symbol]), f"{day} {symbol} closing mark")
            close(float(saved.at[symbol, "weight"]), quantity * marks[symbol] / nav, f"{day} {symbol} weight")
        total_costs += fees
        total_turnover += notional / prior_nav
        prior_nav, previous_day = nav, day
    metrics = json.loads((path / "metrics.json").read_text())
    values = np.r_[settings["initial_cash"], equity.nav.to_numpy()]
    economic_values = np.r_[settings["initial_cash"], equity.economic_nav.to_numpy()]
    returns = values[1:] / values[:-1] - 1
    expected = {
        "total_return": values[-1] / values[0] - 1,
        "max_drawdown": -(values / np.maximum.accumulate(values) - 1).min(),
        "economic_max_drawdown": -(economic_values / np.maximum.accumulate(economic_values) - 1).min(),
        "annualized_volatility": returns.std(ddof=1) * np.sqrt(252),
        "transaction_costs": total_costs, "slippage_cost": total_slippage,
        "turnover_two_way": total_turnover, "final_nav": values[-1],
        "sessions": len(equity), "trades": len(fills),
    }
    for key, value in expected.items():
        close(float(metrics[key]), float(value), f"{path.name} metric {key}")
    if returns.std(ddof=1) > 0:
        close(float(metrics["sharpe_zero_rf"]), returns.mean() / returns.std(ddof=1) * np.sqrt(252), "Sharpe")
    return {
        "model": path.name, "status": "PASS", "sessions": len(equity), "trades": len(fills),
        "annual_segments": annual_segments(equity, settings["initial_cash"]),
        "raw_rule_breach_days": int(equity.violations.fillna("").ne("").sum()),
        "stale_held_price_days": int(equity.stale_count.gt(0).sum()),
        "max_daily_volume_participation": float(fills.volume_participation.max()) if len(fills) else 0.,
        "above_entire_daily_volume": int(fills.volume_participation.gt(1).sum()) if len(fills) else 0,
    }


def audit_0050(path, daily, config, expected_sessions):
    """Separate one-position ledger, including the genuine 2025 trading suspension."""
    equity = read_csv(path / "equity.csv")
    assert equity.date.tolist() == expected_sessions
    etf = daily[daily.symbol == "0050.TW"]
    last_pre = etf[etf.date < expected_sessions[0]].sort_values("date").iloc[-1]
    q = int((1 - config["cash_target"]) * config["initial_cash"] / last_pre.close / config["lot_size"]) * config["lot_size"]
    cash, receivable, mark = float(config["initial_cash"]), 0., float(last_pre.close)
    total_costs = 0.
    for idx, row in equity.iterrows():
        today = etf[etf.date == row.date]
        fee = 0.
        if len(today):
            bar = today.iloc[0]
            if idx:
                receivable += q * float(bar.dividend)
                q *= float(bar.split)
            else:
                fill = float(bar.open) * (1 + config["slippage_bps"] / 10000.) if config["execution"] == "next_open" else float(bar.turnover / bar.volume)
                fee = q * fill * config["commission"]
                cash -= q * fill + fee
            mark = float(bar.close)
        elif idx == 0:
            raise AssertionError("Missing first 0050 fill")
        if idx == len(equity) - 1:
            cash += receivable
            receivable = 0.
        nav = cash + q * mark
        for actual, expected, label in [(row.cash, cash, "cash"), (row["nav"], nav, "NAV"),
                                        (row.economic_nav, nav + receivable, "economic NAV"),
                                        (row.dividend_receivable, receivable, "receivable"), (row.costs, fee, "costs")]:
            close(float(actual), float(expected), f"0050 {row.date} {label}")
        assert row.stale_count == int(not len(today))
        total_costs += fee
    split = etf[etf.date == "2025-06-18"]
    assert len(split) == 1 and float(split.iloc[0].split) == 4., "Missing effective June18 0050 four-for-one split"
    assert not len(etf[etf.date.between("2025-06-11", "2025-06-17")]), "Fabricated 0050 suspension bars"
    metrics = json.loads((path / "metrics.json").read_text())
    close(float(metrics["total_return"]), float(equity.nav.iloc[-1]) / config["initial_cash"] - 1, "0050 return")
    close(float(metrics["transaction_costs"]), total_costs, "0050 costs")
    return {"model": path.name, "status": "PASS", "sessions": len(equity),
            "annual_segments": annual_segments(equity, config["initial_cash"])}


def audit_analytics(output, config):
    """Verify the displayed annual/monthly tables, not just per-run metric files."""
    annual = pd.read_csv(output / "annual_returns.csv")
    monthly = pd.read_csv(output / "monthly_returns.csv")
    summary = pd.read_csv(output / "summary.csv").set_index("model")
    expected_models = set(VARIANTS) | {"0050_buy_hold_85pct"}
    assert set(summary.index) == set(annual.model) == set(monthly.model) == expected_models
    assert not summary.index.duplicated().any()
    assert not annual.duplicated(["model", "year"]).any()
    assert not monthly.duplicated(["model", "month"]).any()
    initial = float(config["initial_cash"])
    for model in sorted(expected_models):
        eq = read_csv(output / model / "equity.csv")
        dates = pd.to_datetime(eq.date)
        for name, basis in [("book", "nav"), ("economic", "economic_nav")]:
            values = np.r_[initial, eq[basis].to_numpy()]
            r = values[1:] / values[:-1] - 1
            pref = "" if name == "book" else "economic_"
            for metric, expected in {
                "total_return": values[-1] / initial - 1,
                "max_drawdown": -(values / np.maximum.accumulate(values) - 1).min(),
                "annualized_volatility": r.std(ddof=1) * np.sqrt(252),
                "sharpe_zero_rf": r.mean() / r.std(ddof=1) * np.sqrt(252),
            }.items():
                close(float(summary.at[model, pref + metric]), float(expected), f"summary {model} {pref}{metric}")
            for label, groups, table, group_key in [
                ("annual", dates.dt.year, annual[annual.model == model], "year"),
                ("monthly", dates.dt.to_period("M").astype(str), monthly[monthly.model == model], "month"),
            ]:
                previous = initial
                seen = []
                for period, rows in eq.groupby(groups, sort=True):
                    observed = table[table[group_key] == period]
                    assert len(observed) == 1, f"Missing {label} {model} {period}"
                    saved = observed.iloc[0]
                    close(float(saved[name + "_return"]), float(rows[basis].iloc[-1]) / previous - 1,
                          f"{label} {model} {period} {name} return")
                    if label == "annual":
                        subpath = np.r_[previous, rows[basis].to_numpy()]
                        close(float(saved[name + "_max_drawdown"]), float(-(subpath / np.maximum.accumulate(subpath) - 1).min()),
                              f"annual {model} {period} {name} drawdown")
                        assert int(saved.sessions) == len(rows)
                        assert saved.start == rows.date.iloc[0] and saved.end == rows.date.iloc[-1]
                    else:
                        assert saved.through == rows.date.iloc[-1]
                    previous = float(rows[basis].iloc[-1])
                    seen.append(period)
                assert len(seen) == len(table), f"Extraneous {label} period"
        years = (pd.Timestamp(eq.date.iloc[-1]) - (pd.Timestamp(config["start"]) - pd.Timedelta(days=1))).days / 365.25
        close(float(summary.at[model, "calendar_cagr"]), float((eq.nav.iloc[-1] / initial) ** (1 / years) - 1), f"{model} CAGR")
    return {"status": "PASS", "annual_rows": len(annual), "monthly_rows": len(monthly), "models": len(summary)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/backtest_2025_to_now_v1")
    args = parser.parse_args()
    output = ROOT / args.output
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance.get("outputs_complete") and provenance.get("run_completed_at"), "Incomplete candidate run"
    for relative, expected in provenance["hashes"].items():
        assert file_hash(ROOT / relative) == expected, f"Input/code changed after run: {relative}"
    paths = {key: input_path(provenance, key, suffix) for key, suffix in {
        "daily": "daily_canonical.csv", "hourly": "hourly_canonical.csv",
        "universe": "universe_20241231.csv", "config": "strategy_v1_2025_to_now.json",
    }.items()}
    daily = read_csv(paths["daily"])
    hourly = read_csv(paths["hourly"])
    universe = read_csv(paths["universe"])
    if "known_at" not in universe:
        universe["known_at"] = universe.known_at_assumption
    assert len(universe) == 150 and universe.symbol.nunique() == 150
    assert "2888.TW" in set(universe.symbol), "Survivorship deletion of historical 2888"
    config = json.loads(paths["config"].read_text())
    original = json.loads((ROOT / "config/strategy_v1.json").read_text())
    assert {k: v for k, v in config.items() if k not in ("strategy_id", "start")} == {k: v for k, v in original.items() if k not in ("strategy_id", "start")}, "Frozen economic parameters changed"
    assert config["start"] == "2025-01-01" and config["end"] == "2026-09-21"
    source_alignment = audit_source_alignment(daily, hourly, universe, config)
    models = []
    for name, changes in VARIANTS.items():
        print(f"Reconstructing {name}", flush=True)
        models.append(audit_strategy(output / name, daily, universe, config, changes))
    main_equity = read_csv(output / "v1/equity.csv")
    models.append(audit_0050(output / "0050_buy_hold_85pct", daily, config, main_equity.date.tolist()))
    analytics = audit_analytics(output, config)
    # A 150-name buy-and-hold path necessarily owns the unsupported merger entitlement.
    assert not (output / "universe_buy_hold_85pct/metrics.json").exists(), "Unverified multi-asset-merger benchmark must not enter the result table"
    prefix_reports = []
    for cutoff in CUTOFFS:
        print(f"Verifying actual prefix {cutoff}", flush=True)
        limited_daily = daily[daily.date <= cutoff]
        limited_hourly = hourly[hourly.date <= cutoff]
        result = run_backtest(limited_daily, universe, {**config, "end": cutoff}, aggregate_four_hour(limited_hourly))
        for name, datecol in [("signals", "date"), ("orders", "signal_date"), ("trades", "date")]:
            full = read_csv(output / f"v1/{name}.csv")
            full = full[full[datecol] <= cutoff].reset_index(drop=True)
            assert_frame_equal(full, result[name].reset_index(drop=True), check_dtype=False,
                               check_exact=False, rtol=1e-12, atol=1e-10)
        # Short-run terminal dividends shift cash, but cannot change economic wealth or shares.
        full_eq = main_equity[main_equity.date <= cutoff].reset_index(drop=True)
        close(float(result["equity"].economic_nav.iloc[-1]), float(full_eq.economic_nav.iloc[-1]), "prefix economic NAV")
        full_h = read_csv(output / "v1/holdings.csv")
        full_h = full_h[full_h.date <= cutoff][["date", "symbol", "shares", "close"]].reset_index(drop=True)
        assert_frame_equal(full_h, result["holdings"][["date", "symbol", "shares", "close"]].reset_index(drop=True), check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-10)
        prefix_reports.append({"cutoff": cutoff, "status": "PASS", "sessions": len(result["equity"]),
                               "signals": len(result["signals"]), "orders": len(result["orders"])})
    report = {
        "status": "PASS", "verified_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Independent computational ledger and causal-prefix verification; source completeness, liquidity and contest certification remain separate.",
        "models": models, "prefixes": prefix_reports, "annual_monthly_summary_tables": analytics,
        "source_alignment": source_alignment,
        "checks": ["Frozen economic parameters unchanged", "All run inputs/code hashes match", "Historical 150-name universe retains extinct issuer",
                   "Cash, old-share dividends, split quantities, executed quantities, fees, tax, slippage and marks independently reconstructed",
                   "No year-boundary reset or premature dividend cash payment", "Annual economic returns compound to full-period wealth",
                   "0050 split and suspended market sessions retained", "No unmodeled 2888 post-merger holding or invalid equal-weight benchmark",
                   "Signals, fixed-share orders, fills and positions preserved at three historical cutoffs"],
        "auditor_hash": file_hash(Path(__file__)),
    }
    (output / "audit_extension.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
