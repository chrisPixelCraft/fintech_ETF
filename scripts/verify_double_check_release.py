"""Independently reconstruct saved trials, then freeze a compact research release.

--build-audit needs all local trials. Normal verification needs only the compact
tracked release. Neither operation establishes official policy eligibility.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
import multiprocessing as mp
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
import v2_double_check_fintuned as entry
from scripts.audit_double_check import audit_result

RELEASE = "v2_double_check_fintuned"
TRACKS = ("official_ex_post", "historical_pit")
TABLES = ("equity", "orders", "trades", "holdings", "warnings", "snapshots",
          "plan_audit", "compliance_daily", "rejected_trades")
COMMON_FILES = {name + ".csv" for name in TABLES} | {"config.json", "metrics.json", "independent_audit.json"}
FINAL_FILES = COMMON_FILES | {"signals.csv"}
TRIAL_FILES = COMMON_FILES | {"monthly.csv"}
SUMMARY_FILES = {"manifest.json", "selection.json", "study_audit.json", "trials.csv", "global.csv",
                 "local_grid.json", "comparison.csv", "monthly.csv", "prefix_audit.json"}
ZERO = ("measured_hard_breach_days", "no_valid_plan_days", "unfilled_orders",
        "simulated_warning_days", "stale_held_price_days", "hold_without_envelope_days",
        "execution_price_bound_breaches", "raw_rule_breach_days")
SCORES = ("total_return", "max_drawdown", "turnover_two_way")
PAIR_KEYS = {"return_pair": ("return_short", "return_long"),
             "ema_pair": ("ema_fast", "ema_slow"),
             "macd_tuple": ("macd_fast", "macd_slow", "macd_signal")}
CONTEXTS = {}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_hashes(root, hashes, required=()):
    require(isinstance(hashes, dict) and bool(hashes) and set(required) <= set(hashes),
            "Incomplete hash manifest")
    for relative, expected in hashes.items():
        path = (root / relative).resolve()
        require(not Path(relative).is_absolute() and root.resolve() in path.parents,
                "Unsafe manifest path: " + relative)
        require(isinstance(expected, str) and len(expected) == 64 and sha(path) == expected,
                "Hash mismatch: " + relative)


def verify_receipt(folder, required):
    require(not (folder / "failure.json").exists(), "Failed result cannot be trusted")
    receipt = read(folder / "receipt.json")
    require(set(receipt) == set(required), "Incomplete result receipt")
    verify_hashes(folder, receipt, required)
    return receipt


def same(actual, expected, label):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), label + ": keys differ")
        for key in expected:
            same(actual[key], expected[key], label + "." + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + ": length differs")
        for index, value in enumerate(expected):
            same(actual[index], value, label + ":" + str(index))
    elif isinstance(expected, bool) or expected is None:
        require(actual is expected, label + ": boolean/null differs")
    elif isinstance(expected, (int, float)):
        require(isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and math.isfinite(actual) and math.isfinite(expected)
                and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-7), label + ": number differs")
    else:
        require(actual == expected, label + ": value differs")


def measured_eligible(audit):
    """Independent selection predicate over reconstructed accounting only."""
    finite = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
    return (audit.get("independent_audit") == "PASS" and audit.get("complete_period") is True
            and audit.get("disqualified") is False
            and all(finite(audit.get(k)) and audit[k] == 0 for k in ZERO)
            and all(finite(audit.get(k)) for k in SCORES)
            and audit.get("official_compliance") == "UNKNOWN_BLOCK_SUBMISSION")


def winner(records, track):
    candidates = [r for r in records if r["track"] == track and measured_eligible(r["audit"])]
    require(bool(candidates), "No independently eligible research winner")
    return min(candidates, key=lambda r: (-r["audit"]["total_return"], r["audit"]["max_drawdown"],
                                         r["audit"]["turnover_two_way"], r["candidate_id"]))


def signature(params):
    effective = dict(params)
    if effective["max_replacements_per_day"] == 0:
        effective["replacement_margin"] = 0.
    m, f = effective.pop("momentum_weight"), effective.pop("long_return_fraction")
    effective["score_weights"] = [round(x, 12) for x in
                                  (m * (1-f), m*f, (1-m)*.4, (1-m)*.2, (1-m)*.2, (1-m)*.2)]
    return json.dumps(effective, sort_keys=True, separators=(",", ":"))


def validate_grid(study, grid, parent):
    """Rebuild the declared Cartesian product independently of its producer."""
    require(grid["parent"] == parent["candidate_id"], "Wrong local-grid parent")
    params = parent["params"]
    axes = study["local_policy"]["axes"]
    require(len(axes) == 7 and len(set(axes)) == 7, "Local grid must have seven unique axes")
    choices = {}
    for axis in axes:
        values = study["spaces"][axis]
        value = [params[k] for k in PAIR_KEYS[axis]] if axis in PAIR_KEYS else params[axis]
        require(value in values and len(values) >= 2, "Grid parent outside declared search space")
        index = values.index(value)
        choices[axis] = [value, values[index+1] if index+1 < len(values) else values[index-1]]
    same(grid["choices"], choices, "Local choices")
    all_trials = study["candidates"] + grid["candidates"]
    ids = {t["candidate_id"]: t for t in all_trials}
    require(len(ids) == len(all_trials), "Duplicate candidate IDs")
    signatures = {signature(t["params"]): t["candidate_id"] for t in all_trials}
    require(len(signatures) == len(all_trials), "Duplicate effective candidate parameters")
    require(grid["raw_combinations"] == len(grid["coverage"]) == 128, "Incomplete local coverage")
    used = set()
    for values, row in zip(itertools.product(*choices.values()), grid["coverage"]):
        same(row["values"], dict(zip(axes, values)), "Local coverage tuple")
        expected = dict(params)
        for axis, value in zip(axes, values):
            if axis in PAIR_KEYS:
                expected.update(zip(PAIR_KEYS[axis], value))
            else:
                expected[axis] = value
        require(row["candidate_id"] in ids and signature(ids[row["candidate_id"]]["params"]) == signature(expected),
                "Local tuple references different effective parameters")
        used.add(row["candidate_id"])
    require({t["candidate_id"] for t in grid["candidates"]} <= used, "Uncovered extra local candidate")
    return all_trials


def load_contexts():
    base = read(ROOT / "config/strategy_v2.json")
    for track in TRACKS:
        if track == "official_ex_post":
            directory = ROOT / "data/tuning_2nd/official_universe/processed"
            daily, universe = directory / "daily.csv", directory / "universe.csv"
        else:
            daily = ROOT / "data/v2/market_daily.csv"
            universe = ROOT / "data/extended/processed/universe_20241231.csv"
        CONTEXTS[track] = dict(daily=pd.read_csv(daily), universe=pd.read_csv(universe), base=base, track=track)


def load_result(folder):
    result = {name: pd.read_csv(folder / (name + ".csv"), keep_default_na=False,
                                float_precision="round_trip") for name in TABLES}
    result.update(config=read(folder / "config.json"), metrics=read(folder / "metrics.json"))
    return result


def monthly_rows(equity, initial):
    previous = {"book": initial, "economic": initial}
    rows = []
    for month, group in equity.groupby(pd.to_datetime(equity.date).dt.to_period("M")):
        row = dict(month=str(month), sessions=len(group), partial_period=str(month) == "2026-09")
        for label, column in (("book", "nav"), ("economic", "economic_nav")):
            values = [float(previous[label]), *group[column].astype(float).tolist()]
            peak, worst = values[0], 0.
            for value in values:
                peak = max(peak, value)
                worst = max(worst, 1 - value / peak)
            row.update({label + "_return": values[-1] / values[0] - 1,
                        label + "_max_drawdown": worst, "ending_" + label + "_nav": values[-1]})
            previous[label] = values[-1]
        rows.append(row)
    return rows


def verify_csv_rows(path, expected):
    actual = pd.read_csv(path, keep_default_na=False, float_precision="round_trip").to_dict("records")
    require(len(actual) == len(expected), "CSV row count differs: " + str(path))
    for row, wanted in zip(actual, expected):
        compare_csv_row(row, wanted, str(path))


def compare_csv_row(actual, expected, label):
    require(set(actual) == set(expected), label + ": columns differ")
    for key, value in expected.items():
        got = actual[key]
        if isinstance(value, bool) and got in ("True", "False"):
            got = got == "True"
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and isinstance(got, str):
            try:
                got = float(got)
            except ValueError:
                pass
        same(got, "" if value is None else value, label + "." + key)


def verify_trial(task):
    root, track, trial = task
    folder = root / track / "trials" / trial["candidate_id"]
    receipt = verify_receipt(folder, TRIAL_FILES)
    result = load_result(folder)
    row = result["metrics"]
    require(row.get("status") == "COMPLETE" and row.get("track") == track
            and row.get("candidate_id") == trial["candidate_id"] and row.get("phase") == trial["phase"],
            "Trial result identity/status differs")
    for key, value in trial["params"].items():
        same(row.get(key), value, "Trial parameter " + key)
    from src.double_check_tuning import config_for
    same(result["config"], config_for(CONTEXTS[track], trial), "Reconstructed trial config")
    rebuilt = audit_result(result, CONTEXTS[track])
    verify_csv_rows(folder / "monthly.csv", monthly_rows(result["equity"], result["config"]["initial_cash"]))
    same(read(folder / "independent_audit.json"), rebuilt, "Saved independent audit")
    for key in ZERO + SCORES + ("complete_period", "disqualified", "official_compliance"):
        same(row.get(key), rebuilt[key], "Required audited metric " + key)
    eligibility = measured_eligible(rebuilt)
    require(row.get("eligible") is eligibility and row.get("research_eligible") is eligibility,
            "Research eligibility differs from independent reconstruction")
    return dict(track=track, candidate_id=trial["candidate_id"], params=trial["params"],
                audit=rebuilt, row=row, receipt_sha256=sha(folder / "receipt.json"))


def verify_summary(path, records):
    frame = pd.read_csv(path, keep_default_na=False, float_precision="round_trip")
    require(len(frame) == len(records) and not frame.duplicated(["track", "candidate_id"]).any(),
            "Incomplete/duplicate summary: " + str(path))
    index = {(r["track"], r["candidate_id"]): r["row"] for r in records}
    require(set(zip(frame.track, frame.candidate_id)) == set(index), "Unexpected summary trial IDs")
    for row in frame.to_dict("records"):
        expected = index[(row["track"], row["candidate_id"])]
        compare_csv_row(row, expected, "Summary")


def verify_selection(selection, selected):
    require(selection.get("candidate_id") == selected["candidate_id"], "Wrong selected candidate")
    same(selection.get("params"), selected["params"], "Selected parameters")
    require(selection.get("selected_on") == "official_ex_post"
            and selection.get("scope") == "EX_POST_DEVELOPMENT_ONLY"
            and selection.get("formal_submission") == "BLOCK_IF_UNKNOWN"
            and selection.get("global_exhaustive") is False
            and selection.get("local_grid_exhaustive") is True, "False selection scope/eligibility")


def verify_prefix(root):
    path = root / "prefix_audit.json"
    require(path.is_file(), "Prefix evidence missing")
    evidence = read(path)
    require(isinstance(evidence, dict) and set(evidence) == set(TRACKS), "Prefix evidence must cover both tracks")
    for track in TRACKS:
        result = evidence[track]
        require(isinstance(result, dict) and result.get("status") == "PASS"
                and result.get("hyperparameters_unchanged") is True,
                "Prefix audit failed or parameters changed: " + track)
        count = result.get("pre_cutoff_fills")
        require(isinstance(count, int) and not isinstance(count, bool) and count > 0,
                "Prefix evidence has no actual pre-cutoff fills: " + track)
        require(result.get("official_compliance") == "UNKNOWN_BLOCK_SUBMISSION",
                "Prefix audit falsely implies official approval: " + track)
    selection = read(root / "selection.json")
    for track, result in evidence.items():
        config = root / track / "final" / RELEASE / "config.json"
        require(result.get("candidate_id") == selection.get("candidate_id")
                and result.get("config_sha256") == sha(config),
                "Prefix audit is not bound to selected config: " + track)
    return evidence


def verify_final(root, track, selected, records):
    folder = root / track / "final" / RELEASE
    verify_receipt(folder, FINAL_FILES)
    result = load_result(folder)
    source = root / track / "trials" / selected["candidate_id"]
    same(result["config"], read(source / "config.json"), "Final selected config")
    same(result["config"]["full_tuning_params"], selected["params"], "Cross-track parameters")
    for name in TABLES:
        require(sha(folder / (name + ".csv")) == sha(source / (name + ".csv")), "Final replay differs: " + name)
    rebuilt = audit_result(result, CONTEXTS[track])
    same(read(folder / "independent_audit.json"), rebuilt, "Final independent audit")
    record = next(r for r in records if r["track"] == track and r["candidate_id"] == selected["candidate_id"])
    same(rebuilt, record["audit"], "Final/trial accounting")
    return rebuilt


def verify_comparison(root, records):
    """Bind reported controls to frozen evidence and new rows to saved ledgers."""
    old_root = ROOT / "outputs/full_tuned_v2"
    old_audit = read(old_root / "audit.json")
    require(old_audit.get("status") == "PASS", "Legacy controls have no frozen audit")
    source_names = {"comparison.csv"} | {
        f"{track}/final/{model}/equity.csv" for track in TRACKS
        for model in ("full_tuned_v2", "0050", "v1_matched")}
    verify_hashes(old_root, {name: old_audit["artifact_hashes"][name] for name in source_names}, source_names)
    expected = []
    for track in TRACKS:
        expected.append(dict(track=track, model=RELEASE,
                             **read(root / track / "final" / RELEASE / "metrics.json")))
    old_rows = pd.read_csv(old_root / "comparison.csv", keep_default_na=False,
                           float_precision="round_trip").to_dict("records")
    for row in old_rows:
        if row["model"] == "full_tuned_v2":
            row["model"] = "incumbent_f0019"
        expected.append(row)
    expected.extend(dict(record["row"], model="incumbent_replayed_new_ledger")
                    for record in records if record["candidate_id"] == "d0000")
    columns = set().union(*(row.keys() for row in expected))
    by_id = {(row["track"], row["model"]): {key: row.get(key, "") for key in columns} for row in expected}
    actual = pd.read_csv(root / "comparison.csv", keep_default_na=False,
                         float_precision="round_trip").to_dict("records")
    actual_by_id = {(row["track"], row["model"]): row for row in actual}
    require(len(actual_by_id) == len(actual) == len(expected) and set(actual_by_id) == set(by_id),
            "Comparison rows differ")
    for identity, row in actual_by_id.items():
        compare_csv_row(row, by_id[identity], "Comparison " + str(identity))
    return {str((old_root / name).relative_to(ROOT)) for name in source_names} | {
        "outputs/full_tuned_v2/audit.json"}


def build_audit(root, workers=4):
    require(workers >= 1, "workers must be positive")
    require(not (root / "audit.json").exists(), "Preserve existing release audit; verify it instead")
    manifest = read(root / "manifest.json")
    require(manifest.get("outputs_complete") is True, "Study not complete")
    require(not (root / "failure.json").exists(), "Study failure remains unresolved")
    verify_prefix(root)
    verify_hashes(ROOT, manifest["input_hashes"], {"scripts/run_double_check.py", "scripts/audit_double_check.py",
                                                  "config/v2_double_check_study.json"})
    study = manifest["study"]
    same(study, read(ROOT / "config/v2_double_check_study.json"), "Frozen study")
    require(tuple(study["tracks"]) == TRACKS and study["selection_track"] == TRACKS[0]
            and study["exhaustive_global"] is False, "Study selection/scope changed")
    grid = read(root / "local_grid.json")
    require(grid["global_sha256"] == sha(root / "global.csv"), "Local grid source summary changed")
    trials = study["candidates"] + grid["candidates"]
    expected = {(track, trial["candidate_id"]) for track in TRACKS for trial in trials}
    require(len(expected) == 2 * len(trials) == manifest.get("completed_trials") == manifest.get("expected_trials"),
            "Incomplete study counts")
    for track in TRACKS:
        actual = {p.name for p in (root / track / "trials").iterdir() if p.is_dir()}
        require(actual == {t["candidate_id"] for t in trials}, "Missing/unexpected trial directories")
    tasks = [(root, track, trial) for track in TRACKS for trial in trials]
    records = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork"), initializer=load_contexts) as pool:
        for record in pool.map(verify_trial, tasks, chunksize=1):
            records.append(record)
            if len(records) % 50 == 0:
                print(f"independently reconstructed {len(records)}/{len(tasks)}", flush=True)
    global_ids = {t["candidate_id"] for t in study["candidates"]}
    global_records = [r for r in records if r["candidate_id"] in global_ids]
    verify_summary(root / "global.csv", global_records)
    verify_summary(root / "trials.csv", records)
    parent = winner(global_records, TRACKS[0])
    validate_grid(study, grid, parent)
    selected = winner(records, TRACKS[0])
    verify_selection(read(root / "selection.json"), selected)
    load_contexts()
    final = {track: verify_final(root, track, selected, records) for track in TRACKS}
    months = []
    for track in TRACKS:
        result = load_result(root / track / "final" / RELEASE)
        months.extend(dict(track=track, model=RELEASE, **row)
                      for row in monthly_rows(result["equity"], result["config"]["initial_cash"]))
    verify_csv_rows(root / "monthly.csv", months)
    control_paths = verify_comparison(root, records)
    study_audit = read(root / "study_audit.json")
    require(study_audit.get("status") == "PASS" and study_audit.get("verified_trials") == len(tasks)
            and study_audit.get("auditor_sha256") == sha(ROOT / "scripts/audit_double_check.py")
            and study_audit.get("official_compliance") == "UNKNOWN_BLOCK_SUBMISSION"
            and study_audit.get("global_exhaustive") is False
            and study_audit.get("local_raw_combinations") == 128, "Invalid study audit scope/count")
    same(study_audit["final"], final, "Study final audits")
    verify_hashes(ROOT, manifest["input_hashes"])
    paths = set(manifest["input_hashes"]) | set(entry.REQUIRED_INPUTS) | control_paths
    paths.update(str(p.relative_to(ROOT)) for p in (ROOT / "daily_auto").glob("*.py"))
    paths.add("scripts/verify_double_check_release.py")
    paths.add("scripts/report_double_check.py")
    paths.add("tests/test_double_check_causality.py")
    paths.add("scripts/check_double_check_prefix.py")
    for pattern in ("reports/v2_double_check_fintuned*", "reports/v2_double_check_fintuned_assets/*",
                    "docs/v2_double_check*.md"):
        paths.update(str(p.relative_to(ROOT)) for p in ROOT.glob(pattern) if p.is_file())
    input_hashes = {name: sha(ROOT / name) for name in sorted(paths)}
    artifacts = {str(p.relative_to(root)): sha(p) for p in root.iterdir()
                 if p.is_file() and p.suffix in (".csv", ".json")
                 and p.name not in ("audit.json", "failure.json", "status.json")}
    for track in TRACKS:
        for path in (root / track / "final" / RELEASE).iterdir():
            if path.is_file():
                artifacts[str(path.relative_to(root))] = sha(path)
    receipts = {r["track"] + "/" + r["candidate_id"]: r["receipt_sha256"] for r in records}
    audit = dict(status="PASS", scope="INDEPENDENT_RESEARCH_ACCOUNTING_AND_BOUNDED_SEARCH_ONLY",
                 official_compliance="UNKNOWN_BLOCK_SUBMISSION", submission_status="BLOCK_SUBMISSION",
                 selected_candidate=selected["candidate_id"], verified_trials=len(records),
                 verified_sessions=sum(r["audit"]["sessions"] for r in records),
                 global_exhaustive=False, local_raw_combinations=128,
                 historical_pit_research_eligible=measured_eligible(final["historical_pit"]),
                 audited_at=datetime.now(timezone.utc).isoformat(),
                 auditor_sha256=sha(Path(__file__)), input_hashes=input_hashes,
                 artifact_hashes=dict(sorted(artifacts.items())),
                 trial_receipt_sha256=receipts, final=final)
    temporary = root / ".audit.candidate.json"
    with temporary.open("x") as stream:
        json.dump(audit, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    temporary.rename(root / "audit.json")
    # Status is advisory and excluded from audit hashes. Write it only after
    # the authoritative audit exists, so a failed verification cannot claim trust.
    status = dict(status="TRUSTED_RESEARCH_ONLY_BLOCK_SUBMISSION",
                  submission_status="BLOCK_SUBMISSION", completed=len(records), expected=len(records),
                  completed_at=audit["audited_at"], audit_sha256=sha(root / "audit.json"),
                  scope="Research accounting and bounded-search evidence only; no official approval.",
                  trust_condition="audit.json must exist and pass release verification; status alone is not evidence")
    status_temporary = root / ".status.candidate.json"
    with status_temporary.open("x") as stream:
        json.dump(status, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    status_temporary.replace(root / "status.json")
    return audit


def verify_release(root):
    audit = read(root / "audit.json")
    require(audit.get("status") == "PASS" and audit.get("submission_status") == "BLOCK_SUBMISSION"
            and audit.get("official_compliance") == "UNKNOWN_BLOCK_SUBMISSION", "Invalid release scope/status")
    require(audit.get("auditor_sha256") == sha(Path(__file__)), "Release verifier source changed")
    verify_hashes(ROOT, audit["input_hashes"], entry.REQUIRED_INPUTS | {
        "scripts/verify_double_check_release.py", "tests/test_double_check_causality.py",
        "scripts/check_double_check_prefix.py"})
    required = SUMMARY_FILES | {f"{track}/final/{RELEASE}/{name}" for track in TRACKS
                                for name in FINAL_FILES | {"receipt.json"}}
    verify_hashes(root, audit["artifact_hashes"], required)
    manifest = read(root / "manifest.json")
    require(manifest.get("outputs_complete") is True
            and manifest.get("completed_trials") == manifest.get("expected_trials") == audit.get("verified_trials"),
            "Incomplete compact release")
    require(all(audit["input_hashes"].get(path) == digest for path, digest in manifest["input_hashes"].items()),
            "Release omitted/changed study dependency")
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / RELEASE)
    parser.add_argument("--build-audit", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        audit = build_audit(args.output.resolve(), args.workers) if args.build_audit else verify_release(args.output.resolve())
    except (AssertionError, ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps(dict(status="BLOCK", submission_status="BLOCK_SUBMISSION", error=str(exc))))
        return 2
    print(json.dumps(dict(research_audit=audit["status"], verified_trials=audit["verified_trials"],
                          submission_status="BLOCK_SUBMISSION")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
