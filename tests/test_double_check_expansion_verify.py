"""Independent gates for the bounded expansion's coverage and selection."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts import verify_double_check_expansion as verify


class ExpansionVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.study = verify.read(verify.STUDY_PATH)
        cls.parent, cls.old_candidates, _ = verify._prior_evidence(cls.study)

    def test_frozen_grid_maps_all_positions_and_rejects_substitution(self):
        trials = verify.validate_grid(self.study, self.parent, self.old_candidates)
        self.assertEqual(len(trials), 573)
        changed = copy.deepcopy(self.study)
        changed["grid"][0]["candidate_id"] = "d0034"
        with self.assertRaisesRegex(ValueError, "mapping differs"):
            verify.validate_grid(changed, self.parent, self.old_candidates)
        changed = copy.deepcopy(self.study)
        changed["candidates"][0]["params"]["momentum_weight"] = .95
        with self.assertRaisesRegex(ValueError, "candidate parameters"):
            verify.validate_grid(changed, self.parent, self.old_candidates)

    def test_high_return_with_one_breach_cannot_displace_audited_parent(self):
        parent_audit = verify.read(verify.PRIOR_OUTPUT /
            "official_ex_post/final/v2_double_check_fintuned/independent_audit.json")
        unsafe = dict(parent_audit, total_return=100., raw_rule_breach_days=1)
        new = dict(track="official_ex_post", candidate_id="x0000",
                   params=self.study["candidates"][0]["params"], audit=unsafe)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selection = dict(candidate_id="d0034", params=self.parent["params"],
                             source="prior", selected_on="official_ex_post",
                             scope="EX_POST_DEVELOPMENT_ONLY", formal_submission="BLOCK_IF_UNKNOWN",
                             official_compliance="UNKNOWN_BLOCK_SUBMISSION",
                             submission_status="BLOCK_SUBMISSION",
                             incumbent_candidate_id="d0034",
                             incumbent_total_return=parent_audit["total_return"],
                             total_return=parent_audit["total_return"],
                             max_drawdown=parent_audit["max_drawdown"],
                             turnover_two_way=parent_audit["turnover_two_way"])
            (root / "selection.json").write_text(json.dumps(selection))
            verify._verify_selection(root, self.study, [new], self.parent)
            selection.update(candidate_id="x0000", params=new["params"], source="new",
                             total_return=100.)
            (root / "selection.json").write_text(json.dumps(selection))
            with self.assertRaisesRegex(ValueError, "winner differs"):
                verify._verify_selection(root, self.study, [new], self.parent)

    def test_official_pass_cannot_be_claimed_for_research_selection(self):
        parent_audit = verify.read(verify.PRIOR_OUTPUT /
            "official_ex_post/final/v2_double_check_fintuned/independent_audit.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selection = dict(candidate_id="d0034", params=self.parent["params"],
                             source="prior", selected_on="official_ex_post",
                             scope="EX_POST_DEVELOPMENT_ONLY", formal_submission="BLOCK_IF_UNKNOWN",
                             official_compliance="PASS", submission_status="BLOCK_SUBMISSION",
                             incumbent_candidate_id="d0034",
                             incumbent_total_return=parent_audit["total_return"],
                             total_return=parent_audit["total_return"],
                             max_drawdown=parent_audit["max_drawdown"],
                             turnover_two_way=parent_audit["turnover_two_way"])
            (root / "selection.json").write_text(json.dumps(selection))
            with self.assertRaisesRegex(ValueError, "official status"):
                verify._verify_selection(root, self.study, [], self.parent)


if __name__ == "__main__":
    unittest.main()
