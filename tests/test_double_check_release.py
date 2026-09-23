"""Release verifier rejects omitted evidence, tampering and wrong selection."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd

from scripts import verify_double_check_release as verify
from scripts.prepare_double_check import design, local_grid


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def receipt(self):
        for name in verify.TRIAL_FILES:
            (self.root / name).write_text("{}\n" if name.endswith(".json") else "date\n")
        hashes = {name: verify.sha(self.root / name) for name in verify.TRIAL_FILES}
        (self.root / "receipt.json").write_text(json.dumps(hashes))
        return hashes

    def test_receipt_requires_metrics_and_all_tables(self):
        for missing in ("metrics.json", "holdings.csv", "independent_audit.json"):
            hashes = self.receipt()
            del hashes[missing]
            (self.root / "receipt.json").write_text(json.dumps(hashes))
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "Incomplete result receipt"):
                verify.verify_receipt(self.root, verify.TRIAL_FILES)

    def test_tampered_result_and_failed_result_cannot_pass(self):
        self.receipt()
        (self.root / "equity.csv").write_text("date,nav\n2026-09-21,999999999\n")
        with self.assertRaisesRegex(ValueError, "Hash mismatch"):
            verify.verify_receipt(self.root, verify.TRIAL_FILES)
        self.receipt()
        (self.root / "failure.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Failed result"):
            verify.verify_receipt(self.root, verify.TRIAL_FILES)

    def test_missing_count_unknown_and_nonfinite_never_eligible(self):
        audit = dict(independent_audit="PASS", complete_period=True, disqualified=False,
                     official_compliance="UNKNOWN_BLOCK_SUBMISSION", total_return=.5,
                     max_drawdown=.2, turnover_two_way=3., **{key: 0 for key in verify.ZERO})
        self.assertTrue(verify.measured_eligible(audit))
        for key in verify.ZERO:
            for bad in (None, False, "0", 1, float("nan")):
                self.assertFalse(verify.measured_eligible({**audit, key: bad}))
        self.assertFalse(verify.measured_eligible({**audit, "official_compliance": "PASS"}))

    def test_selection_is_recomputed_from_audited_metrics(self):
        audit = dict(independent_audit="PASS", complete_period=True, disqualified=False,
                     official_compliance="UNKNOWN_BLOCK_SUBMISSION", total_return=.5,
                     max_drawdown=.2, turnover_two_way=3., **{key: 0 for key in verify.ZERO})
        records = [dict(track="official_ex_post", candidate_id="a", params={"x": 1}, audit=audit),
                   dict(track="official_ex_post", candidate_id="b", params={"x": 2},
                        audit={**audit, "total_return": 100, "raw_rule_breach_days": 1})]
        selected = verify.winner(records, "official_ex_post")
        self.assertEqual(selected["candidate_id"], "a")
        selection = dict(candidate_id="b", params={"x": 2}, selected_on="official_ex_post",
                         scope="EX_POST_DEVELOPMENT_ONLY", formal_submission="BLOCK_IF_UNKNOWN",
                         global_exhaustive=False, local_grid_exhaustive=True)
        with self.assertRaisesRegex(ValueError, "Wrong selected"):
            verify.verify_selection(selection, selected)
        selection.update(candidate_id="a", params={"x": 1})
        verify.verify_selection(selection, selected)
        selection["params"]["x"] = 3
        with self.assertRaisesRegex(ValueError, "Selected parameters"):
            verify.verify_selection(selection, selected)

    def test_grid_rebuild_checks_all_128_tuples_and_parent(self):
        study = design()
        parent = study["candidates"][0]
        grid = local_grid(study, parent["params"])
        grid["parent"] = parent["candidate_id"]
        verify.validate_grid(study, grid, parent)
        broken = copy.deepcopy(grid)
        broken["coverage"].pop()
        with self.assertRaisesRegex(ValueError, "Incomplete local coverage"):
            verify.validate_grid(study, broken, parent)
        broken = copy.deepcopy(grid)
        broken["coverage"][0]["candidate_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "different effective"):
            verify.validate_grid(study, broken, parent)

    def test_build_requires_complete_before_any_worker(self):
        (self.root / "manifest.json").write_text(json.dumps({"outputs_complete": False}))
        with patch.object(verify, "ProcessPoolExecutor", side_effect=AssertionError("must not run")):
            with self.assertRaisesRegex(ValueError, "Study not complete"):
                verify.build_audit(self.root)
        self.assertFalse((self.root / "audit.json").exists())

    def test_missing_failed_or_empty_prefix_blocks_before_workers(self):
        (self.root / "manifest.json").write_text(json.dumps({"outputs_complete": True}))
        passing = dict(status="PASS", hyperparameters_unchanged=True, pre_cutoff_fills=1,
                       official_compliance="UNKNOWN_BLOCK_SUBMISSION")
        cases = [None, {verify.TRACKS[0]: passing},
                 {track: {**passing, "status": "FAIL"} for track in verify.TRACKS},
                 {track: {**passing, "hyperparameters_unchanged": False} for track in verify.TRACKS},
                 {track: {**passing, "pre_cutoff_fills": 0} for track in verify.TRACKS}]
        with patch.object(verify, "ProcessPoolExecutor", side_effect=AssertionError("must not run")), \
                patch.object(verify, "verify_hashes", side_effect=AssertionError("must not hash inputs")):
            for evidence in cases:
                if evidence is not None:
                    (self.root / "prefix_audit.json").write_text(json.dumps(evidence))
                with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError, "Prefix"):
                    verify.build_audit(self.root)
        self.assertFalse((self.root / "audit.json").exists())

    def test_prefix_binds_selected_candidate_and_exact_config(self):
        (self.root / "selection.json").write_text(json.dumps({"candidate_id": "selected"}))
        evidence = {}
        for track in verify.TRACKS:
            config = self.root / track / "final" / verify.RELEASE / "config.json"
            config.parent.mkdir(parents=True)
            config.write_text("{}")
            evidence[track] = dict(status="PASS", hyperparameters_unchanged=True,
                pre_cutoff_fills=1, official_compliance="UNKNOWN_BLOCK_SUBMISSION",
                candidate_id="selected", config_sha256=verify.sha(config))
        prefix = self.root / "prefix_audit.json"
        prefix.write_text(json.dumps(evidence))
        verify.verify_prefix(self.root)
        evidence[verify.TRACKS[0]]["candidate_id"] = "other"
        prefix.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(ValueError, "not bound"):
            verify.verify_prefix(self.root)
        evidence[verify.TRACKS[0]]["candidate_id"] = "selected"
        evidence[verify.TRACKS[0]]["config_sha256"] = "0" * 64
        prefix.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(ValueError, "not bound"):
            verify.verify_prefix(self.root)

    def test_comparison_checks_frozen_controls_and_replayed_rows(self):
        old = self.root / "outputs/full_tuned_v2"
        old.mkdir(parents=True)
        legacy = [dict(track=track, model="full_tuned_v2", total_return=.1, max_drawdown=.2)
                  for track in verify.TRACKS]
        pd.DataFrame(legacy).to_csv(old / "comparison.csv", index=False)
        hashes = {"comparison.csv": verify.sha(old / "comparison.csv")}
        for track in verify.TRACKS:
            for model in ("full_tuned_v2", "0050", "v1_matched"):
                name = f"{track}/final/{model}/equity.csv"
                path = old / name
                path.parent.mkdir(parents=True)
                path.write_text("date,nav\n2026-09-21,100\n")
                hashes[name] = verify.sha(path)
        (old / "audit.json").write_text(json.dumps(dict(status="PASS", artifact_hashes=hashes)))
        root = self.root / "new"
        records, rows = [], []
        for track in verify.TRACKS:
            folder = root / track / "final" / verify.RELEASE
            folder.mkdir(parents=True)
            metrics = dict(total_return=.3, max_drawdown=.2, complete_period=True)
            (folder / "metrics.json").write_text(json.dumps(metrics))
            rows.append(dict(track=track, model=verify.RELEASE, **metrics))
            row = dict(track=track, candidate_id="d0000", eligible=False, total_return=.1)
            records.append(dict(row=row, candidate_id="d0000"))
        rows += [{**row, "model": "incumbent_f0019"} for row in legacy]
        rows += [dict(record["row"], model="incumbent_replayed_new_ledger") for record in records]
        pd.DataFrame(rows).to_csv(root / "comparison.csv", index=False)
        with patch.object(verify, "ROOT", self.root):
            paths = verify.verify_comparison(root, records)
            self.assertIn("outputs/full_tuned_v2/comparison.csv", paths)
            rows[-1]["total_return"] = 9.
            pd.DataFrame(rows).to_csv(root / "comparison.csv", index=False)
            with self.assertRaisesRegex(ValueError, "Comparison"):
                verify.verify_comparison(root, records)

    def test_normal_verify_needs_no_middle_trials_but_requires_final_metrics(self):
        root = self.root / "release"
        root.mkdir()
        script = self.root / "scripts/verify_double_check_release.py"
        script.parent.mkdir()
        script.write_bytes(Path(verify.__file__).read_bytes())
        inputs = {"scripts/verify_double_check_release.py": verify.sha(script)}
        causality = self.root / "tests/test_double_check_causality.py"
        causality.parent.mkdir()
        causality.write_text("# fixture\n")
        inputs["tests/test_double_check_causality.py"] = verify.sha(causality)
        prefix_script = self.root / "scripts/check_double_check_prefix.py"
        prefix_script.write_text("# fixture\n")
        inputs["scripts/check_double_check_prefix.py"] = verify.sha(prefix_script)
        required = verify.SUMMARY_FILES | {f"{track}/final/{verify.RELEASE}/{name}"
                    for track in verify.TRACKS for name in verify.FINAL_FILES | {"receipt.json"}}
        for name in required:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
        (root / "manifest.json").write_text(json.dumps(dict(outputs_complete=True,
            completed_trials=2, expected_trials=2, input_hashes=inputs)))
        audit = dict(status="PASS", submission_status="BLOCK_SUBMISSION",
            official_compliance="UNKNOWN_BLOCK_SUBMISSION", verified_trials=2,
            auditor_sha256=verify.sha(Path(verify.__file__)), input_hashes=inputs,
            artifact_hashes={name: verify.sha(root / name) for name in required})
        (root / "audit.json").write_text(json.dumps(audit))
        with patch.object(verify, "ROOT", self.root), patch.object(verify.entry, "REQUIRED_INPUTS", frozenset()):
            verify.verify_release(root)
            self.assertFalse((root / "official_ex_post/trials").exists())
            del audit["artifact_hashes"][f"official_ex_post/final/{verify.RELEASE}/metrics.json"]
            (root / "audit.json").write_text(json.dumps(audit))
            with self.assertRaisesRegex(ValueError, "Incomplete hash"):
                verify.verify_release(root)

    def test_saved_ledger_is_reconstructed_even_if_tamper_is_rehashed(self):
        from src import double_check_ledger
        from tests.test_backtest_v2 import fixture, settings, constant_signals
        daily, universe = fixture()
        context = dict(daily=daily, universe=universe)
        result = double_check_ledger.run_v2(daily, universe, settings(), signal_transform=constant_signals)
        result["plan_audit"] = pd.DataFrame({"date": result["snapshots"].date,
                                             "final_reason": result["snapshots"].plan_reason})
        audit = verify.audit_result(result, context)
        trial = dict(candidate_id="fixture", params={"test_param": 1}, phase="unit_test")
        row = dict(result["metrics"], **trial["params"])
        row.update(audit)
        row.update(status="COMPLETE", track="official_ex_post", candidate_id="fixture", phase="unit_test",
                   eligible=verify.measured_eligible(audit), research_eligible=verify.measured_eligible(audit))
        folder = self.root / "official_ex_post/trials/fixture"
        folder.mkdir(parents=True)
        for name in verify.TABLES:
            result[name].to_csv(folder / (name + ".csv"), index=False)
        for name, value in (("config", result["config"]), ("metrics", row), ("independent_audit", audit)):
            (folder / (name + ".json")).write_text(json.dumps(value))
        pd.DataFrame(verify.monthly_rows(result["equity"], result["config"]["initial_cash"])).to_csv(
            folder / "monthly.csv", index=False)
        hashes = {name: verify.sha(folder / name) for name in verify.TRIAL_FILES}
        (folder / "receipt.json").write_text(json.dumps(hashes))
        with patch.dict(verify.CONTEXTS, {"official_ex_post": context}), \
                patch("src.double_check_tuning.config_for", return_value=result["config"]):
            rebuilt = verify.verify_trial((self.root, "official_ex_post", trial))
            self.assertEqual(rebuilt["audit"], audit)
            equity = pd.read_csv(folder / "equity.csv")
            equity.loc[0, "cash"] += 100
            equity.to_csv(folder / "equity.csv", index=False)
            hashes["equity.csv"] = verify.sha(folder / "equity.csv")
            (folder / "receipt.json").write_text(json.dumps(hashes))
            with self.assertRaisesRegex(AssertionError, "cash"):
                verify.verify_trial((self.root, "official_ex_post", trial))


if __name__ == "__main__":
    unittest.main()
