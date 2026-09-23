"""Counterexamples for April-first monthly selection and negative results."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts import verify_monthly_expansion as verify


class MonthlyExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = verify.read(verify.DESIGN)
        cls.anchors = verify._prior_candidates()
        cls.calendar = pd.read_csv(verify.ROOT / "data/v2/market_daily.csv", usecols=["date"]).date

    @staticmethod
    def episode(candidate_id, month, *, good=True, score=.01, turnover=1.):
        return dict(track="official_ex_post", candidate_id=candidate_id,
                    episode=f"m25_2025-{month:02}",
                    independent_audit="PASS", complete_period=good, disqualified=not good,
                    official_compliance="UNKNOWN_BLOCK_SUBMISSION",
                    total_return=score, economic_total_return=score,
                    max_drawdown=.02, turnover_two_way=turnover,
                    **{key: 0 for key in verify.base.ZERO})

    def test_declared_72_positions_and_cross_year_window_are_checked(self):
        candidates, windows = verify.verify_design(self.design, self.anchors, self.calendar)
        self.assertEqual(len(candidates), 72)
        self.assertEqual(len(windows), 22)
        self.assertGreater(windows["m25_2025-12"]["end"], "2025-12-31")
        changed = copy.deepcopy(self.design)
        changed["candidates"][0]["params"]["ema_slow"] = 200
        with self.assertRaisesRegex(ValueError, "Grid parameters"):
            verify.verify_design(changed, self.anchors, self.calendar)
        changed = copy.deepcopy(self.design)
        changed["training_episodes"].append("m25_2025-12")
        with self.assertRaisesRegex(ValueError, "training contract"):
            verify.verify_design(changed, self.anchors, self.calendar)

    def test_complete_negative_april_evidence_selects_nobody(self):
        records = [self.episode(c["candidate_id"], 4, good=False) for c in self.design["candidates"]]
        ranking, selected = verify.independent_ranking(records, self.design["candidates"])
        self.assertIsNone(selected)
        self.assertEqual(len(ranking), 72)
        self.assertTrue(all(r["evaluated_train_windows"] == 1 and not r["training_eligible"]
                            and r["median_return"] is None for r in ranking))

    def test_one_failed_month_blocks_selection_and_diagnostics_do_not_rank(self):
        candidates = self.design["candidates"]
        records = [self.episode(c["candidate_id"], 4, good=False) for c in candidates]
        a, b = candidates[0]["candidate_id"], candidates[1]["candidate_id"]
        records = [r for r in records if r["candidate_id"] not in (a, b)]
        for cid, score in ((a, .03), (b, .02)):
            for month in range(1, 12):
                records.append(self.episode(cid, month, score=score))
        records.append(dict(self.episode(b, 7, score=.02), episode="m25_2026-07",
                            economic_total_return=999.))
        ranking, selected = verify.independent_ranking(records, candidates)
        self.assertEqual(selected, a)
        self.assertEqual(sum(r["training_eligible"] for r in ranking), 2)
        for row in records:
            if row["candidate_id"] == a and row["episode"] == "m25_2025-04":
                row["execution_price_bound_breaches"] = 1
        ranking, selected = verify.independent_ranking(records, candidates)
        self.assertEqual(selected, b)
        for row in records:
            if row["candidate_id"] == b and row["episode"] == "m25_2025-04":
                row["complete_period"] = False
        ranking, selected = verify.independent_ranking(records, candidates)
        self.assertIsNone(selected)

    def test_saved_audit_requires_exact_match_and_never_overwrites(self):
        audit = dict(status="PASS", verified_candidates=72, verified_runs=72,
                     selected_candidate=None, submission_status="BLOCK_SUBMISSION")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "has not been built"):
                verify.check_saved_audit(root, audit)
            verify.check_saved_audit(root, audit, write=True)
            verify.check_saved_audit(root, audit)
            path = root / "audit.json"
            changed = dict(audit, verified_runs=71)
            path.write_text(json.dumps(changed))
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Saved monthly expansion audit"):
                verify.check_saved_audit(root, audit, write=True)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
