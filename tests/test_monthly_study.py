"""Boundary and counterexample tests for fixed monthly-horizon comparisons."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts import run_monthly_study as runner
from scripts import verify_monthly_study as verifier
from src import monthly_study as model


class MonthlyMetricTests(unittest.TestCase):
    def test_month_boundary_uses_previous_month_end_and_compounds(self):
        frame = pd.DataFrame(dict(date=["2025-01-02", "2025-01-31", "2025-02-28", "2025-03-31"],
                                  economic_nav=[90., 108., 54., 81.]))
        rows = model.monthly_returns(frame, 100.)
        self.assertEqual([r["sessions"] for r in rows], [2, 1, 1])
        np.testing.assert_allclose([r["return_net"] for r in rows], [.08, -.5, .5])
        self.assertAlmostEqual(np.prod([1 + r["return_net"] for r in rows]), .81)
        self.assertAlmostEqual(rows[0]["max_drawdown"], .1)
        verifier.same(rows, verifier.independent_months(frame, 100.), "Independent monthly calculation")

    def test_terminal_book_credit_cannot_move_economic_month_return(self):
        frame = pd.DataFrame(dict(date=["2025-01-31", "2025-02-28"],
                                  economic_nav=[102., 104.], nav=[100., 104.]))
        before = model.monthly_returns(frame, 100.)
        frame.loc[1, "nav"] = 1000.
        self.assertEqual(model.monthly_returns(frame, 100.), before)
        self.assertAlmostEqual(before[1]["return_net"], 104 / 102 - 1)

    def test_rejects_duplicate_unordered_nonfinite_or_nonpositive_nav(self):
        frame = pd.DataFrame(dict(date=["2025-01-02", "2025-01-03"], economic_nav=[100., 101.]))
        bad = []
        duplicate = frame.copy(); duplicate.loc[1, "date"] = duplicate.loc[0, "date"]; bad.append(duplicate)
        bad.append(frame.iloc[::-1])
        for value in (float("nan"), float("inf"), 0., -1.):
            changed = frame.copy(); changed.loc[1, "economic_nav"] = value; bad.append(changed)
        for item in bad:
            with self.subTest(frame=item.to_dict("list")), self.assertRaises(ValueError):
                model.monthly_returns(item)

    def test_2026_returns_and_turnover_do_not_enter_2025_ranking(self):
        dates = [f"2025-{m:02}-28" for m in range(1, 13)] + ["2026-01-28", "2026-09-21"]
        frame = pd.DataFrame(dict(date=dates, economic_nav=[1e9 * 1.01 ** i for i in range(1, 15)],
                                  turnover=[1.] * 14))
        before = model.selection_row("candidate", frame)
        frame.loc[12:, "economic_nav"] *= 100
        frame.loc[12:, "turnover"] *= 1000
        self.assertEqual(model.selection_row("candidate", frame), before)
        self.assertEqual(before["turnover_2025"], 12.)
        verifier.same(before, verifier.independent_ranking("candidate", frame, 1e9), "Independent ranking")

    def test_missing_2025_month_and_duplicate_candidates_rejected(self):
        frame = pd.DataFrame(dict(date=[f"2025-{m:02}-28" for m in range(1, 12)],
                                  economic_nav=[1e9] * 11, turnover=[0.] * 11))
        with self.assertRaisesRegex(ValueError, "twelve"):
            model.selection_row("short", frame)
        row = dict(candidate_id="same", median_2025=.1, worst_2025=-.1, turnover_2025=1.)
        with self.assertRaises(ValueError):
            model.select_candidate([row, dict(row)])
        with self.assertRaises(ValueError):
            model.select_candidate([dict(row, median_2025=float("nan"))])

    def test_selection_is_one_fixed_candidate_with_declared_tie_breaks(self):
        rows = [dict(candidate_id="a", median_2025=.1, worst_2025=-.2, turnover_2025=1.),
                dict(candidate_id="b", median_2025=.1, worst_2025=-.1, turnover_2025=2.),
                dict(candidate_id="c", median_2025=.1, worst_2025=-.1, turnover_2025=1.)]
        self.assertEqual(model.select_candidate(rows), "c")
        rows.append(dict(rows[-1], candidate_id="b2"))
        self.assertEqual(model.select_candidate(rows), "b2")


class MonthlyEpisodeTests(unittest.TestCase):
    def test_reset_windows_count_exactly_25_sessions_and_preserve_contest_dates(self):
        daily = pd.read_csv(verifier.ROOT / "data/v2/market_daily.csv", usecols=["date"])
        calendar = sorted(set(daily.date))
        actual = model.episode_windows(calendar)
        self.assertEqual(actual, verifier.independent_windows(calendar))
        self.assertEqual(len(actual), 22)
        resets = [r for r in actual if r["kind"] == "reset_25_sessions"]
        self.assertEqual(len(resets), 20)
        for row in resets:
            dates = [d for d in calendar if row["start"] <= d <= row["end"]]
            self.assertEqual(len(dates), 25)
            month = row["episode"].removeprefix("m25_")
            self.assertEqual(row["start"], next(d for d in calendar if d.startswith(month)))
        contest = actual[-1]
        self.assertEqual(contest["start"], "2025-10-26")
        self.assertEqual(next(d for d in calendar if contest["start"] <= d <= contest["end"]), "2025-10-27")
        with self.assertRaises(ValueError):
            model.episode_windows([d for d in calendar if d != "2025-01-02"])

    def test_any_failed_daily_gate_cannot_be_eligible(self):
        good = dict(independent_audit="PASS", complete_period=True, disqualified=False,
                    official_compliance="UNKNOWN_BLOCK_SUBMISSION", total_return=10.,
                    max_drawdown=0., turnover_two_way=1., **dict.fromkeys(verifier.prior.ZERO, 0))
        self.assertTrue(verifier.prior.measured_eligible(good))
        for key in verifier.prior.ZERO:
            for value in (1, float("nan"), None):
                with self.subTest(key=key, value=value):
                    self.assertFalse(verifier.prior.measured_eligible(dict(good, **{key: value})))
            missing = dict(good); missing.pop(key)
            self.assertFalse(verifier.prior.measured_eligible(missing))
        self.assertFalse(verifier.prior.measured_eligible(dict(good, complete_period=False)))

    def test_partial_month_excluded_and_failed_episodes_retained_in_denominator(self):
        ids = ["x0352", "challenger"]
        records, months = [], []
        dates = [f"2025-{m:02}-28" for m in range(1, 13)] + [f"2026-{m:02}-28" for m in range(1, 9)] + ["2026-09-21"]
        values = [1e9 * 1.01 ** i for i in range(1, 21)]
        values.append(values[-1] * 10)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for track in runner.TRACKS:
                for cid in ids:
                    equity = pd.DataFrame(dict(date=dates, economic_nav=values))
                    folder = root / "runs" / track / cid / "full"; folder.mkdir(parents=True)
                    equity.to_csv(folder / "equity.csv", index=False)
                    full = dict(track=track, candidate_id=cid, episode="full", kind="continuous",
                                complete_period=True, research_eligible=True,
                                total_return=values[-1] / 1e9 - 1, economic_total_return=values[-1] / 1e9 - 1,
                                max_daily_volume_participation=.1)
                    records.append(full)
                    for index in range(20):
                        records.append(dict(track=track, candidate_id=cid, episode=f"reset_{index:02}",
                            kind="reset_25_sessions", research_eligible=index < 15,
                            economic_total_return=.01 if index < 15 else 100.))
                    months.extend(dict(track=track, candidate_id=cid, **r,
                                       partial_period=r["month"] == "2026-09", research_eligible=True)
                                  for r in verifier.independent_months(equity, 1e9))
            runner.summarize(root, records, ids)
            summary = json.loads((root / "summary.json").read_text())
            verifier.same(summary, verifier.independent_summary(records, months, ids), "Independent summary")
            for row in summary["candidates"]:
                self.assertEqual(row["complete_months"], 20)
                self.assertAlmostEqual(row["median_month"], .01)
                self.assertEqual(row["reset_count"], 20)
                self.assertEqual(row["reset_eligible"], 15)
                self.assertAlmostEqual(row["eligible_reset_median"], .01)
            for row in summary["paired_resets"]:
                self.assertEqual(row["paired_eligible"], 15)
                self.assertEqual(row["selected_wins"], 0)
                self.assertEqual(row["ties"], 15)

    def test_csv_tamper_dropped_failed_episode_or_duplicate_is_rejected(self):
        expected = [dict(episode="good", research_eligible=True), dict(episode="failed", research_eligible=False)]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "episodes.csv"
            pd.DataFrame(expected[:1]).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "row count"):
                verifier.verify_csv(path, expected, ("episode",))
            pd.DataFrame([expected[0], expected[0]]).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                verifier.verify_csv(path, expected, ("episode",))


class PortableScreeningTests(unittest.TestCase):
    def test_saved_audit_tampering_is_rejected_without_overwrite(self):
        result = dict(status="PASS", verified_runs=88, submission_status="BLOCK_SUBMISSION")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            verifier.check_saved_audit(root, result, write=True)
            verifier.check_saved_audit(root, result)
            path = root / "audit.json"
            changed = dict(result, verified_runs=87)
            path.write_text(json.dumps(changed))
            before = path.read_bytes()
            for write in (False, True):
                with self.subTest(write=write), self.assertRaisesRegex(ValueError, "Saved monthly release audit"):
                    verifier.check_saved_audit(root, result, write=write)
                self.assertEqual(path.read_bytes(), before)

    def test_clean_checkout_compact_fallback_rejects_artifact_and_receipt_tampering(self):
        """No original trial ledgers are copied: the historical audit is the anchor."""
        source = verifier.DEFAULT_OUTPUT / "screening"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compact = root / "outputs/monthly_horizon_20260923"
            shutil.copytree(source, compact / "screening")
            for study in runner.STUDIES:
                target = root / study
                target.mkdir(parents=True)
                for name in ("audit.json", "trials.csv"):
                    shutil.copyfile(verifier.ROOT / study / name, target / name)
            with patch.object(runner, "ROOT", root), patch.object(runner, "DEFAULT_OUTPUT", compact):
                candidates = runner.source_candidates()
                self.assertEqual(len(candidates), 48)
                first = candidates[0]
                folder = compact / "screening" / first["candidate_id"]
                self.assertEqual(runner.source_folder(first["source_study"], first["candidate_id"]), folder)
                equity = folder / "equity.csv"
                equity.write_bytes(equity.read_bytes() + b"\n")
                with self.assertRaises(ValueError):
                    runner.source_candidates()
                receipt = json.loads((folder / "receipt.json").read_text())
                receipt["equity.csv"] = hashlib.sha256(equity.read_bytes()).hexdigest()
                (folder / "receipt.json").write_text(json.dumps(receipt))
                with self.assertRaisesRegex(ValueError, "prior independent audit"):
                    runner.source_candidates()


if __name__ == "__main__":
    unittest.main()
