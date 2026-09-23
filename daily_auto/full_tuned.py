"""Build a live-shaped D-Plan from a reconciled state and fixed A signals.

This module never fetches data, schedules a run, or submits a plan.  The pure
``build_packet`` seam accepts the fixed planner/configuration used by tests and
the public strategy entrypoint.  The CLI deliberately imports only the pinned
``v2_offcial_best_deep_tuning`` entrypoint; it cannot select an arbitrary
planner or configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence
import argparse
import copy
import hashlib
import importlib
import inspect
import json
import os
import re
import shutil
import tempfile

import numpy as np
import pandas as pd

from daily_auto.validate import (OFFICIAL_REFERENCE, SCHEMA, TZ, PreflightError,
                                 decimal, derive_orders, json_safe, load_json,
                                 project_orders, validate_plan)
from src.official_v2_review import representable_weight, whole_lots

ROOT = Path(__file__).resolve().parents[1]
TICKER = re.compile(r"^\d{4}$")
SHA = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
SOURCE_AUTHORITIES = {"twse", "tpex", "taifex", "mops", "fininst", "media", "vendor", "other"}
EXPLAIN_COLUMNS = (
    "return20", "return50", "ema20", "ema50", "macd_hist", "volume_ratio",
    "return_short", "return_long", "trend", "long_trend",
)


class PacketBuildError(ValueError):
    """Fail-closed packet construction error with a stable machine code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SignalBundle:
    """Causal ranked rows and their timestamped external evidence.

    ``frame`` must already use official four-digit identities in ``symbol`` or
    ``ticker``.  Historical aliases such as 5371.TWO are resolved upstream,
    where the dated identity evidence is available.
    """

    frame: pd.DataFrame
    as_of: str
    known_at: str
    sources: tuple[Mapping, ...]
    input_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class GeneratedPacket:
    filename: str
    plan: Mapping
    state: Mapping
    signals: pd.DataFrame
    signal_metadata: Mapping
    config: Mapping
    preflight: Mapping
    receipt: Mapping
    code_paths: tuple[Path, ...]
    input_paths: tuple[Path, ...]


