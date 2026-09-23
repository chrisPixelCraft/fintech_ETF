"""Regression tests for release binding and non-submittable daily outputs."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import v2_double_check_fintuned as entry
import v2_offcial_best_deep_tuning as old_entry
from daily_auto import double_check, full_tuned


def fake_prepare_signals(**kwargs):
    return build_config("official_ex_post")  # Rebound by the isolated adapter.


class DoubleCheckDailyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.study = self.root / "outputs/v2_double_check_fintuned"
        self.study.mkdir(parents=True)
        old_config = json.loads((entry.ROOT / "outputs/full_tuned_v2/official_ex_post/final/full_tuned_v2/config.json").read_text())
        self.selection = dict(candidate_id="dTEST", params=old_config["full_tuning_params"])
        self.config = copy.deepcopy(old_config)
        self.config["strategy_id"] = "full_tuned_v2_double_check_dTEST"
        for relative in entry.REQUIRED_INPUTS:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((entry.ROOT / relative).read_bytes())
        (self.study / "selection.json").write_text(json.dumps(self.selection))
        for track in entry.TRACKS:
            target = self.study / track / "final" / entry.RELEASE / "config.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({**self.config, "universe_mode": track}))
        self.audit = dict(status="PASS", input_hashes={
            path: entry._sha(self.root / path) for path in entry.REQUIRED_INPUTS}, artifact_hashes={})
        self._save_audit()
        self.addCleanup(patch.stopall)
        patch.object(entry, "ROOT", self.root).start()
        patch.object(entry, "STUDY", self.study).start()

    def _save_audit(self):
        artifacts = ["selection.json"] + [f"{track}/final/{entry.RELEASE}/config.json" for track in entry.TRACKS]
        self.audit["artifact_hashes"] = {path: entry._sha(self.study / path) for path in artifacts}
        (self.study / "audit.json").write_text(json.dumps(self.audit))

    def test_selected_double_check_config_and_no_legacy_loader(self):
        with patch.object(old_entry, "build_config", side_effect=AssertionError("old selection accessed")):
            self.assertEqual(entry.build_config()["strategy_id"], "full_tuned_v2_double_check_dTEST")
            loaded, config, _, _ = double_check._load_fixed_strategy()
            self.assertIs(loaded, entry)
            self.assertEqual(config["full_tuning_params"], self.selection["params"])

    def test_signal_function_binds_new_config_without_global_mutation(self):
        original_loader = old_entry.build_config
        with patch.object(old_entry, "prepare_signals", fake_prepare_signals):
            result = entry.prepare_signals()
        self.assertEqual(result["strategy_id"], self.config["strategy_id"])
        self.assertIs(old_entry.build_config, original_loader)

    def test_default_plan_has_no_export_even_when_research_audit_passes(self):
        output = self.root / "should_not_exist"
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture), patch.object(full_tuned, "main", side_effect=AssertionError("legacy CLI")):
            status = entry.main(["plan", "--output", str(output)])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(capture.getvalue())["code"], "OFFICIAL_ELIGIBILITY_UNRESOLVED")
        self.assertFalse(output.exists())

    def test_tampered_source_blocks_release(self):
        (self.root / "daily_auto/strategy_declaration.md").write_text("changed declaration")
        with self.assertRaisesRegex(ValueError, "Frozen release file changed"):
            entry.verify_release()

    def test_tampered_config_blocks_release(self):
        path = self.study / "official_ex_post/final" / entry.RELEASE / "config.json"
        path.write_text(json.dumps({**self.config, "target_count": 30}))
        with self.assertRaisesRegex(ValueError, "Frozen release file changed"):
            entry.build_config()

    def test_rehashed_wrong_strategy_cannot_select_old_release(self):
        path = self.study / "official_ex_post/final" / entry.RELEASE / "config.json"
        path.write_text(json.dumps({**self.config, "strategy_id": "full_tuned_v2_f0019"}))
        self._save_audit()
        with self.assertRaisesRegex(ValueError, "double-check strategy"):
            entry.build_config()

    def test_rehashed_parameter_disagreement_blocks(self):
        path = self.study / "selection.json"
        selected = copy.deepcopy(self.selection)
        selected["params"]["target_count"] = 21
        path.write_text(json.dumps(selected))
        self._save_audit()
        with self.assertRaisesRegex(ValueError, "selected parameters"):
            entry.build_config()

    def test_incomplete_or_traversing_manifest_blocks(self):
        self.audit["input_hashes"].pop("daily_auto/operations.py")
        self._save_audit()
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            entry.verify_release()
        self.audit["input_hashes"]["daily_auto/operations.py"] = entry._sha(self.root / "daily_auto/operations.py")
        self.audit["input_hashes"]["../outside.txt"] = "0" * 64
        self._save_audit()
        with self.assertRaisesRegex(ValueError, "escapes root"):
            entry.verify_release()

    def test_draft_is_wrapped_and_never_has_ingestible_dplan_name(self):
        packet = full_tuned.GeneratedPacket(
            filename="D-Plan_TEAM_2026-10-27.json", plan={"team_id": "TEAM", "orders": []},
            state={}, signals=pd.DataFrame(), signal_metadata={}, config=self.config,
            preflight={"status": "BLOCK", "blocking_codes": ["ACTIVE_SHARE_UNKNOWN"]},
            receipt={"code_files": {}, "input_files": {}, "strategy_id": self.config["strategy_id"]},
            code_paths=(), input_paths=())
        destination = self.root / "research-draft"
        receipt = double_check._write_research_draft(packet, destination)
        self.assertEqual(receipt["submission_status"], "BLOCK_SUBMISSION")
        self.assertEqual(receipt["mode"], "RESEARCH_DRAFT_NEVER_SUBMIT")
        self.assertEqual(list(destination.glob("D-Plan*")), [])
        draft = json.loads(next(destination.glob("RESEARCH-DRAFT*" )).read_text())
        self.assertNotIn("orders", draft)
        self.assertEqual(draft["candidate_plan"], packet.plan)
        with self.assertRaises(FileExistsError):
            double_check._write_research_draft(packet, destination)

    def test_replay_audits_before_writing_and_preserves_daily_compliance(self):
        target = self.root / "replay"
        result = {name: pd.DataFrame({"date": ["2026-09-21"]}) for name in
                  ("plan_audit", "compliance_daily", "rejected_trades")}
        result["metrics"] = {"official_compliance": "UNKNOWN_BLOCK_SUBMISSION"}
        from src import double_check_tuning, tuning_a_deep
        with patch.object(tuning_a_deep, "context", return_value={}), \
                patch.object(double_check_tuning, "run_model", return_value=result), \
                patch("src.backtest.save_result") as save, \
                patch("scripts.audit_double_check.audit_result", side_effect=AssertionError("bad ledger")):
            with self.assertRaisesRegex(AssertionError, "bad ledger"):
                entry.run_backtest("official_ex_post", target)
            save.assert_not_called()
            self.assertFalse(target.exists())
        with patch.object(tuning_a_deep, "context", return_value={}), \
                patch.object(double_check_tuning, "run_model", return_value=result), \
                patch("src.backtest.save_result", side_effect=lambda result, folder: folder.mkdir()), \
                patch("scripts.audit_double_check.audit_result", return_value={"independent_audit": "PASS"}), \
                contextlib.redirect_stdout(io.StringIO()):
            entry.run_backtest("official_ex_post", target)
        self.assertTrue((target / "compliance_daily.csv").is_file())
        self.assertTrue((target / "rejected_trades.csv").is_file())
        self.assertEqual(json.loads((target / "independent_audit.json").read_text()), {"independent_audit": "PASS"})


if __name__ == "__main__":
    unittest.main()
