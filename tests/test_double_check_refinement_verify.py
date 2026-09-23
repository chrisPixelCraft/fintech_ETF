"""Independent coverage and claim guards for the third-round refinement."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts import verify_double_check_refinement as verify


class RefinementVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.study = verify.read(verify.STUDY_PATH)
        cls.parent, cls.old_candidates, _ = verify._prior_evidence(cls.study)
        cls.parent_audit = verify.read(
            verify.PRIOR_OUTPUT / "official_ex_post/final/expansion_selected/independent_audit.json")

    def test_frozen_grid_rejects_missing_or_substituted_candidate(self):
        trials = verify.validate_grid(self.study, self.parent, self.old_candidates)
        self.assertEqual(len(trials), 80)
        changed = copy.deepcopy(self.study)
        changed["grid"][0]["candidate_id"] = "x0352"
        with self.assertRaises(ValueError):
            verify.validate_grid(changed, self.parent, self.old_candidates)
        changed = copy.deepcopy(self.study)
        changed["candidates"][0]["params"]["momentum_weight"] = 0.95
        with self.assertRaises(ValueError):
            verify.validate_grid(changed, self.parent, self.old_candidates)
        changed = copy.deepcopy(self.study)
        changed["axes"]["volume_high"] = [1.5, 2.0, 3.0]
        with self.assertRaisesRegex(ValueError, "axes differ"):
            verify.validate_grid(changed, self.parent, self.old_candidates)

    def _selection(self, candidate_id="x0352", params=None, score=None):
        audit = self.parent_audit
        return dict(candidate_id=candidate_id, params=params or self.parent["params"],
                    source="prior" if candidate_id == "x0352" else "new",
                    selected_on="official_ex_post", scope="EX_POST_DEVELOPMENT_ONLY",
                    formal_submission="BLOCK_IF_UNKNOWN",
                    official_compliance="UNKNOWN_BLOCK_SUBMISSION",
                    submission_status="BLOCK_SUBMISSION", incumbent_candidate_id="x0352",
                    incumbent_total_return=audit["total_return"],
                    total_return=audit["total_return"] if score is None else score,
                    max_drawdown=audit["max_drawdown"],
                    turnover_two_way=audit["turnover_two_way"])

    def test_high_return_with_one_breach_cannot_displace_incumbent(self):
        unsafe = dict(self.parent_audit, total_return=100., raw_rule_breach_days=1)
        record = dict(track="official_ex_post", candidate_id="y0000",
                      params=self.study["candidates"][0]["params"], audit=unsafe)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "selection.json").write_text(json.dumps(self._selection()))
            verify._verify_selection(root, self.study, [record], self.parent)
            selection = self._selection("y0000", record["params"], 100.)
            (root / "selection.json").write_text(json.dumps(selection))
            with self.assertRaisesRegex(ValueError, "winner differs"):
                verify._verify_selection(root, self.study, [record], self.parent)

    def test_official_pass_claim_is_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selection = self._selection()
            selection["official_compliance"] = "PASS"
            (root / "selection.json").write_text(json.dumps(selection))
            with self.assertRaisesRegex(ValueError, "official status"):
                verify._verify_selection(root, self.study, [], self.parent)


if __name__ == "__main__":
    unittest.main()
