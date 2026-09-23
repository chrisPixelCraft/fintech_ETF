"""Independently verify the bounded double-check refinement and freeze its release.

The earlier audited release is the incumbent. This verifier reconstructs every
new trial from its saved ledger; neither a producer summary nor a receipt alone
can establish research eligibility. Formal policy submission remains blocked.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import itertools
import json
import multiprocessing as mp
from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import verify_double_check_release as prior
from scripts import verify_double_check_expansion as parent_release
from scripts.audit_double_check import audit_result
from scripts.check_double_check_prefix import choose_prefix_window
from src import double_check_tuning
from src.tuning_a_deep import context
from tests.test_double_check_causality import verify_selected_config

STUDY_PATH = ROOT / "config/v2_double_check_refinement.json"
DEFAULT_OUTPUT = ROOT / "outputs/v2_double_check_refinement"
PRIOR_OUTPUT = ROOT / "outputs/v2_double_check_expansion"
FOUNDATION_OUTPUT = ROOT / "outputs/v2_double_check_fintuned"
PARENT_RELEASE = "expansion_selected"
RELEASE = "refinement_selected"
TRACKS = prior.TRACKS
TABLES = prior.TABLES
FINAL_FILES = prior.FINAL_FILES
TRIAL_FILES = prior.TRIAL_FILES
SUMMARY_FILES = {"manifest.json", "trials.csv", "grid.json", "selection.json",
                 "study_audit.json", "prefix_audit.json", "comparison.csv", "monthly.csv"}


def read(path):
    return json.loads(Path(path).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _prior_evidence(study):
    old_audit = parent_release.verify_release(PRIOR_OUTPUT)
    require(prior.sha(PRIOR_OUTPUT / "audit.json") == study["parent_audit_sha256"],
            "Prior release audit differs from the refinement's frozen baseline")
    require(old_audit["selected_candidate"] == study["parent_candidate_id"] == "x0352",
            "Refinement parent differs from independently audited prior winner")
    selected = read(PRIOR_OUTPUT / "selection.json")
    old_manifest = read(PRIOR_OUTPUT / "manifest.json")
    foundation_manifest = read(FOUNDATION_OUTPUT / "manifest.json")
    foundation_grid = read(FOUNDATION_OUTPUT / "local_grid.json")
    old_trials = (foundation_manifest["study"]["candidates"] +
                  foundation_grid["candidates"] + old_manifest["study"]["candidates"])
    by_signature = {prior.signature(trial["params"]): trial for trial in old_trials}
    require(len(old_trials) == len(by_signature) == 1409, "Prior candidate coverage differs")
    require(selected["candidate_id"] == "x0352" and selected["params"] ==
            next(t["params"] for t in old_trials if t["candidate_id"] == "x0352"),
            "Prior parent parameters differ")
    for track in TRACKS:
        folder = PRIOR_OUTPUT / track / "final" / PARENT_RELEASE
        prior.verify_receipt(folder, FINAL_FILES)
        require(prior.measured_eligible(read(folder / "independent_audit.json")),
                "Prior winner is not independently research eligible on both tracks")
    return selected, by_signature, old_audit


def validate_grid(study, prior_selected, old_by_signature):
    """Reconstruct every tuple and its old/new effective-parameter identity."""
    axes = study["axes"]
    require(isinstance(axes, dict) and len(axes) == 4 and all(
        isinstance(values, list) and values for values in axes.values()),
        "Refinement needs four declared, nonempty axes")
    require(axes == {"momentum_weight": [0.5, 0.55, 0.6],
                     "long_return_fraction": [0.15, 0.2, 0.25],
                     "volume_low": [0.75, 0.8, 0.85],
                     "volume_high": [1.5, 2.0, 2.5]},
            "Refinement axes differ from frozen four-axis protocol")
    combinations = itertools.product(*axes.values())
    rows = study["grid"]
    require(len(rows) == 81, "Refinement grid must contain 81 positions")
    candidates = study["candidates"]
    require(len(candidates) == 80, "Refinement must contain 80 new effective candidates")
    require(study.get("raw_grid_combinations") == study.get("effective_grid_combinations") == 81
            and study.get("reused_parent_count") == 1
            and study.get("new_candidate_count") == 80
            and study.get("candidate_scope") == "DEVELOPMENT_ONLY_NO_UNSEEN_HOLDOUT"
            and study.get("official_compliance") == "UNKNOWN_BLOCK_SUBMISSION",
            "Refinement study misstates coverage or research scope")
    candidate_by_id = {t["candidate_id"]: t for t in candidates}
    require(len(candidate_by_id) == len(candidates), "Duplicate new candidate ID")
    expected_new, reused = {}, 0
    seen = set()
    for index, (values, row) in enumerate(zip(combinations, rows)):
        wanted_values = dict(zip(axes, values))
        require(row["grid_index"] == index, "Refinement grid index/order differs")
        prior.same(row["values"], wanted_values, "Refinement grid values")
        params = dict(prior_selected["params"])
        for axis, value in wanted_values.items():
            if axis in prior.PAIR_KEYS:
                params.update(zip(prior.PAIR_KEYS[axis], value))
            else:
                require(axis in params, "Unexpected refinement parameter axis: " + axis)
                params[axis] = value
        from src.official_deep_tuning import validate_params
        validate_params(params)
        signature = prior.signature(params)
        require(signature not in seen, "Duplicate effective refinement tuple")
        seen.add(signature)
        older = old_by_signature.get(signature)
        if older is not None:
            reused += 1
            require(row["reused_prior"] is True and row.get("source") == "parent" and
                    row["candidate_id"] == older["candidate_id"],
                    "Old candidate mapping differs")
        else:
            candidate_id = f"y{len(expected_new):04d}"
            require(row["reused_prior"] is False and row.get("source") == "new" and
                    row["candidate_id"] == candidate_id,
                    "New candidate mapping differs")
            trial = candidate_by_id.get(candidate_id)
            require(trial is not None and trial["grid_index"] == index,
                    "New candidate/grid index differs")
            prior.same(trial["params"], params, "New candidate parameters")
            require(trial.get("phase") == "exhaustive_four_axis_grid"
                    and trial.get("varied") == "joint4", "Unexpected trial phase")
            expected_new[candidate_id] = trial
    require(reused == 1 and len(expected_new) == 80 and
            set(expected_new) == set(candidate_by_id),
            "Refinement deduplication/coverage differs")
    return candidates


def _new_record(task):
    return prior.verify_trial(task)


def _incumbent_record(old_selected):
    folder = PRIOR_OUTPUT / "official_ex_post/final" / PARENT_RELEASE
    audit = read(folder / "independent_audit.json")
    return dict(track="official_ex_post", candidate_id="x0352",
                params=old_selected["params"], audit=audit)


def _verify_selection(root, study, records, old_selected):
    selection = read(root / "selection.json")
    winner = prior.winner(records + [_incumbent_record(old_selected)], "official_ex_post")
    require(selection.get("candidate_id") == winner["candidate_id"],
            "Refinement winner differs from independently audited rank")
    prior.same(selection.get("params"), winner["params"], "Selected parameters")
    require(selection.get("selected_on") == "official_ex_post"
            and selection.get("scope") == "EX_POST_DEVELOPMENT_ONLY"
            and selection.get("formal_submission") == "BLOCK_IF_UNKNOWN",
            "Selection claims an unsupported scope or official status")
    source = "prior" if winner["candidate_id"] == "x0352" else "new"
    require(selection.get("source") == source, "Selected candidate source differs")
    for key in ("total_return", "max_drawdown", "turnover_two_way"):
        prior.same(selection.get(key), winner["audit"][key], "Selected audited score " + key)
    incumbent = _incumbent_record(old_selected)
    require(selection.get("incumbent_candidate_id") == "x0352"
            and selection.get("official_compliance") == "UNKNOWN_BLOCK_SUBMISSION"
            and selection.get("submission_status") == "BLOCK_SUBMISSION",
            "Selection omits incumbent or misstates official status")
    prior.same(selection.get("incumbent_total_return"), incumbent["audit"]["total_return"],
               "Frozen incumbent total return")
    return selection, winner


def _verify_final(root, selection, records):
    final, results, replay_contexts = {}, {}, {}
    source = selection["source"]
    candidate_id = selection["candidate_id"]
    trial = dict(candidate_id=candidate_id, params=selection["params"])
    for track in TRACKS:
        folder = root / track / "final" / RELEASE
        prior.verify_receipt(folder, FINAL_FILES)
        result = prior.load_result(folder)
        prior.same(result["config"], double_check_tuning.config_for(prior.CONTEXTS[track], trial),
                   "Selected final configuration")
        require(result["config"]["full_tuning_params"] == selection["params"],
                "Selected final parameters differ")
        expected_folder = ((PRIOR_OUTPUT / track / "final" / PARENT_RELEASE) if source == "prior"
                           else (root / track / "trials" / candidate_id))
        for name in TABLES:
            saved = pd.read_csv(expected_folder / (name + ".csv"), keep_default_na=False,
                                float_precision="round_trip")
            try:
                pd.testing.assert_frame_equal(result[name].fillna("").reset_index(drop=True), saved,
                                              check_dtype=False, rtol=1e-12, atol=1e-5)
            except AssertionError as exc:
                raise ValueError("Final replay differs from selected trial: "
                                 + track + "/" + name) from exc
        rebuilt = audit_result(result, prior.CONTEXTS[track])
        prior.same(read(folder / "independent_audit.json"), rebuilt,
                   "Saved final independent audit")
        if source == "prior":
            expected = read(expected_folder / "independent_audit.json")
        else:
            expected = next(r["audit"] for r in records
                            if r["track"] == track and r["candidate_id"] == candidate_id)
        prior.same(rebuilt, expected, "Selected final/prior accounting")
        replay_context = context(track)
        replay = double_check_tuning.run_model(replay_context, result["config"])
        prior.same(audit_result(replay, replay_context), rebuilt,
                   "Fresh selected replay accounting")
        for name in TABLES:
            saved = pd.read_csv(folder / (name + ".csv"), keep_default_na=False,
                                float_precision="round_trip")
            try:
                pd.testing.assert_frame_equal(replay[name].fillna("").reset_index(drop=True), saved,
                                              check_dtype=False, rtol=1e-12, atol=1e-5)
            except AssertionError as exc:
                raise ValueError("Fresh selected replay differs: " + track + "/" + name) from exc
        signal_digest = hashlib.sha256(replay["signals"].to_csv(index=False).encode("utf-8")).hexdigest()
        require(signal_digest == prior.sha(folder / "signals.csv"),
                "Fresh selected replay differs: " + track + "/signals")
        final[track] = rebuilt
        results[track] = result
        replay_contexts[track] = replay_context
    return final, results, replay_contexts


def _verify_reported_tables(root, results):
    comparison, monthly = [], []
    for track in TRACKS:
        result = results[track]
        comparison.append(dict(track=track, model=RELEASE, **result["metrics"]))
        parent = read(PRIOR_OUTPUT / track / "final" / PARENT_RELEASE / "metrics.json")
        comparison.append(dict(track=track, model="parent_x0352", **parent))
        monthly.extend(dict(track=track, model=RELEASE, **row)
                       for row in prior.monthly_rows(result["equity"], result["config"]["initial_cash"]))
    prior.verify_csv_rows(root / "comparison.csv", comparison)
    prior.verify_csv_rows(root / "monthly.csv", monthly)


def _verify_prefix(root, selection, replay_contexts):
    evidence = {}
    for track in TRACKS:
        folder = root / track / "final" / RELEASE
        cfg_path = folder / "config.json"
        cfg = read(cfg_path)
        ctx = replay_contexts[track]
        window = choose_prefix_window(cfg, ctx["daily"], ctx["universe"])
        result = verify_selected_config(cfg, ctx["daily"], ctx["universe"], ctx["bars"], **window)
        result.update(candidate_id=selection["candidate_id"],
                      config_sha256=prior.sha(cfg_path))
        require(result["status"] == "PASS" and result["pre_cutoff_fills"] > 0
                and result["hyperparameters_unchanged"] is True
                and result["official_compliance"] == "UNKNOWN_BLOCK_SUBMISSION",
                "Selected prefix check failed")
        evidence[track] = result
    path = root / "prefix_audit.json"
    if path.exists():
        prior.same(read(path), evidence, "Saved prefix evidence")
    else:
        temporary = path.with_suffix(".candidate.json")
        with temporary.open("x") as stream:
            json.dump(evidence, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.rename(path)
    return evidence


def build_audit(root, workers):
    require(workers > 0, "Worker count must be positive")
    require(not (root / "audit.json").exists(), "Preserve completed refinement audit")
    manifest = read(root / "manifest.json")
    require(manifest.get("outputs_complete") is True and not (root / "failure.json").exists(),
            "Refinement study incomplete or failed")
    prior.verify_hashes(ROOT, manifest["input_hashes"],
                        {"config/v2_double_check_refinement.json", "scripts/refine_double_check.py"})
    study = read(STUDY_PATH)
    require(manifest.get("study_sha256") == prior.sha(STUDY_PATH),
            "Manifest/study source differs")
    prior.same(manifest.get("study"), study, "Manifest refinement design")
    require(tuple(study["tracks"]) == TRACKS and study["selection_track"] == TRACKS[0],
            "Refinement tracks or selection pool differ")
    old_selected, old_by_signature, old_audit = _prior_evidence(study)
    prior.same(read(root / "grid.json"), dict(
        axes=study["axes"], grid=study["grid"], raw_grid_combinations=81,
        effective_grid_combinations=81, reused_parent_count=1,
        new_candidate_count=80, parent_candidate_id="x0352"), "Saved refinement grid")
    candidates = validate_grid(study, old_selected, old_by_signature)
    expected = {(track, trial["candidate_id"]) for track in TRACKS for trial in candidates}
    require(len(expected) == manifest.get("completed_trials") == manifest.get("expected_trials") == 160,
            "Incomplete refinement trial count")
    for track in TRACKS:
        actual = {p.name for p in (root / track / "trials").iterdir() if p.is_dir()}
        require(actual == {t["candidate_id"] for t in candidates},
                "Missing or unexpected refinement trial directories")
    tasks = [(root, track, trial) for track in TRACKS for trial in candidates]
    records = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork"),
                             initializer=prior.load_contexts) as pool:
        for record in pool.map(_new_record, tasks, chunksize=1):
            records.append(record)
            if len(records) % 50 == 0:
                print(f"independently reconstructed {len(records)}/{len(tasks)}", flush=True)
    prior.verify_summary(root / "trials.csv", records)
    selection, _ = _verify_selection(root, study, records, old_selected)
    prior.load_contexts()
    require(manifest.get("selected_candidate") == selection["candidate_id"],
            "Manifest winner differs")
    final, results, replay_contexts = _verify_final(root, selection, records)
    _verify_reported_tables(root, results)
    _verify_prefix(root, selection, replay_contexts)
    study_audit = read(root / "study_audit.json")
    require(study_audit.get("status") == "PASS" and study_audit.get("verified_trials") == len(records)
            and study_audit.get("official_compliance") == "UNKNOWN_BLOCK_SUBMISSION"
            and study_audit.get("submission_status") == "BLOCK_SUBMISSION"
            and study_audit.get("selected_candidate") == selection["candidate_id"]
            and study_audit.get("grid_raw") == 81
            and study_audit.get("new_effective") == 80
            and study_audit.get("parent_reused") == 1
            and study_audit.get("auditor_sha256") == prior.sha(ROOT / "scripts/audit_double_check.py"),
            "Producer study audit scope/count differs")
    prior.same(study_audit["final"], final, "Producer/independent final audits")
    prior.verify_hashes(ROOT, manifest["input_hashes"])
    inputs = set(manifest["input_hashes"]) | {
        "scripts/verify_double_check_refinement.py", "scripts/verify_double_check_expansion.py",
        "scripts/verify_double_check_release.py",
        "scripts/report_double_check_refinement.py", "tests/test_double_check_refinement_verify.py",
        "v2_double_check_refinement.py",
        "scripts/check_double_check_prefix.py", "tests/test_double_check_causality.py",
        "outputs/v2_double_check_expansion/audit.json",
        "outputs/v2_double_check_fintuned/audit.json",
        "reports/v2_double_check_refinement_report.md",
        "docs/v2_double_check_refinement_protocol.md",
    }
    for pattern in ("reports/v2_double_check_refinement*", "docs/v2_double_check_refinement*",
                    "v2_double_check_refinement*.py"):
        inputs.update(str(path.relative_to(ROOT)) for path in ROOT.glob(pattern) if path.is_file())
    input_hashes = {name: prior.sha(ROOT / name) for name in sorted(inputs)}
    artifacts = {path.name: prior.sha(path) for path in root.iterdir()
                 if path.is_file() and path.suffix in (".csv", ".json")
                 and path.name not in ("audit.json", "failure.json", "status.json")}
    for track in TRACKS:
        folder = root / track / "final" / RELEASE
        artifacts.update({str(path.relative_to(root)): prior.sha(path)
                          for path in folder.iterdir() if path.is_file()})
    audit = dict(status="PASS", scope="INDEPENDENT_RESEARCH_ACCOUNTING_AND_BOUNDED_REFINEMENT_ONLY",
                 official_compliance="UNKNOWN_BLOCK_SUBMISSION", submission_status="BLOCK_SUBMISSION",
                 selected_candidate=selection["candidate_id"], selected_source=selection["source"],
                 verified_trials=len(records), raw_grid_combinations=81,
                 new_effective_candidates=80, reused_prior_candidates=1,
                 audited_at=datetime.now(timezone.utc).isoformat(),
                 auditor_sha256=prior.sha(Path(__file__)), input_hashes=input_hashes,
                 artifact_hashes=dict(sorted(artifacts.items())),
                 trial_receipt_sha256={r["track"] + "/" + r["candidate_id"]: r["receipt_sha256"]
                                       for r in records}, final=final,
                 prior_release_sha256=prior.sha(PRIOR_OUTPUT / "audit.json"))
    temporary = root / ".audit.candidate.json"
    with temporary.open("x") as stream:
        json.dump(audit, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    temporary.rename(root / "audit.json")
    status = dict(status="TRUSTED_RESEARCH_ONLY_BLOCK_SUBMISSION",
                  submission_status="BLOCK_SUBMISSION", completed=len(records), expected=len(records),
                  audit_sha256=prior.sha(root / "audit.json"))
    temporary = root / ".status.candidate.json"
    with temporary.open("x") as stream:
        json.dump(status, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    temporary.replace(root / "status.json")
    return audit


def verify_release(root):
    audit = read(root / "audit.json")
    require(audit.get("status") == "PASS" and audit.get("submission_status") == "BLOCK_SUBMISSION"
            and audit.get("official_compliance") == "UNKNOWN_BLOCK_SUBMISSION"
            and audit.get("scope") == "INDEPENDENT_RESEARCH_ACCOUNTING_AND_BOUNDED_REFINEMENT_ONLY"
            and audit.get("verified_trials") == 160
            and audit.get("raw_grid_combinations") == 81
            and audit.get("new_effective_candidates") == 80
            and audit.get("reused_prior_candidates") == 1,
            "Refinement release status/scope differs")
    require(audit.get("auditor_sha256") == prior.sha(Path(__file__)),
            "Refinement release verifier source changed")
    prior.verify_hashes(ROOT, audit["input_hashes"],
                        {"scripts/verify_double_check_refinement.py", "config/v2_double_check_refinement.json",
                         "scripts/refine_double_check.py", "scripts/report_double_check_refinement.py",
                         "tests/test_double_check_refinement_verify.py",
                         "v2_double_check_refinement.py",
                         "reports/v2_double_check_refinement_report.md",
                         "docs/v2_double_check_refinement_protocol.md",
                         "outputs/v2_double_check_expansion/audit.json"})
    required = SUMMARY_FILES | {f"{track}/final/{RELEASE}/{name}" for track in TRACKS
                                for name in FINAL_FILES | {"receipt.json"}}
    prior.verify_hashes(root, audit["artifact_hashes"], required)
    manifest = read(root / "manifest.json")
    require(manifest.get("outputs_complete") is True
            and manifest.get("completed_trials") == manifest.get("expected_trials") == 160,
            "Refinement manifest incomplete")
    require(all(audit["input_hashes"].get(path) == digest
                for path, digest in manifest["input_hashes"].items()),
            "Refinement release omits frozen study input")
    study = read(STUDY_PATH)
    require(manifest["study_sha256"] == prior.sha(STUDY_PATH)
            and audit["prior_release_sha256"] == prior.sha(PRIOR_OUTPUT / "audit.json")
            and read(root / "grid.json")["grid"] == study["grid"],
            "Refinement design or prior baseline changed")
    old = parent_release.verify_release(PRIOR_OUTPUT)
    require(old["selected_candidate"] == study["parent_candidate_id"] == "x0352",
            "Prior audited parent changed")
    require(audit.get("selected_candidate") == read(root / "selection.json")["candidate_id"]
            and audit.get("selected_source") == read(root / "selection.json")["source"],
            "Refinement release winner differs")
    status = read(root / "status.json")
    require(status.get("status") == "TRUSTED_RESEARCH_ONLY_BLOCK_SUBMISSION"
            and status.get("submission_status") == "BLOCK_SUBMISSION"
            and status.get("completed") == status.get("expected") == 160
            and status.get("audit_sha256") == prior.sha(root / "audit.json"),
            "Refinement release status or audit hash differs")
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--build-audit", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        audit = (build_audit(args.output.resolve(), args.workers) if args.build_audit
                 else verify_release(args.output.resolve()))
    except (AssertionError, ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps(dict(status="BLOCK", submission_status="BLOCK_SUBMISSION", error=str(exc))))
        return 2
    print(json.dumps(dict(research_audit=audit["status"], verified_trials=audit["verified_trials"],
                          selected_candidate=audit["selected_candidate"],
                          submission_status="BLOCK_SUBMISSION")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