Planner = Callable[[pd.DataFrame, Mapping[str, float], float, float, Mapping, bool,
                    Sequence[str] | None], tuple[Mapping[str, int], str, Sequence[str]]]


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _plain(value):
    """Convert pandas/numpy/Decimal values to strict, deterministic JSON data."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if value is pd.NA:
        return None
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _canonical_sha(value) -> str:
    payload = json.dumps(_plain(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def _time(value, label: str) -> datetime:
    try:
        result = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise PacketBuildError("INVALID_TIME", label + " must be an ISO timestamp") from None
    if result.utcoffset() != timedelta(hours=8):
        raise PacketBuildError("INVALID_TIME", label + " must use +08:00")
    return result


def _clock_now() -> datetime:
    return datetime.now(TZ).replace(microsecond=0)


def _assert_known_by(value, cutoff: datetime, label: str = "state") -> None:
    """Reject input evidence whose own availability time is after invocation."""
    if isinstance(value, Mapping):
        if "known_at" in value:
            if _time(value["known_at"], label + ".known_at") > cutoff:
                raise PacketBuildError("LOOKAHEAD_STATE", label + " was not known when generation started")
        for key, item in value.items():
            _assert_known_by(item, cutoff, label + "." + str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_known_by(item, cutoff, f"{label}[{index}]")


def _under_root(path: Path) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError:
        raise PacketBuildError("CODE_OUTSIDE_PROJECT", str(resolved)) from None
    if not resolved.is_file():
        raise PacketBuildError("MISSING_INPUT_FILE", str(resolved))
    return resolved


def _callable_path(planner) -> Path:
    target = planner if inspect.isfunction(planner) else planner.__class__
    path = inspect.getsourcefile(target)
    if not path:
        raise PacketBuildError("PLANNER_SOURCE_UNKNOWN", repr(target))
    return _under_root(Path(path))


def _holdings(state: Mapping) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in state.get("holdings", []):
        ticker = row.get("ticker")
        if not isinstance(ticker, str) or not TICKER.fullmatch(ticker) or ticker in result:
            raise PacketBuildError("HOLDINGS_IDENTITY", "Holdings require unique official four-digit tickers")
        shares = decimal(row.get("shares"), "holdings.shares")
        if shares <= 0 or shares != shares.to_integral_value():
            raise PacketBuildError("FRACTIONAL_HOLDING", ticker + " must retain its exact integral share count")
        result[ticker] = int(shares)
    return result


def _close_map(state: Mapping) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for row in state.get("closes", []):
        ticker = row.get("ticker")
        if not isinstance(ticker, str) or not TICKER.fullmatch(ticker) or ticker in result:
            raise PacketBuildError("CLOSE_IDENTITY", "Closes require unique official four-digit tickers")
        price = decimal(row.get("close"), "close")
        if price <= 0:
            raise PacketBuildError("CLOSE_VALUE", ticker + " close must be positive")
        result[ticker] = price
    return result


def _validate_config(config: Mapping) -> dict:
    cfg = copy.deepcopy(dict(config))
    exact = {
        "min_count": 20, "max_count": 30, "max_weight": .1,
        "tsmc_max_weight": .25, "commission": .001425, "sell_tax": .003,
        "lot_size": 1000, "cash_target": 0, "cash_guard_headroom": .02,
        "allocation_mode": "local", "execution": "vwap",
        "dividend_cash_policy": "end_of_period", "universe_mode": "official_ex_post",
    }
    missing = [key for key in exact if key not in cfg]
    if missing:
        raise PacketBuildError("CONFIG_MISSING", ",".join(missing))
    for key, expected in exact.items():
        if cfg[key] != expected:
            raise PacketBuildError("FIXED_RULE_CHANGED", f"{key}={cfg[key]!r}, expected {expected!r}")
    if not 20 <= int(cfg.get("target_count", 0)) <= 30:
        raise PacketBuildError("CONFIG_TARGET_COUNT", "target_count must be 20..30")
    if not .08 <= float(cfg.get("cash_guard_ratio", -1)) <= .18:
        raise PacketBuildError("CONFIG_CASH_GUARD", "cash_guard_ratio must remain in the frozen range")
    if not 0 < float(cfg.get("price_lower_buffer", 0)) <= 1 <= float(cfg.get("price_buffer", 0)):
        raise PacketBuildError("CONFIG_PRICE_BOUNDS", "price bounds must contain 1.0")
    params = cfg.get("full_tuning_params")
    if not isinstance(params, dict) or len(params) != 18 or params.get("cash_guard_ratio") != cfg["cash_guard_ratio"]:
        raise PacketBuildError("CONFIG_NOT_PINNED", "Expected all 18 selected parameters")
    policy = cfg.get("study_policy", {})
    review = cfg.get("official_review_policy", {})
    if policy.get("official_live_submission") != "BLOCK_IF_UNKNOWN":
        raise PacketBuildError("CONFIG_POLICY", "Missing BLOCK_IF_UNKNOWN policy")
    if review.get("active_share") != "UNKNOWN_BLOCK_SUBMISSION":
        raise PacketBuildError("CONFIG_POLICY", "Active Share must fail closed")
    if not str(cfg.get("strategy_id", "")).startswith("full_tuned_v2_"):
        raise PacketBuildError("CONFIG_STRATEGY_ID", "Expected pinned full_tuned_v2 strategy")
    return cfg


def _normalize_signals(bundle: SignalBundle, state: Mapping, completed: datetime) -> pd.DataFrame:
    rows = bundle.frame.copy(deep=True)
    if "symbol" not in rows and "ticker" in rows:
        rows = rows.rename(columns={"ticker": "symbol"})
    if "symbol" not in rows:
        raise PacketBuildError("SIGNAL_COLUMNS", "signals require symbol or ticker")
    required = {"date", "symbol", "close", "score", "entry_ok", "exit"}
    if required - set(rows):
        raise PacketBuildError("SIGNAL_COLUMNS", ",".join(sorted(required - set(rows))))
    rows["symbol"] = rows.symbol.astype(str)
    if not rows.symbol.map(lambda value: bool(TICKER.fullmatch(value))).all():
        raise PacketBuildError("SIGNAL_ALIAS_UNRESOLVED", "signals must use official four-digit tickers")
    if rows.symbol.duplicated().any():
        raise PacketBuildError("SIGNAL_DUPLICATE", "one row per official ticker is required")
    if set(rows.date.astype(str)) != {bundle.as_of} or bundle.as_of != state.get("as_of"):
        raise PacketBuildError("SIGNAL_DATE", "all signal rows must match the reconciled prior session")
    if _time(bundle.known_at, "signals.known_at") > completed:
        raise PacketBuildError("LOOKAHEAD_SIGNAL", "signals were not available when the plan completed")
    whitelist = state.get("whitelist", {}).get("tickers", [])
    if len(whitelist) != 150 or len(set(whitelist)) != 150 or set(rows.symbol) != set(whitelist):
        raise PacketBuildError("SIGNAL_UNIVERSE", "signals must cover the exact official 150 identities")
    closes = _close_map(state)
    if set(rows.symbol) != set(closes):
        raise PacketBuildError("SIGNAL_CLOSE_COVERAGE", "state closes and signal identities must match")
    rows["close"] = pd.to_numeric(rows.close, errors="coerce")
    rows["score"] = pd.to_numeric(rows.score, errors="coerce")
    if not np.isfinite(rows.close).all() or not rows.close.gt(0).all() or not np.isfinite(rows.score).all():
        raise PacketBuildError("SIGNAL_NUMERIC", "close and score must be finite")
    for row in rows.itertuples():
        if Decimal(str(row.close)) != closes[row.symbol]:
            raise PacketBuildError("SIGNAL_CLOSE_MISMATCH", row.symbol)
    for column in ("entry_ok", "exit"):
        if not rows[column].map(lambda value: isinstance(value, (bool, np.bool_))).all():
            raise PacketBuildError("SIGNAL_BOOLEAN", column)
        rows[column] = rows[column].astype(bool)
    return rows.sort_values(["score", "symbol"], ascending=[False, True]).reset_index(drop=True)


def _plan_sources(bundle: SignalBundle, state: Mapping, completed: datetime) -> tuple[list[dict], dict]:
    if not 1 <= len(bundle.sources) <= 4:
        raise PacketBuildError("SIGNAL_SOURCE_COUNT", "one to four signal sources are required")
    ledger = state.get("ledger_provenance")
    if not isinstance(ledger, dict):
        raise PacketBuildError("LEDGER_PROVENANCE", "state ledger provenance is required")
    ledger_required = ("source_url", "known_at", "sha256")
    if any(ledger.get(key) is None for key in ledger_required):
        raise PacketBuildError("LEDGER_PROVENANCE", "ledger source_url, known_at and sha256 are required")
    permitted_ledger_authorities = {"organizer"} if state.get("data_mode") == "LIVE" else SOURCE_AUTHORITIES
    if ledger.get("authority") not in permitted_ledger_authorities or not SHA.fullmatch(str(ledger.get("sha256"))):
        raise PacketBuildError("LEDGER_PROVENANCE", "ledger authority or sha256 is invalid for this data mode")
    ledger_known = _time(ledger["known_at"], "ledger.known_at")
    if ledger_known > completed:
        raise PacketBuildError("LOOKAHEAD_STATE", "ledger was not known when generation started")
    reconciled = (ledger.get("reconciled") is True
                  and ledger.get("origin") in ("official_settlement", "reconciled_local"))
    ledger_name = ("Organizer or reconciled prior-session ledger" if reconciled else
                   "Supplied prior-session ledger; official reconciliation unconfirmed")
    sources = [dict(source_id="S1", authority="other", url=ledger.get("source_url"),
                    name=ledger_name,
                    content_as_of=ledger.get("known_at"), fetched_at=ledger.get("known_at"))]
    receipt = {"ledger_provenance_sha256": ledger.get("sha256"), "signal_sources": []}
    bundle_known = _time(bundle.known_at, "signals.known_at")
    for index, raw in enumerate(bundle.sources, 2):
        item = dict(raw)
        required = ("authority", "source_url", "content_as_of", "known_at", "sha256")
        if any(item.get(key) is None for key in required):
            raise PacketBuildError("SIGNAL_SOURCE_FIELDS", "missing source evidence field")
        if item["authority"] not in SOURCE_AUTHORITIES or not SHA.fullmatch(str(item["sha256"])):
            raise PacketBuildError("SIGNAL_SOURCE_EVIDENCE", str(item.get("source_url")))
        content = _time(item["content_as_of"], "source.content_as_of")
        known = _time(item["known_at"], "source.known_at")
        if not content <= known <= bundle_known <= completed:
            raise PacketBuildError("SIGNAL_SOURCE_TIME", str(item["source_url"]))
        row = dict(source_id=f"S{index}", authority=item["authority"],
                   url=item["source_url"], content_as_of=item["content_as_of"],
                   fetched_at=item["known_at"])
        for key in ("name", "archive_url", "published_at"):
            if item.get(key) is not None:
                row[key] = item[key]
        sources.append(row)
        receipt["signal_sources"].append({key: item[key] for key in required})
    return sources, receipt


def _signed_orders(raw, holdings: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        raise PacketBuildError("PLANNER_RESULT", "planner orders must be a mapping")
    result: dict[str, int] = {}
    for ticker, quantity in raw.items():
        ticker = str(ticker)
        if not TICKER.fullmatch(ticker) or ticker in result:
            raise PacketBuildError("PLANNER_TICKER", ticker)
        q = decimal(quantity, "planner order")
        if q == 0 or q != q.to_integral_value() or q % 1000:
            raise PacketBuildError("PLANNER_ORDER_LOT", ticker)
        if q < 0 and -int(q) > holdings.get(ticker, 0):
            raise PacketBuildError("PLANNER_OVERSELL", ticker)
        if holdings.get(ticker, 0) % 1000 and q:
            raise PacketBuildError("ODD_HOLDING_ORDER", ticker + " must remain unchanged")
        result[ticker] = int(q)
    if len(result) > 30:
        raise PacketBuildError("PLANNER_ORDER_COUNT", "D-Plan allows at most 30 orders")
    return result


def _feature_values(row: pd.Series, held: int) -> dict[str, float | int]:
    values: dict[str, float | int] = {
        "close": float(row.close), "score": float(row.score),
        "held_shares": int(held), "entry_ok": int(bool(row.entry_ok)),
        "exit": int(bool(row.exit)),
    }
    for column in EXPLAIN_COLUMNS:
        if column in row and not isinstance(row[column], (bool, np.bool_)):
            try:
                value = float(row[column])
            except (TypeError, ValueError):
                continue
            if np.isfinite(value):
                values[column] = value
    return values


def _posture(projection: Mapping, nav: float) -> dict:
    net = float(projection["net_notional"])
    intent = "hold" if abs(net) <= .02 * nav else "increase" if net > 0 else "reduce"
    ratio = float(projection["cash_ratio"])
    low = max(0., min(.25, ratio - .01))
    high = max(low, min(.25, ratio + .01))
    return dict(net_exposure_intent=intent, target_cash_pct_range=[low, high])


def _build_plan(state: Mapping, rows: pd.DataFrame, config: Mapping, planner: Planner,
                sources: list[dict], started: datetime, completed: datetime,
                code_version: str, planner_context: Mapping | None) -> tuple[dict, dict]:
    holdings = _holdings(state)
    cash, nav = float(decimal(state.get("cash"), "cash")), float(decimal(state.get("nav"), "nav"))
    context = dict(planner_context or {})
    buy_phase = context.get("buy_phase", False)
    desired = context.get("desired_symbols")
    if not isinstance(buy_phase, bool):
        raise PacketBuildError("PLANNER_CONTEXT", "buy_phase must be boolean")
    if desired is not None and (not isinstance(desired, list) or not set(desired) <= set(rows.symbol)):
        raise PacketBuildError("PLANNER_CONTEXT", "desired_symbols must use available official tickers")
    before = rows.copy(deep=True)
    phase_status = context.get("phase_status", "UNKNOWN_BLOCKED")
    allowed_phase = phase_status in ("BOOTSTRAP_EMPTY", "TRUSTED_PRIOR")
    if not allowed_phase:
        raw_orders, reason, selected = {}, "BLOCKED_PRIOR_PHASE_UNKNOWN", list(holdings)
    else:
        raw_orders, reason, selected = planner(rows.copy(deep=True), dict(holdings), cash, nav,
                                               copy.deepcopy(config), buy_phase, desired)
    pd.testing.assert_frame_equal(rows, before)
    orders = _signed_orders(raw_orders, holdings)
    if not isinstance(reason, str) or not reason:
        raise PacketBuildError("PLANNER_REASON", "planner must return a reason")
    if not re.fullmatch(r"[A-Z0-9_:;-]+", reason):
        raise PacketBuildError("PLANNER_REASON", "planner reason must use the stable machine token alphabet")
    if not isinstance(selected, Sequence) or isinstance(selected, (str, bytes)):
        raise PacketBuildError("PLANNER_SELECTED", "planner must return selected identities")
    if not set(map(str, selected)) <= set(rows.symbol):
        raise PacketBuildError("PLANNER_SELECTED", "planner selected unavailable identities")

    source_refs = [row["source_id"] for row in sources[1:]]
    breadth = dict(entry_eligible=int(rows.entry_ok.sum()), exit_flagged=int(rows.exit.sum()),
                   universe_count=len(rows), median_score=float(rows.score.median()))
    reconciled = (state.get("ledger_provenance", {}).get("reconciled") is True
                  and state.get("ledger_provenance", {}).get("origin")
                  in ("official_settlement", "reconciled_local"))
    ledger_statement = ("已對帳之前一交易日現金、帳面淨值、持股檔數與應收股息。" if reconciled else
                        "提供的前一交易日帳本尚未確認官方對帳；以下只記錄其現金、帳面淨值、持股檔數與應收股息。")
    phase_after = reason == "SELL_THEN_WAIT_SETTLEMENT"
    phase_statement = (f"固定規劃器狀態：plan_reason={reason};phase_before={int(buy_phase)};"
                       f"phase_after={int(phase_after)}。委託與保留持股摘要均由同次固定規劃產生。")
    plan = dict(schema_version="4.0", doc_type="D-Plan", team_id=state["team_id"],
                trade_date=state["calendar"]["trade_date"], sources=sources,
                observations=[
                    dict(obs_id="O1", source_ref=source_refs,
                         statement="前一交易日完整官方股票池的固定 A 分數、可買與退出旗標摘要。",
                         values=breadth),
                    dict(obs_id="O2", source_ref=["S1"],
                         statement=ledger_statement,
                         values=dict(cash=cash, book_nav=nav, holdings=len(holdings),
                                     dividend_receivable=float(decimal(state.get("dividend_receivable", 0))))),
                    dict(obs_id="O3", source_ref=["S1", *source_refs[:4]],
                         statement=phase_statement,
                         values=dict(planned_orders=len(orders), selected_names=len(set(map(str, selected))),
                                     phase_before=int(buy_phase), phase_after=int(phase_after))),
                ], market_view={}, inferences=[], decisions=[], no_trade_decisions=[], orders=[],
                agent_metadata=dict(model_provider="other", model_version="full-tuned-v2-deterministic",
                                    run_started_at=started.isoformat(), run_completed_at=completed.isoformat(),
                                    code_version=code_version))
    table = rows.set_index("symbol", drop=False)
    names = sorted(set(holdings) | set(orders))
    if len(names) > 60:
        raise PacketBuildError("INFERENCE_COUNT", "D-Plan allows at most 60 inference rows")
    inference_for: dict[str, str] = {}
    for ticker in names:
        row = table.loc[ticker]
        obs_id = f"O{len(plan['observations']) + 1}"
        refs = source_refs.copy()
        if ticker in holdings:
            refs = ["S1", *refs[:4]]
        plan["observations"].append(dict(obs_id=obs_id, source_ref=refs,
            statement=f"{ticker} 的前日收盤、固定 A 分數、持股與進出條件。",
            values=_feature_values(row, holdings.get(ticker, 0))))
        inf_id = f"I{len(plan['inferences']) + 1}"
        quantity = orders.get(ticker, 0)
        if quantity:
            direction = "增加" if quantity > 0 else "降低"
            logic = (f"固定 A 依已公布的價量趨勢分數與既有持股決定{direction} {ticker}；"
                     f"規劃器原因為 {reason}，股數由固定合規規劃器產生，未人工挑股或改單。")
            conclusion = f"{direction} {ticker} {abs(quantity)} 股"
        else:
            odd = holdings.get(ticker, 0) % 1000
            logic = (f"固定 A 本日未對 {ticker} 產生委託；"
                     + ("現有公司行動零股餘額無法依千股公式異動，故完整保留。" if odd else
                        "既有部位依本日固定分數及風險規則續抱。"))
            conclusion = f"續抱 {ticker}"
        plan["inferences"].append(dict(inf_id=inf_id, premise_refs=[obs_id, "O2", "O3"],
            logic=logic, conclusion=conclusion,
            counter_evidence="當日成交均價尚未知；Active Share 與公司事件證據仍由獨立檢查決定是否阻擋。"))
        inference_for[ticker] = inf_id
    if not plan["inferences"]:
        plan["inferences"].append(dict(inf_id="I1", premise_refs=["O1", "O2", "O3"],
            logic="固定 A 未產生可執行委託，且目前沒有既有持股；保留空單結果供本機規則檢查阻擋或核對。",
            conclusion="本日空單", counter_evidence="持股檔數與正式證據仍須通過獨立檢查。"))

    for ticker in sorted(orders):
        quantity = orders[ticker]
        held = holdings.get(ticker, 0)
        target = held + quantity
        if target < 0 or not whole_lots(target):
            raise PacketBuildError("UNREPRESENTABLE_TARGET", ticker)
        cap = float(config["tsmc_max_weight"] if ticker == "2330" else config["max_weight"])
        weight = representable_weight(target, float(table.at[ticker, "close"]), nav, cap)
        action = ("BUY" if held == 0 else "ADD") if quantity > 0 else ("SELL_ALL" if target == 0 else "TRIM")
        plan["decisions"].append(dict(decision_id=f"D{len(plan['decisions']) + 1}", ticker=ticker,
            action=action, target_weight=weight, inference_refs=[inference_for[ticker]],
            risk_check="固定整張股數、前日收盤與帳面 NAV 已再次回算；仍須通過本機完整 preflight。"))
    for ticker in sorted(set(holdings) - set(orders)):
        reason_text = ("公司行動後零股餘額完整保留，未假設可用千股委託出清。"
                       if holdings[ticker] % 1000 else "固定 A 與合規規劃器本日均未要求異動。")
        plan["no_trade_decisions"].append(dict(ticker=ticker,
            reason_refs=[inference_for[ticker]], reason=reason_text))
    plan["orders"] = derive_orders(plan, state)["orders"]
    actual = {row["ticker"]: row["shares"] * (1 if row["side"] == "BUY" else -1)
              for row in plan["orders"]}
    if actual != orders:
        raise PacketBuildError("FORMULA_MISMATCH", "planner fixed shares differ from official derivation")
    projection = project_orders(state, plan["orders"])
    posture = _posture(projection, nav)
    stance = "aggressive" if posture["net_exposure_intent"] == "increase" else (
        "defensive" if posture["net_exposure_intent"] == "reduce" else "neutral")
    plan["market_view"] = dict(basis_refs=["O1", "O2", "O3"],
        logic=("固定 A 只依前一交易日已公布的價格、成交量及趨勢分數排序，不加入人工市場判斷；"
               "本日姿態完全由固定委託的前日收盤估算淨流向決定。"),
        regime="neutral", stance=stance, posture=posture,
        counter_evidence="當日均價與收盤價尚未形成，實際成交及收盤後限制仍須依主辦方結算。")
    return plan, dict(reason=reason, selected=list(map(str, selected)), orders=orders,
                      buy_phase=buy_phase, phase_before=buy_phase, phase_after=phase_after,
                      desired_symbols=desired,
                      phase_status=phase_status, phase_report=_plain(context.get("phase_report", {})),
                      audit=copy.deepcopy(getattr(planner, "audit", [])[-1:] if hasattr(planner, "audit") else []))


def build_packet(*, state: Mapping, signals: SignalBundle, config: Mapping, planner: Planner,
                 code_paths: Sequence[Path], planner_context: Mapping | None = None,
                 clock: Callable[[], datetime] = _clock_now) -> GeneratedPacket:
    """Return a complete offline packet without writing files or using network.

    The caller owns data acquisition and the fixed strategy choice.  This seam
    owns identity/time checks, the D-Plan chain, formula equivalence, hashes and
    the conservative local preflight.
    """
    started = clock()
    if started.utcoffset() != timedelta(hours=8):
        raise PacketBuildError("CLOCK", "clock must use Asia/Taipei offset")
    if state.get("state_version") != "1.0" or state.get("data_mode") not in ("LIVE", "HISTORICAL_REPLAY"):
        raise PacketBuildError("STATE_MODE", "Use LIVE or explicitly labeled HISTORICAL_REPLAY state")
    _assert_known_by(state, started)
    cfg = _validate_config(config)
    holdings = _holdings(state)
    rows = _normalize_signals(signals, state, started)
    if set(holdings) - set(rows.symbol):
        raise PacketBuildError("MISSING_HELD_SIGNAL", ",".join(sorted(set(holdings) - set(rows.symbol))))
    paths = {_under_root(Path(__file__)), _under_root(ROOT / "daily_auto/validate.py"),
             _under_root(ROOT / "daily_auto/operations.py"),
             _under_root(ROOT / "daily_auto/compliance_state.py"),
             _under_root(SCHEMA), _under_root(OFFICIAL_REFERENCE), _callable_path(planner)}
    paths.update(_under_root(Path(path)) for path in code_paths)
    code_hashes = {str(path.relative_to(ROOT)): _sha(path) for path in sorted(paths)}
    input_paths = tuple(Path(path).resolve() for path in signals.input_paths)
    if len(set(input_paths)) != len(input_paths):
        raise PacketBuildError("DUPLICATE_INPUT_FILE", "SignalBundle input paths must be unique")
    if any(not path.is_file() for path in input_paths):
        raise PacketBuildError("MISSING_INPUT_FILE", "SignalBundle input path is absent")
    if sum(path.stat().st_size for path in input_paths) > 1024 ** 3:
        raise PacketBuildError("INPUT_SNAPSHOT_TOO_LARGE", "Raw input snapshot exceeds 1 GiB")
    input_hashes = {str(path): _sha(path) for path in input_paths}
    if input_paths:
        evidence_hashes = {str(item["sha256"]).removeprefix("sha256:") for item in signals.sources}
        absent = evidence_hashes - set(input_hashes.values())
        if absent:
            raise PacketBuildError("SOURCE_FILE_HASH_MISSING",
                                   "Every signal source sha256 must match a preserved raw input")
    config_sha = _canonical_sha(cfg)
    bundle_sha = hashlib.sha256(json.dumps(dict(code=code_hashes, config=config_sha),
                                               sort_keys=True).encode()).hexdigest()
    code_version = "sha256:" + bundle_sha[:48]
    # Every planner input must be known when invocation starts.  The final
    # completion stamp is sampled only after the planner and plan construction.
    sources, source_receipt = _plan_sources(signals, state, started)
    plan, planner_result = _build_plan(state, rows, cfg, planner, sources, started, started,
                                       code_version, planner_context)
    completed = clock()
    if completed < started or completed.utcoffset() != timedelta(hours=8):
        raise PacketBuildError("CLOCK", "clock moved backwards or changed timezone")
    plan["agent_metadata"]["run_completed_at"] = completed.isoformat()
    filename = f"D-Plan_{state['team_id']}_{state['calendar']['trade_date']}.json"
    preflight = validate_plan(plan, copy.deepcopy(dict(state)), checked_at=completed,
                              filename=filename, evidence_root=ROOT)
    if planner_result["phase_status"] == "UNKNOWN_BLOCKED":
        preflight["checks"].append(dict(code="PRIOR_PLANNER_PHASE_UNKNOWN", status="UNKNOWN",
            message="Existing holdings require an accepted previous packet and settled official ledger reconciliation"))
        preflight["blocking_codes"] = sorted(set(preflight["blocking_codes"]) | {"PRIOR_PLANNER_PHASE_UNKNOWN"})
        preflight["status"] = "BLOCK"
    cap_history = state.get("passive_cap_history", {})
    if cap_history.get("warning_disqualification") is True:
        preflight["checks"].append(dict(code="OFFICIAL_WARNING_DISQUALIFICATION", status="FAIL",
            message="Official ledger reports at least three warnings; local generation cannot clear eligibility"))
        preflight["blocking_codes"] = sorted(
            set(preflight["blocking_codes"]) | {"OFFICIAL_WARNING_DISQUALIFICATION"})
        preflight["status"] = "BLOCK"
    signal_meta = dict(as_of=signals.as_of, known_at=signals.known_at,
                       sources=[dict(item) for item in signals.sources],
                       input_paths=[str(path) for path in input_paths])
    frame_payload = _plain(rows.sort_values("symbol").to_dict("records"))
    packet_mode = ("LIVE_PACKET_LOCAL_ONLY" if state.get("data_mode") == "LIVE"
                   else "RESEARCH_PACKET_LOCAL_ONLY")
    receipt = dict(mode=packet_mode, generated_at=completed.isoformat(),
        strategy_id=cfg["strategy_id"], planner=f"{planner.__class__.__module__}.{planner.__class__.__qualname__}",
        planner_result=_plain(planner_result), code_bundle_sha256=bundle_sha,
        code_files=code_hashes, config_sha256=config_sha,
        state_payload_sha256=_canonical_sha(state), signals_payload_sha256=_canonical_sha(frame_payload),
        signal_metadata_sha256=_canonical_sha(signal_meta), input_files=input_hashes,
        source_evidence=source_receipt, preflight_status=preflight["status"],
        submission_status=preflight["submission_status"], network_used=False,
        note="Local generation and preflight only; no scheduler, platform call, order placement or formal certification.")
    return GeneratedPacket(filename=filename, plan=plan, state=copy.deepcopy(dict(state)),
        signals=rows.copy(deep=True), signal_metadata=signal_meta, config=cfg,
        preflight=preflight, receipt=receipt, code_paths=tuple(sorted(paths)),
        input_paths=input_paths)


def _json_bytes(value) -> bytes:
    return (json.dumps(_plain(value), ensure_ascii=False, indent=2,
                       allow_nan=False) + "\n").encode()


def write_packet(packet: GeneratedPacket, output_dir: Path) -> Mapping:
    """Atomically create one immutable packet directory and return its receipt."""
    output = Path(output_dir).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError("Preserve existing packet: " + str(output))
    temporary = Path(tempfile.mkdtemp(prefix="." + output.name + ".", dir=output.parent))
    try:
        files = {
            packet.filename: _json_bytes(packet.plan),
            "preflight.json": _json_bytes(packet.preflight),
            "input_snapshot/state.json": _json_bytes(packet.state),
            "input_snapshot/config.json": _json_bytes(packet.config),
            "input_snapshot/signal_metadata.json": _json_bytes(packet.signal_metadata),
            "input_snapshot/signals.csv": packet.signals.to_csv(index=False).encode(),
        }
        for name, payload in files.items():
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        receipt = copy.deepcopy(dict(packet.receipt))
        artifact_hashes = {name: hashlib.sha256(payload).hexdigest()
                           for name, payload in sorted(files.items())}

        def copy_verified(source: Path, relative: str, expected: str) -> int:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with source.open("rb") as reader, target.open("xb") as writer:
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            actual = digest.hexdigest()
            if actual != expected:
                raise PacketBuildError("SNAPSHOT_HASH_CHANGED", str(source))
            artifact_hashes[relative] = actual
            return size

        code_snapshot = {}
        for source in packet.code_paths:
            relative_source = str(source.relative_to(ROOT))
            relative = "code_snapshot/" + relative_source
            copy_verified(source, relative, receipt["code_files"][relative_source])
            code_snapshot[relative_source] = relative
        raw_snapshot = {}
        for index, source in enumerate(packet.input_paths, 1):
            expected = receipt["input_files"][str(source)]
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", source.name) or "input"
            relative = f"input_snapshot/raw/{index:02d}_{expected[:16]}_{safe_name}"
            size = copy_verified(source, relative, expected)
            raw_snapshot[str(source)] = dict(stored_as=relative, sha256=expected, bytes=size)
        receipt["code_snapshot"] = code_snapshot
        receipt["raw_input_snapshot"] = raw_snapshot
        receipt["artifact_hashes"] = dict(sorted(artifact_hashes.items()))
        (temporary / "receipt.json").write_bytes(_json_bytes(receipt))
        os.rename(temporary, output)
        return receipt
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _unverified_cli_state(state: Mapping, code: str, detail: str) -> dict:
    """Mark a user-supplied LIVE ledger as unverified; never trust its boolean."""
    result = copy.deepcopy(dict(state))
    ledger = copy.deepcopy(result.get("ledger_provenance", {}))
    ledger["origin"] = (ledger.get("origin") if result.get("data_mode") == "HISTORICAL_REPLAY"
                        else "unverified_state_file")
    ledger["reconciled"] = False
    ledger["operations_status"] = "BLOCKED"
    ledger["operations_blocking_codes"] = [code]
    result["ledger_provenance"] = ledger
    result["operations_reconciliation"] = dict(status="BLOCKED", blocking_codes=[code], detail=detail,
                                                 official_platform_connected=False,
                                                 ledger_transport_verified=False)
    return result


def _state_from_operations(state: Mapping, operations_root: Path, ledger_id: str) -> tuple[dict, tuple[Path, ...]]:
    """Resolve a transport-verified official ledger and reconcile final state.

    The first comparison records any difference from the supplied local state.
    When the official record itself is trusted, settled and identity-matched,
    its cash/share values replace the local copy and are reconciled a second
    time.  Manual imports and any identity/time/trust failure remain blocked.
    """
    from daily_auto.operations import OperationError, OperationsStore, TRUSTED

    supplied = copy.deepcopy(dict(state))
    store = OperationsStore(operations_root)
    try:
        record = store.record(ledger_id)
        if record.get("kind") != "ledger":
            raise OperationError("RECORD_KIND", "Expected ledger")
        initial = store.reconcile_ledger(ledger_id, supplied,
            team_id=str(supplied.get("team_id")), as_of=str(supplied.get("as_of")))
    except (OperationError, PreflightError, KeyError, TypeError, ValueError, OSError) as exc:
        code = getattr(exc, "code", "OPERATIONS_LEDGER_UNAVAILABLE")
        return _unverified_cli_state(supplied, code, str(exc)), (store.db.resolve(),)

    raw_dir = Path(operations_root).resolve() / "raw"
    evidence_paths = [raw_dir / (record["raw_sha"] + ".bin"), store.db.resolve()]
    for key in ("adapter_sha256", "spec_sha256"):
        if record.get("metadata", {}).get(key):
            evidence_paths.append(raw_dir / (record["metadata"][key] + ".bin"))
    evidence_paths = tuple(dict.fromkeys(evidence_paths))
    resolvable = {"LEDGER_RECONCILIATION_MISMATCH", "LOCAL_LEDGER_FIELD_MISSING"}
    remaining = set(initial.get("blocking_codes", ())) - resolvable
    if record.get("provenance") != TRUSTED or remaining:
        result = _unverified_cli_state(supplied,
            next(iter(sorted(remaining or {"OFFICIAL_LEDGER_ORIGIN_UNVERIFIED"}))),
            "OperationsStore ledger did not satisfy transport, settlement, identity and time checks")
        result["operations_reconciliation"] = _plain(initial)
        result["operations_reconciliation"]["official_platform_connected"] = False
        result["operations_reconciliation"]["ledger_transport_verified"] = False
        return result, evidence_paths

    official = record["data"]
    resolved = copy.deepcopy(supplied)
    # A local state cannot assert the organizer warning count.  The separate
    # cap-history resolver reads the optional value from this authenticated
    # OperationsStore record and records UNKNOWN when the field is absent.
    resolved.pop("official_warning_count", None)
    for field in ("cash", "nav", "dividend_receivable", "holdings"):
        resolved[field] = copy.deepcopy(official[field])
    resolved["known_at"] = record["acquired_at"]
    resolved["ledger_provenance"] = dict(origin="official_settlement", reconciled=True,
        ledger_id=official["ledger_id"], operations_record_id=ledger_id,
        known_at=record["acquired_at"], authority="organizer",
        source_url=record.get("metadata", {}).get("url"), sha256=record["raw_sha"])
    final = store.reconcile_ledger(ledger_id, resolved,
        team_id=str(resolved.get("team_id")), as_of=str(resolved.get("as_of")))
    if final.get("status") != "TRUSTED_RECONCILED":
        result = _unverified_cli_state(resolved, "FINAL_LEDGER_RECONCILIATION_FAILED",
                                       "Official overlay did not reconcile exactly")
        result["operations_reconciliation"] = _plain(final)
        result["operations_reconciliation"]["initial_differences"] = initial.get("differences", [])
        result["operations_reconciliation"]["official_platform_connected"] = False
        result["operations_reconciliation"]["ledger_transport_verified"] = True
        return result, evidence_paths
    resolved["operations_reconciliation"] = dict(status="TRUSTED_RECONCILED",
        blocking_codes=[], record_id=ledger_id, raw_sha256=record["raw_sha"],
        initial_differences=initial.get("differences", []), official_platform_connected=False,
        ledger_transport_verified=True,
        scope="READ_ONLY_LEDGER_ACQUISITION; NO PLAN_SUBMISSION OR ORDER_PLACEMENT")
    return resolved, evidence_paths


def _restore_passive_cap_history(state: Mapping, *, operations_root: Path | None,
                                 ledger_id: str | None,
                                 calendar_record_id: str | None) -> tuple[dict, tuple[Path, ...]]:
    """Strip user ages and restore only an independently reproducible history."""
    from daily_auto.compliance_state import resolve_passive_cap_days

    resolved = copy.deepcopy(dict(state))
    for row in resolved.get("holdings", []):
        row.pop("passive_cap_days", None)
    ages, report, evidence = resolve_passive_cap_days(state=resolved,
        operations_root=operations_root, ledger_id=ledger_id,
        calendar_record_id=calendar_record_id)
    if report.get("status") == "TRUSTED_RESTORED":
        if set(ages) != {row.get("ticker") for row in resolved.get("holdings", [])}:
            raise PacketBuildError("PASSIVE_CAP_HISTORY_COVERAGE",
                                   "Trusted passive-cap history must cover every current holding")
        for row in resolved.get("holdings", []):
            age = ages[row["ticker"]]
            if isinstance(age, bool) or not isinstance(age, int) or age < 0:
                raise PacketBuildError("PASSIVE_CAP_HISTORY_VALUE", row["ticker"])
            row["passive_cap_days"] = age
    resolved["passive_cap_history"] = _plain(report)
    return resolved, tuple(Path(path).resolve() for path in evidence)


def _phase_block(code: str, detail: str) -> tuple[dict, dict, tuple[Path, ...]]:
    report = dict(status="UNKNOWN_BLOCKED", blocking_codes=[code], detail=detail,
                  buy_phase=None, source="NO_INFERENCE")
    return dict(phase_status="UNKNOWN_BLOCKED", buy_phase=False,
                desired_symbols=None, phase_report=report), report, ()


def _same_json(left, right) -> bool:
    """Compare JSON structures with Decimal/float values by decimal spelling."""
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_same_json(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    if isinstance(left, (Decimal, int, float)) and not isinstance(left, bool):
        if not isinstance(right, (Decimal, int, float)) or isinstance(right, bool):
            return False
        return decimal(left, "comparison") == decimal(right, "comparison")
    return left == right


def _accepted_plan(store, *, team_id: str, trade_date: str, plan_sha256: str) -> Mapping:
    """Use only a public read-only accepted-plan lookup; never query SQLite here."""
    lookup = (getattr(store, "accepted_plan", None)
              or getattr(store, "accepted_plan_status", None))
    if not callable(lookup):
        raise PacketBuildError("ACCEPTED_PLAN_LOOKUP_UNAVAILABLE",
                               "OperationsStore lacks a read-only exact accepted-plan lookup")
    result = lookup(team_id=team_id, trade_date=trade_date, plan_sha256=plan_sha256)
    if not isinstance(result, Mapping) or result.get("status") != "VERIFIED_ACCEPTED":
        raise PacketBuildError("PREVIOUS_PLAN_NOT_ACCEPTED", "No exact VERIFIED_ACCEPTED prior D-Plan receipt")
    if any(result.get(key) != value for key, value in
           (("team_id", team_id), ("trade_date", trade_date), ("plan_sha256", plan_sha256))):
        raise PacketBuildError("PREVIOUS_PLAN_RECEIPT_MISMATCH", "Accepted receipt identity differs")
    return result


def resolve_planner_context(*, state: Mapping, config: Mapping,
                            previous_packet: Path | None,
                            operations_store=None) -> tuple[dict, dict, tuple[Path, ...]]:
    """Recover the two-session planner phase from independently bound evidence.

    A user boolean is never accepted.  Empty initial capital may bootstrap with
    ``buy_phase=False``.  Existing holdings require the exact prior packet, an
    official accepted-plan receipt, both prior/current trusted ledgers and an
    exact share transition after the accepted orders.
    """
    holdings = _holdings(state)
    initial = decimal(config.get("initial_cash"), "initial_cash")
    if not holdings:
        cash = decimal(state.get("cash"), "cash")
        nav = decimal(state.get("nav"), "nav")
        receivable = decimal(state.get("dividend_receivable", 0), "dividend_receivable")
        if cash == nav == initial and receivable == 0:
            report = dict(status="BOOTSTRAP_EMPTY", blocking_codes=[], buy_phase=False,
                          source="EXPLICIT_EMPTY_INITIAL_LEDGER", initial_cash=str(initial))
            context = dict(phase_status="BOOTSTRAP_EMPTY", buy_phase=False,
                           desired_symbols=None, phase_report=report)
            return context, report, ()
        return _phase_block("BOOTSTRAP_LEDGER_MISMATCH",
                            "Empty holdings require cash=nav=fixed initial_cash and zero receivable")
    if state.get("operations_reconciliation", {}).get("status") != "TRUSTED_RECONCILED":
        return _phase_block("CURRENT_LEDGER_NOT_TRUSTED", "Current holdings lack trusted official reconciliation")
    if previous_packet is None:
        return _phase_block("PREVIOUS_PACKET_MISSING", "Existing holdings require the previous immutable packet")
    if operations_store is None:
        return _phase_block("OPERATIONS_STORE_MISSING", "Previous plan acceptance and ledger cannot be verified")

    packet = Path(previous_packet).resolve()
    try:
        receipt_path = packet / "receipt.json"
        receipt = load_json(receipt_path)
        artifact_hashes = receipt.get("artifact_hashes", {})
        if not isinstance(artifact_hashes, Mapping) or not artifact_hashes:
            raise PacketBuildError("PREVIOUS_PACKET_MANIFEST", "Prior receipt has no artifact hashes")
        required_artifacts = {
            "input_snapshot/state.json", "input_snapshot/config.json", "preflight.json"
        }
        if not required_artifacts <= set(map(str, artifact_hashes)):
            missing = sorted(required_artifacts - set(map(str, artifact_hashes)))
            raise PacketBuildError("PREVIOUS_PACKET_MANIFEST",
                                   "Prior receipt omits required artifacts: " + ",".join(missing))
        verified_paths = [receipt_path]
        for relative, expected in artifact_hashes.items():
            relative_path = Path(str(relative))
            target = (packet / relative_path).resolve()
            if relative_path.is_absolute() or packet not in target.parents or not target.is_file():
                raise PacketBuildError("PREVIOUS_PACKET_PATH", str(relative))
            if not SHA.fullmatch(str(expected)) or _sha(target) != str(expected).removeprefix("sha256:"):
                raise PacketBuildError("PREVIOUS_PACKET_HASH", str(relative))
            verified_paths.append(target)
        plan_names = [name for name in artifact_hashes
                      if Path(str(name)).parent == Path(".")
                      and Path(str(name)).name.startswith("D-Plan_")]
        if len(plan_names) != 1:
            raise PacketBuildError("PREVIOUS_PACKET_PLAN", "Prior packet requires exactly one D-Plan")
        plan_path = (packet / str(plan_names[0])).resolve()
        plan = load_json(plan_path)
        prior_state_path = packet / "input_snapshot/state.json"
        prior_config_path = packet / "input_snapshot/config.json"
        prior_preflight_path = packet / "preflight.json"
        prior_state = load_json(prior_state_path)
        prior_config = load_json(prior_config_path)
        prior_preflight = load_json(prior_preflight_path)
        if receipt.get("mode") != "LIVE_PACKET_LOCAL_ONLY":
            raise PacketBuildError("PREVIOUS_PACKET_NOT_LIVE", "Research packet cannot establish live planner phase")
        if receipt.get("strategy_id") != config.get("strategy_id") or not _same_json(prior_config, config):
            raise PacketBuildError("PREVIOUS_PACKET_CONFIG", "Previous packet used a different fixed strategy/config")
        if plan.get("team_id") != state.get("team_id") or plan.get("trade_date") != state.get("as_of"):
            raise PacketBuildError("PREVIOUS_PACKET_IDENTITY", "Prior plan team/date must match current settled ledger")
        if prior_state.get("team_id") != state.get("team_id"):
            raise PacketBuildError("PREVIOUS_STATE_IDENTITY", "Prior ledger team differs")
        action = prior_state.get("corporate_actions", {})
        if action.get("trade_date") != plan.get("trade_date") or action.get("status") != "NONE_CONFIRMED":
            raise PacketBuildError("PREVIOUS_CORPORATE_ACTION_UNKNOWN",
                                   "Cannot infer share transition across an unverified corporate action")
        if "CORPORATE_ACTION_STATE_UNKNOWN" in prior_preflight.get("blocking_codes", []):
            raise PacketBuildError("PREVIOUS_CORPORATE_ACTION_UNKNOWN", "Prior preflight did not clear actions")

        plan_sha = _sha(plan_path)
        accepted = _accepted_plan(operations_store, team_id=str(state["team_id"]),
                                  trade_date=str(state["as_of"]), plan_sha256=plan_sha)
        prior_record_id = prior_state.get("ledger_provenance", {}).get("operations_record_id")
        if not prior_record_id:
            raise PacketBuildError("PREVIOUS_LEDGER_RECORD_MISSING", "Prior packet lacks OperationsStore ledger identity")
        prior_reconcile = operations_store.reconcile_ledger(prior_record_id, prior_state,
            team_id=str(prior_state["team_id"]), as_of=str(prior_state["as_of"]))
        if prior_reconcile.get("status") != "TRUSTED_RECONCILED":
            raise PacketBuildError("PREVIOUS_LEDGER_NOT_TRUSTED", "Prior packet ledger no longer reconciles")

        expected_holdings = _holdings(prior_state)
        for order in plan.get("orders", []):
            ticker = str(order["ticker"])
            quantity = int(order["shares"]) * (1 if order["side"] == "BUY" else -1)
            expected_holdings[ticker] = expected_holdings.get(ticker, 0) + quantity
            if expected_holdings[ticker] < 0:
                raise PacketBuildError("PREVIOUS_PLAN_OVERSELL", ticker)
            if expected_holdings[ticker] == 0:
                expected_holdings.pop(ticker)
        if expected_holdings != holdings:
            raise PacketBuildError("PREVIOUS_EXECUTION_NOT_RECONCILED",
                                   "Accepted prior orders do not match current official shares")

        phase_rows = [row for row in plan.get("observations", []) if row.get("obs_id") == "O3"]
        if len(phase_rows) != 1:
            raise PacketBuildError("PREVIOUS_PHASE_UNBOUND", "Accepted D-Plan lacks one O3 phase record")
        phase_row = phase_rows[0]
        match = re.search(
            r"plan_reason=([A-Z0-9_:;-]+);phase_before=([01]);phase_after=([01])。",
            str(phase_row.get("statement", "")))
        if not match:
            raise PacketBuildError("PREVIOUS_PHASE_UNBOUND", "Accepted D-Plan does not bind reason and phase")
        reason, before_token, after_token = match.groups()
        values = phase_row.get("values", {})
        if (values.get("phase_before") != int(before_token)
                or values.get("phase_after") != int(after_token)):
            raise PacketBuildError("PREVIOUS_PHASE_UNBOUND", "Accepted D-Plan phase values disagree with its text")
        expected_after = int(reason == "SELL_THEN_WAIT_SETTLEMENT")
        if int(after_token) != expected_after:
            raise PacketBuildError("PREVIOUS_PHASE_TRANSITION", "Accepted D-Plan phase transition differs from runtime")
        planner_result = receipt.get("planner_result", {})
        if (planner_result.get("reason") != reason
                or planner_result.get("phase_before") != bool(int(before_token))
                or planner_result.get("phase_after") != bool(int(after_token))):
            raise PacketBuildError("PREVIOUS_PHASE_RECEIPT_MISMATCH",
                                   "Receipt phase differs from accepted D-Plan")
        buy_phase = bool(int(after_token))
        report = dict(status="TRUSTED_PRIOR", blocking_codes=[], buy_phase=buy_phase,
            source="ACCEPTED_DPLAN_PLUS_PRIOR_AND_CURRENT_SETTLED_LEDGERS",
            previous_plan_sha256=plan_sha, accepted_receipt_id=accepted.get("receipt_id"),
            previous_reason=reason, share_transition_names=len(expected_holdings))
        context = dict(phase_status="TRUSTED_PRIOR", buy_phase=buy_phase,
                       desired_symbols=None, phase_report=report)
        evidence = [receipt_path, plan_path, prior_state_path, prior_config_path, prior_preflight_path]
        prior_record = operations_store.record(prior_record_id)
        raw_dir = Path(operations_store.root).resolve() / "raw"
        evidence.append(raw_dir / (prior_record["raw_sha"] + ".bin"))
        if accepted.get("record_id"):
            accepted_record = operations_store.record(accepted["record_id"])
            evidence.append(raw_dir / (accepted_record["raw_sha"] + ".bin"))
        return context, report, tuple(dict.fromkeys(Path(path).resolve() for path in evidence))
    except (PacketBuildError, PreflightError, KeyError, TypeError, ValueError, OSError) as exc:
        return _phase_block(getattr(exc, "code", "PREVIOUS_PACKET_INVALID"), str(exc))


def _load_fixed_strategy():
    """Load the only permitted CLI strategy entrypoint."""
    entry = importlib.import_module("v2_offcial_best_deep_tuning")
    required = ("build_config", "get_params", "prepare_signals")
    if any(not callable(getattr(entry, name, None)) for name in required):
        raise PacketBuildError("FIXED_ENTRY_INTERFACE", "Pinned entry requires build_config/get_params/prepare_signals")
    config = entry.build_config(track="official_ex_post")
    if config.get("full_tuning_params") != entry.get_params():
        raise PacketBuildError("FIXED_ENTRY_PARAMS", "Entrypoint config and selected parameters differ")
    audit = entry.verify_release() if callable(getattr(entry, "verify_release", None)) else None
    strategy = importlib.import_module("src.official_deep_tuning")
    planner = strategy.OfficialPlanner()
    code_paths = [Path(inspect.getsourcefile(entry)), Path(inspect.getsourcefile(strategy))]
    if audit is not None:
        study = Path(getattr(entry, "STUDY", ROOT / "outputs/full_tuned_v2"))
        code_paths.extend([ROOT / "scripts/audit_official_deep.py",
                           study / "audit.json", study / "selection.json",
                           study / "official_ex_post/final/full_tuned_v2/config.json"])
        for relative in audit.get("input_hashes", {}):
            path = ROOT / relative
            if path.suffix == ".py":
                code_paths.append(path)
    return entry, config, planner, code_paths


def _prepared_bundle(value, *, required_paths: Sequence[Path]) -> SignalBundle:
    """Normalize the fixed entrypoint result while retaining every raw input hash."""
    if isinstance(value, SignalBundle):
        raw = dict(frame=value.frame, as_of=value.as_of, known_at=value.known_at,
                   sources=value.sources, input_paths=value.input_paths)
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        raise PacketBuildError("FIXED_ENTRY_SIGNALS", "prepare_signals must return SignalBundle or a mapping")
    try:
        frame = raw["frame"]
        as_of = str(raw["as_of"])
        known_at = str(raw["known_at"])
        sources = tuple(raw["sources"])
    except (KeyError, TypeError):
        raise PacketBuildError("FIXED_ENTRY_SIGNALS", "prepared signals lack frame/as_of/known_at/sources") from None
    if not isinstance(frame, pd.DataFrame) or not all(isinstance(item, Mapping) for item in sources):
        raise PacketBuildError("FIXED_ENTRY_SIGNALS", "prepared frame/sources have invalid types")
    extras = tuple(Path(path) for path in raw.get("input_paths", ()))
    paths = tuple(dict.fromkeys([*(Path(path) for path in required_paths), *extras]))
    return SignalBundle(frame=frame, as_of=as_of, known_at=known_at,
                        sources=sources, input_paths=paths)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate one fixed full-tuned D-Plan packet; never submit it.")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--daily", type=Path, required=True,
                        help="Causal daily market rows through state.as_of")
    parser.add_argument("--hourly", type=Path, required=True,
                        help="Causal intraday rows through state.as_of")
    parser.add_argument("--source-manifest", type=Path, required=True,
                        help="Timestamped evidence for the daily/hourly inputs")
    parser.add_argument("--operations-root", type=Path,
                        help="Local OperationsStore containing an acquired official ledger")
    parser.add_argument("--ledger-id",
                        help="OperationsStore record id; requires --operations-root")
    parser.add_argument("--calendar-record-id",
                        help="Trusted official calendar record used to rebuild passive-cap ages")
    parser.add_argument("--previous-packet", type=Path,
                        help="Immutable previous-day packet; required once the official ledger has holdings")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        state = load_json(args.state)
        if bool(args.operations_root) != bool(args.ledger_id):
            raise PacketBuildError("OPERATIONS_ARGUMENTS", "--operations-root and --ledger-id must be supplied together")
        operation_inputs: tuple[Path, ...] = ()
        if args.operations_root:
            state, operation_inputs = _state_from_operations(state, args.operations_root, args.ledger_id)
        else:
            state = _unverified_cli_state(state, "OPERATIONS_LEDGER_NOT_SUPPLIED",
                                          "No trusted OperationsStore ledger record was supplied")
        state, cap_inputs = _restore_passive_cap_history(state,
            operations_root=args.operations_root, ledger_id=args.ledger_id,
            calendar_record_id=args.calendar_record_id)
        operation_inputs = tuple(dict.fromkeys([*operation_inputs, *cap_inputs]))
        source_manifest = load_json(args.source_manifest)
        entry, config, planner, code_paths = _load_fixed_strategy()
        operations_store = None
        if args.operations_root:
            from daily_auto.operations import OperationsStore
            operations_store = OperationsStore(args.operations_root)
        planner_context, _, phase_inputs = resolve_planner_context(state=state, config=config,
            previous_packet=args.previous_packet, operations_store=operations_store)
        required_inputs = [args.state, args.daily, args.hourly, args.source_manifest]
        required_inputs.extend(operation_inputs)
        required_inputs.extend(phase_inputs)
        prepared = entry.prepare_signals(daily_path=args.daily, hourly_path=args.hourly,
            state=copy.deepcopy(state), source_manifest=source_manifest,
            input_paths=tuple(required_inputs))
        bundle = _prepared_bundle(prepared,
            required_paths=tuple(required_inputs))
        packet = build_packet(state=state, signals=bundle, config=config, planner=planner,
                              code_paths=code_paths, planner_context=planner_context)
        receipt = write_packet(packet, args.output)
    except (PacketBuildError, PreflightError, FileExistsError, KeyError, TypeError, ValueError, OSError) as exc:
        print(json.dumps(dict(status="BLOCK", submission_status="BLOCK_SUBMISSION",
                              code=getattr(exc, "code", "INPUT_ERROR"), error=str(exc)),
                         ensure_ascii=False))
        return 2
    print(json.dumps({key: receipt[key] for key in ("preflight_status", "submission_status",
                                                     "strategy_id", "code_bundle_sha256")}, ensure_ascii=False))
    return 0 if receipt["preflight_status"] == "LOCAL_PREFLIGHT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
