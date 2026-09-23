"""Offline tests for fixed-strategy live-shaped D-Plan generation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from daily_auto.full_tuned import (PacketBuildError, SignalBundle, build_packet,
                                   write_packet, _state_from_operations,
                                   _unverified_cli_state, _restore_passive_cap_history,
                                   resolve_planner_context)
from daily_auto.operations import OperationsStore, TRUSTED
from daily_auto.validate import OFFICIAL_REFERENCE, load_json
from src.official_deep_tuning import OfficialPlanner
from src.tuning_a_deep import FIELDS, FIXED

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = load_json(OFFICIAL_REFERENCE)
TICKERS = [row["ticker"] for row in REFERENCE["stocks"]]
EXCHANGE = {row["ticker"]: row["exchange"] for row in REFERENCE["stocks"]}
START = datetime.fromisoformat("2026-10-27T08:00:00+08:00")
END = datetime.fromisoformat("2026-10-27T08:01:00+08:00")


def evidence(authority="twse", **extra):
    host = "esun-ai-challenge.tw" if authority == "organizer" else (
        "www.tpex.org.tw" if authority == "tpex" else "www.twse.com.tw")
    return dict(known_at="2026-10-26T19:30:00+08:00", authority=authority,
                source_url=f"https://{host}/UNIT_TEST_ONLY", sha256="0" * 64, **extra)


def state_fixture():
    holdings = [dict(ticker=ticker, shares=4000) for ticker in TICKERS[:20]]
    closes = [dict(ticker=ticker, close="100", as_of="2026-10-26",
                   **evidence(EXCHANGE[ticker])) for ticker in TICKERS]
    return dict(state_version="1.0", data_mode="LIVE", team_id="TEAM_042",
                as_of="2026-10-26", known_at="2026-10-26T19:30:00+08:00",
                cash="2000000", nav="10000000", dividend_receivable="0",
                holdings=holdings, closes=closes,
                calendar=evidence("organizer", trade_date="2026-10-27",
                    previous_session="2026-10-26", sessions=["2026-10-26", "2026-10-27"],
                    is_trading_day=True),
                whitelist=evidence("organizer", tickers=TICKERS.copy(), effective_from="2026-09-18"),
                ledger_provenance=evidence("organizer", origin="official_settlement", reconciled=True),
                corporate_actions=evidence("twse", status="NONE_CONFIRMED", trade_date="2026-10-27"),
                active_share={"status": "UNKNOWN"})


def config_fixture():
    params = dict(target_count=20, replacement_margin=0.0, max_replacements_per_day=0,
        volatility_spike_ratio=2.0, one_day_chase_return=.03, volume_low=.3, volume_high=5.0,
        four_hour_mode="coverage_only", return_short=20, return_long=50,
        ema_fast=20, ema_slow=50, macd_fast=12, macd_slow=26, macd_signal=9,
        momentum_weight=.5, long_return_fraction=.5, cash_guard_ratio=.12)
    assert set(params) == set(FIELDS) | {"cash_guard_ratio"}
    config = dict(FIXED)
    config.update({key: params[key] for key in FIELDS[:8]})
    config.update(strategy_id="full_tuned_v2_UNIT_TEST", target_count=params["target_count"],
        initial_cash=1000000000,
        cash_guard_ratio=params["cash_guard_ratio"], universe_mode="official_ex_post",
        full_tuning_params=params,
        study_policy={"official_live_submission": "BLOCK_IF_UNKNOWN"},
        official_review_policy={"active_share": "UNKNOWN_BLOCK_SUBMISSION"})
    return config


def bundle_fixture(known_at="2026-10-26T19:30:00+08:00"):
    rows = pd.DataFrame(dict(date="2026-10-26", symbol=TICKERS,
        close=100.0, score=[1 - index / 200 for index in range(150)],
        entry_ok=True, exit=False, return20=.1, return50=.2,
        ema20=99.0, ema50=98.0, macd_hist=.01, volume_ratio=1.0,
        trend=1.0, long_trend=1.0))
    sources = tuple(evidence(exchange, content_as_of="2026-10-26T13:30:00+08:00")
                    for exchange in ("twse", "tpex"))
    return SignalBundle(frame=rows, as_of="2026-10-26", known_at=known_at, sources=sources)


class FixedClock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0) if self.values else END


class HoldPlanner:
    def __call__(self, ranked, holdings, cash, nav, config, buy_phase=False, desired_symbols=None):
        return {}, "HOLD_UNIT_TEST", list(holdings)


class MixedPlanner:
    def __call__(self, ranked, holdings, cash, nav, config, buy_phase=False, desired_symbols=None):
        return {TICKERS[0]: -1000, TICKERS[20]: 1000}, "MIXED_UNIT_TEST", [*holdings, TICKERS[20]]


class OddOrderPlanner:
    def __call__(self, ranked, holdings, cash, nav, config, buy_phase=False, desired_symbols=None):
        return {TICKERS[0]: -1000}, "ODD_ORDER_UNIT_TEST", list(holdings)


def build(planner=None, *, state=None, bundle=None, planner_context=None):
    return build_packet(state=state or state_fixture(), signals=bundle or bundle_fixture(),
        config=config_fixture(), planner=planner or HoldPlanner(), code_paths=(),
        planner_context=planner_context or {"phase_status": "TRUSTED_PRIOR", "buy_phase": False},
        clock=FixedClock(START, END))


class FullTunedPacketTests(unittest.TestCase):
    def test_empty_plan_is_complete_and_blocks_unknown_active_share(self):
        packet = build()
        self.assertEqual(packet.plan["orders"], [])
        self.assertEqual(packet.plan["decisions"], [])
        self.assertEqual(len(packet.plan["no_trade_decisions"]), 20)
        self.assertEqual(packet.plan["agent_metadata"]["run_started_at"], START.isoformat())
        self.assertEqual(packet.plan["agent_metadata"]["run_completed_at"], END.isoformat())
        self.assertEqual(packet.preflight["blocking_codes"], ["ACTIVE_SHARE_UNKNOWN"])
        self.assertEqual(packet.receipt["submission_status"], "BLOCK_SUBMISSION")
        self.assertFalse(packet.receipt["network_used"])
        phase = next(row for row in packet.plan["observations"] if row["obs_id"] == "O3")
        self.assertIn("plan_reason=HOLD_UNIT_TEST;phase_before=0;phase_after=0", phase["statement"])
        self.assertEqual(phase["values"]["phase_after"], 0)
        self.assertFalse(packet.receipt["planner_result"]["phase_after"])

    def test_unverified_cli_state_never_claims_reconciliation(self):
        state = _unverified_cli_state(state_fixture(), "NO_LEDGER", "unit test")
        packet = build(state=state)
        self.assertFalse(state["ledger_provenance"]["reconciled"])
        self.assertIn("尚未確認官方對帳", packet.plan["observations"][1]["statement"])
        self.assertIn("LEDGER_UNRECONCILED", packet.preflight["blocking_codes"])

    def test_unknown_phase_with_holdings_emits_no_orders_and_explicit_block(self):
        packet = build(MixedPlanner(), planner_context={"phase_status": "UNKNOWN_BLOCKED",
            "buy_phase": False, "phase_report": {"status": "UNKNOWN_BLOCKED"}})
        self.assertEqual(packet.plan["orders"], [])
        self.assertEqual(packet.receipt["planner_result"]["reason"], "BLOCKED_PRIOR_PHASE_UNKNOWN")
        self.assertIn("PRIOR_PLANNER_PHASE_UNKNOWN", packet.preflight["blocking_codes"])

    def test_phase_resolver_allows_only_exact_empty_initial_bootstrap(self):
        state = state_fixture()
        state.update(holdings=[], cash="1000000000", nav="1000000000")
        context, report, paths = resolve_planner_context(state=state, config=config_fixture(),
            previous_packet=None, operations_store=None)
        self.assertEqual(context["phase_status"], "BOOTSTRAP_EMPTY")
        self.assertFalse(context["buy_phase"])
        self.assertEqual(paths, ())
        state["cash"] = "999999999"
        context, report, _ = resolve_planner_context(state=state, config=config_fixture(),
            previous_packet=None, operations_store=None)
        self.assertEqual(context["phase_status"], "UNKNOWN_BLOCKED")
        self.assertIn("BOOTSTRAP_LEDGER_MISMATCH", report["blocking_codes"])

    def test_phase_resolver_never_infers_existing_holdings_without_prior(self):
        state = state_fixture()
        state["operations_reconciliation"] = {"status": "TRUSTED_RECONCILED"}
        context, report, _ = resolve_planner_context(state=state, config=config_fixture(),
            previous_packet=None, operations_store=None)
        self.assertEqual(context["phase_status"], "UNKNOWN_BLOCKED")
        self.assertIn("PREVIOUS_PACKET_MISSING", report["blocking_codes"])

    def test_user_passive_cap_age_is_removed_without_trusted_history(self):
        state = state_fixture()
        state["holdings"][0]["passive_cap_days"] = 5
        resolved, paths = _restore_passive_cap_history(state, operations_root=None,
            ledger_id=None, calendar_record_id=None)
        self.assertNotIn("passive_cap_days", resolved["holdings"][0])
        self.assertEqual(resolved["passive_cap_history"]["status"], "UNKNOWN")
        self.assertEqual(paths, ())

    def test_official_warning_disqualification_is_an_explicit_block(self):
        state = state_fixture()
        state["passive_cap_history"] = {"status": "TRUSTED_RESTORED",
            "official_warning_count": 3, "warning_disqualification": True}
        packet = build(state=state)
        self.assertIn("OFFICIAL_WARNING_DISQUALIFICATION", packet.preflight["blocking_codes"])

    def test_historical_mode_is_named_research_and_never_green(self):
        state = state_fixture()
        state["data_mode"] = "HISTORICAL_REPLAY"
        state["ledger_provenance"].update(authority="other", origin="research_initial_capital",
                                           reconciled=False)
        packet = build(state=state)
        self.assertEqual(packet.receipt["mode"], "RESEARCH_PACKET_LOCAL_ONLY")
        self.assertIn("NON_LIVE_STATE", packet.preflight["blocking_codes"])
        self.assertEqual(packet.preflight["submission_status"], "BLOCK_SUBMISSION")

    def test_mixed_orders_match_official_formula_and_report(self):
        packet = build(MixedPlanner())
        expected = {TICKERS[0]: -1000, TICKERS[20]: 1000}
        actual = {row["ticker"]: row["shares"] * (1 if row["side"] == "BUY" else -1)
                  for row in packet.plan["orders"]}
        self.assertEqual(actual, expected)
        self.assertEqual({row["ticker"] for row in packet.plan["decisions"]}, set(expected))
        self.assertEqual(len(packet.plan["no_trade_decisions"]), 19)
        self.assertNotIn("OFFICIAL_DERIVED_ORDERS", packet.preflight["blocking_codes"])
        self.assertNotIn("PRIOR_HOLDING_COVERAGE", packet.preflight["blocking_codes"])

    def test_odd_holding_is_retained_and_never_fake_sell_all(self):
        state = state_fixture()
        state["holdings"][0]["shares"] = 4500
        state["nav"] = "10050000"
        packet = build(state=state)
        self.assertFalse(any(row["ticker"] == TICKERS[0] for row in packet.plan["orders"]))
        held = next(row for row in packet.plan["no_trade_decisions"] if row["ticker"] == TICKERS[0])
        self.assertIn("零股", held["reason"])
        self.assertIn("EXISTING_ODD_LOT_POLICY", packet.preflight["blocking_codes"])
        with self.assertRaises(PacketBuildError) as cm:
            build(OddOrderPlanner(), state=state)
        self.assertEqual(cm.exception.code, "ODD_HOLDING_ORDER")

    def test_fractional_holding_and_future_inputs_fail_closed(self):
        state = state_fixture()
        state["holdings"][0]["shares"] = 4500.5
        with self.assertRaises(PacketBuildError) as cm:
            build(state=state)
        self.assertEqual(cm.exception.code, "FRACTIONAL_HOLDING")
        with self.assertRaises(PacketBuildError) as cm:
            build(bundle=bundle_fixture("2026-10-27T08:00:01+08:00"))
        self.assertEqual(cm.exception.code, "LOOKAHEAD_SIGNAL")
        state = state_fixture()
        state["closes"][0]["known_at"] = "2026-10-27T08:00:01+08:00"
        with self.assertRaises(PacketBuildError) as cm:
            build(state=state)
        self.assertEqual(cm.exception.code, "LOOKAHEAD_STATE")

    def test_missing_corporate_action_and_as_evidence_remain_blocking(self):
        state = state_fixture()
        state["corporate_actions"]["status"] = "UNKNOWN"
        packet = build(state=state)
        self.assertIn("ACTIVE_SHARE_UNKNOWN", packet.preflight["blocking_codes"])
        self.assertIn("CORPORATE_ACTION_STATE_UNKNOWN", packet.preflight["blocking_codes"])

    def test_real_official_planner_runs_without_network(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
            packet = build(OfficialPlanner())
        self.assertEqual(packet.receipt["planner_result"]["audit"][0]["date"], "2026-10-26")
        self.assertEqual(packet.receipt["submission_status"], "BLOCK_SUBMISSION")

    def test_immutable_write_has_hashes_and_strict_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw1, raw2 = Path(tmp) / "daily.csv", Path(tmp) / "hourly.csv"
            raw1.write_text("daily raw unit test")
            raw2.write_text("hourly raw unit test")
            bundle = bundle_fixture()
            sources = []
            for source, path in zip(bundle.sources, (raw1, raw2)):
                sources.append({**source, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            bundle = SignalBundle(bundle.frame, bundle.as_of, bundle.known_at,
                                  tuple(sources), (raw1, raw2))
            packet = build(MixedPlanner(), bundle=bundle)
            destination = Path(tmp) / "packet"
            receipt = write_packet(packet, destination)
            for name, digest in receipt["artifact_hashes"].items():
                self.assertEqual(hashlib.sha256((destination / name).read_bytes()).hexdigest(), digest)
            parsed = json.loads((destination / packet.filename).read_text())
            self.assertEqual(parsed["orders"], packet.plan["orders"])
            self.assertEqual(len(receipt["raw_input_snapshot"]), 2)
            self.assertIn("daily_auto/full_tuned.py", receipt["code_snapshot"])
            with self.assertRaises(FileExistsError):
                write_packet(packet, destination)

    def test_trusted_operations_ledger_overrides_then_reconciles_local_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OperationsStore(Path(tmp) / "operations")
            official = dict(ledger_id="ledger-unit", team_id="TEAM_042", as_of="2026-10-26",
                settled_at="2026-10-26T19:00:00+08:00", settlement_status="SETTLED",
                cash="2000000", nav="10000000", dividend_receivable="0",
                holdings=[dict(ticker=ticker, shares="4000") for ticker in TICKERS[:20]])
            raw = json.dumps(official).encode()
            with patch("daily_auto.operations._clock", return_value=START):
                record = store._capture("ledger", raw, TRUSTED,
                    {"url": "https://esun-ai-challenge.tw/UNIT_TEST_ONLY/ledger",
                     "origin_authentication": "OFFICIAL_HTTPS_TRANSPORT"}, None)
            local = state_fixture()
            local["cash"] = "1999999"
            local["holdings"][0]["shares"] = 3000
            resolved, evidence_paths = _state_from_operations(local, Path(tmp) / "operations", record["id"])
            self.assertEqual(resolved["cash"], "2000000")
            self.assertEqual(resolved["holdings"][0]["shares"], "4000")
            self.assertTrue(resolved["ledger_provenance"]["reconciled"])
            self.assertEqual(resolved["operations_reconciliation"]["status"], "TRUSTED_RECONCILED")
            self.assertFalse(resolved["operations_reconciliation"]["official_platform_connected"])
            self.assertTrue(resolved["operations_reconciliation"]["ledger_transport_verified"])
            self.assertTrue(all(path.is_file() for path in evidence_paths))
            self.assertTrue(any(path.name == "operations.sqlite3" for path in evidence_paths))


if __name__ == "__main__":
    unittest.main()
