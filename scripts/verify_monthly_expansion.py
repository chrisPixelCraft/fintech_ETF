"""Independently verify the finite April-first monthly parameter search.

Every saved episode, including a disqualified three-day episode, is audited
against raw market inputs. A complete negative result is valid evidence, but
never a deployable strategy or an official policy certification.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
import statistics
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import verify_double_check_release as base
from scripts import verify_monthly_study as monthly
from src.official_deep_tuning import validate_params

DEFAULT_OUTPUT = ROOT / "outputs/monthly_expansion_20260923"
PARENT = ROOT / "outputs/monthly_horizon_20260923"
DESIGN = ROOT / "config/monthly_expansion.json"
ANCHORS = ("x0352", "x0454")
AXES = {"ema_slow": (75, 100, 150, 200), "volume_low": (.4, .6, .8),
        "cash_guard_ratio": (.10, .12, .14)}
SCREEN = "m25_2025-04"
TRAIN = tuple(f"m25_2025-{month:02}" for month in range(1, 12))
RULE = "ALL_11_STRICT_THEN_ECONOMIC_MEDIAN_WORST_MEAN_TURNOVER_ID"
SCOPE = "DEVELOPMENT_ONLY_KNOWN_FAILURE_TARGETED_NOT_UNSEEN_TEST"
require, read, sha, same = base.require, base.read, base.sha, base.same


def _prior_candidates():
    """Authenticate both anchors through the completed monthly study audit."""
    audit = read(PARENT / "audit.json")
    require(audit.get("status") == "PASS" and audit.get("selected_candidate") == "x0454"
            and audit.get("verified_candidates") == 48 and audit.get("verified_runs") == 88
            and audit.get("submission_status") == "BLOCK_SUBMISSION",
            "Prior monthly study differs from the independently audited source")
    require(audit.get("verifier_sha256") == sha(ROOT / "scripts/verify_monthly_study.py"),
            "Prior monthly verifier changed")
    require(audit.get("test_sha256") == sha(ROOT / "tests/test_monthly_study.py"),
            "Prior monthly verifier tests changed")
    base.verify_hashes(ROOT, audit["input_sha256"])
    base.verify_hashes(PARENT, audit["artifact_sha256"])
    old = read(PARENT / "candidates.json")
    by_id = {entry["candidate_id"]: entry for entry in old}
    require(len(old) == len(by_id) == 48 and set(ANCHORS) <= set(by_id),
            "Prior monthly anchors unavailable")
    require(read(PARENT / "selection.json")["candidate_id"] == "x0454",
            "Prior monthly selected anchor changed")
    return {anchor: by_id[anchor]["params"] for anchor in ANCHORS}


def verify_design(design, anchors, calendar):
    """Recreate all 72 tuples and the 25-session windows independently."""
    require(design.get("study_id") == "monthly_expansion_20260923"
            and design.get("selection_rule") == RULE
            and design.get("scope") == SCOPE
            and design.get("submission_status") == "BLOCK_SUBMISSION"
            and design.get("max_runs") == 825,
            "Monthly expansion design scope or bound differs")
    same(design.get("anchors"), anchors, "Audited anchor parameters")
    same(design.get("axes"), {key: list(values) for key, values in AXES.items()},
         "Declared legal parameter axes")
    windows = monthly.independent_windows(calendar)
    same(design.get("windows"), windows, "Independent frozen market windows")
    require(design.get("screen_episode") == SCREEN
            and design.get("training_episodes") == list(TRAIN),
            "April screen or January–November training contract differs")
    candidates = design.get("candidates")
    require(isinstance(candidates, list) and len(candidates) == 72,
            "Expansion must have 72 distinct candidate positions")
    index = 0
    signatures = set()
    for anchor in ANCHORS:
        for values in itertools.product(*AXES.values()):
            expected = dict(anchors[anchor])
            expected.update(zip(AXES, values))
            validate_params(expected)
            candidate = candidates[index]
            require(candidate.get("candidate_id") == f"mx{index:04}"
                    and candidate.get("anchor") == anchor,
                    "Candidate ordering, ID or audited anchor differs")
            same(candidate.get("params"), expected, "Grid parameters")
            signature = base.signature(expected)
            require(signature not in signatures, "Duplicate effective grid candidate")
            signatures.add(signature)
            index += 1
    return candidates, {window["episode"]: window for window in windows}


def _expected_episodes(candidates, windows, root):
    """Infer the adaptive path from independently audited April evidence."""
    records = []
    by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    april = {}
    official = base.CONTEXTS["official_ex_post"]
    for candidate in candidates:
        cid = candidate["candidate_id"]
        folder = root / "runs/official_ex_post" / cid / SCREEN
        _, independent, eligible = monthly.verify_run(folder, official, candidate, windows[SCREEN])
        row = dict(track="official_ex_post", candidate_id=cid, **windows[SCREEN],
                   research_eligible=eligible, **independent)
        records.append(row)
        april[cid] = eligible
    survivors = {cid for cid, eligible in april.items() if eligible}
    for candidate in candidates:
        if candidate["candidate_id"] not in survivors:
            continue
        cid = candidate["candidate_id"]
        for episode in TRAIN:
            if episode == SCREEN:
                continue
            _, independent, eligible = monthly.verify_run(
                root / "runs/official_ex_post" / cid / episode,
                official, candidate, windows[episode])
            records.append(dict(track="official_ex_post", candidate_id=cid,
                                **windows[episode], research_eligible=eligible, **independent))
    return records, survivors, by_id


def independent_ranking(records, candidates):
    """Only all-eleven strict, complete ledgers can enter the predeclared rank."""
    ids = [(r["track"], r["candidate_id"], r["episode"]) for r in records]
    require(len(ids) == len(set(ids)), "Duplicate monthly evaluation identity")
    rows = []
    for candidate in candidates:
        cid = candidate["candidate_id"]
        train = [r for r in records if r["track"] == "official_ex_post"
                 and r["candidate_id"] == cid and r["episode"] in TRAIN]
        require(len(train) in (1, 11), "Partial training evidence for a survivor")
        require(any(r["episode"] == SCREEN for r in train), "April screen missing")
        passed = sum(base.measured_eligible(r) for r in train)
        valid = len(train) == 11 and passed == 11
        values = [r["economic_total_return"] for r in train]
        require(all(isinstance(v, (int, float)) and math.isfinite(v) for v in values),
                "Nonfinite audited training return")
        rows.append(dict(candidate_id=cid, anchor=candidate["anchor"],
                         evaluated_train_windows=len(train), eligible_train_windows=passed,
                         training_eligible=valid,
                         median_return=statistics.median(values) if valid else None,
                         worst_return=min(values) if valid else None,
                         mean_turnover=statistics.mean(r["turnover_two_way"] for r in train)
                         if valid else None))
    qualified = [row for row in rows if row["training_eligible"]]
    selected = min(qualified, key=lambda row: (-row["median_return"], -row["worst_return"],
                                               row["mean_turnover"], row["candidate_id"])) if qualified else None
    return rows, selected["candidate_id"] if selected else None


def _verify_diagnostics(root, selected, trial, windows, records):
    if selected is None:
        return records, False
    for track in base.TRACKS:
        ctx = base.CONTEXTS[track]
        for window in windows.values():
            episode = window["episode"]
            if track == "official_ex_post" and episode in TRAIN:
                continue
            _, independent, eligible = monthly.verify_run(
                root / "runs" / track / selected / episode, ctx, trial, window)
            records.append(dict(track=track, candidate_id=selected, **window,
                                research_eligible=eligible, **independent))
    selected_records = [r for r in records if r["candidate_id"] == selected]
    require(len(selected_records) == 44, "Selected diagnostic window coverage differs")
    return records, all(base.measured_eligible(row) for row in selected_records)


def verify(root=DEFAULT_OUTPUT):
    root = Path(root)
    manifest = read(root / "manifest.json")
    require(manifest.get("completed") is True
            and not any(root.rglob("failure.json")),
            "Monthly expansion incomplete or contains an engineering failure")
    base.verify_hashes(ROOT, manifest["input_sha256"])
    require(manifest.get("design_sha256") == sha(DESIGN)
            and manifest.get("study_id") == "monthly_expansion_20260923"
            and manifest.get("candidate_count") == 72,
            "Manifest differs from frozen monthly design")
    anchors = _prior_candidates()
    base.load_contexts()
    design = read(DESIGN)
    candidates, windows = verify_design(design, anchors, base.CONTEXTS["official_ex_post"]["daily"].date)
    require(set(manifest["input_sha256"]) >= {
        "src/monthly_expansion.py", "scripts/run_monthly_expansion.py",
        "config/monthly_expansion.json", "docs/monthly_expansion_protocol.md",
        "docs/monthly_expansion_design.md", "outputs/monthly_horizon_20260923/audit.json"},
        "Manifest omits a load-bearing dependency")
    records, survivors, by_id = _expected_episodes(candidates, windows, root)
    ranks, selected = independent_ranking(records, candidates)
    monthly.verify_csv(root / "ranking.csv", ranks, ("candidate_id",))
    records, diagnostic_pass = _verify_diagnostics(root, selected, by_id.get(selected), windows, records)
    records.sort(key=lambda row: (row["track"], row["candidate_id"], row["episode"]))
    monthly.verify_csv(root / "episodes.csv", records, ("track", "candidate_id", "episode"))
    expected_ids = {f"{r['track']}/{r['candidate_id']}/{r['episode']}/receipt.json" for r in records}
    actual_ids = {str(path.relative_to(root / "runs")) for path in (root / "runs").rglob("receipt.json")}
    require(actual_ids == expected_ids, "Missing or unexpected monthly episode receipt")
    actual_dirs = {str(path.relative_to(root / "runs")) for path in (root / "runs").glob("*/*/*")
                   if path.is_dir()}
    expected_dirs = {path.removesuffix("/receipt.json") for path in expected_ids}
    require(actual_dirs == expected_dirs, "Unreceipted or unexpected monthly episode directory")
    expected = 72 + 10 * len(survivors) + (33 if selected else 0)
    require(len(records) == expected == manifest.get("expected_runs") == manifest.get("completed_runs")
            and expected <= design["max_runs"]
            and manifest.get("april_passers") == len(survivors)
            and manifest.get("selected_candidate") == selected,
            "Adaptive evaluation count or manifest outcome differs")
    selection = read(root / "selection.json")
    expected_selection = dict(
        status="SELECTED_ON_TRAINING" if selected else "NO_ELIGIBLE_WINNER",
        candidate_id=selected,
        params=by_id[selected]["params"] if selected else None,
        april_passers=len(survivors),
        training_eligible_count=sum(row["training_eligible"] for row in ranks),
        selection_rule=design["selection_rule"], training_episodes=list(TRAIN),
        scope=design["scope"], submission_status="BLOCK_SUBMISSION",
        all_tested_windows_eligible=diagnostic_pass if selected else False,
        adoption=("HOLD_OFFICIAL_EVIDENCE_AND_UNSEEN_DATA" if diagnostic_pass
                  else "FAILED_DIAGNOSTIC_NO_ADOPTION") if selected else "NO_ADOPTION")
    same(selection, expected_selection, "Independently reconstructed monthly decision")
    require(selected is not None or all(not r["training_eligible"] for r in ranks),
            "Negative result contradicts audited training evidence")
    source_hashes = dict(manifest["input_sha256"])
    source_hashes.update({name: sha(ROOT / name) for name in
                          ("scripts/verify_monthly_expansion.py", "tests/test_monthly_expansion.py")})
    artifacts = {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob("*"))
                 if path.is_file() and path.name != "audit.json"}
    return dict(status="PASS", verified_candidates=len(candidates), verified_runs=len(records),
                selected_candidate=selected, selection_status=selection["status"],
                april_passers=len(survivors), training_eligible_count=expected_selection["training_eligible_count"],
                diagnostic_eligible=diagnostic_pass,
                official_compliance="UNKNOWN_BLOCK_SUBMISSION", submission_status="BLOCK_SUBMISSION",
                scope=SCOPE, input_sha256=source_hashes, artifact_sha256=artifacts,
                verifier_sha256=sha(Path(__file__)),
                test_sha256=sha(ROOT / "tests/test_monthly_expansion.py"))


def check_saved_audit(root, result, write=False):
    path = Path(root) / "audit.json"
    if path.exists():
        same(read(path), result, "Saved monthly expansion audit")
    elif write:
        path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    else:
        raise ValueError("Monthly expansion audit.json has not been built")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write", action="store_true", help="Write a new complete independent audit")
    args = parser.parse_args(argv)
    result = verify(args.output)
    check_saved_audit(args.output, result, write=args.write)
    print(json.dumps({key: result[key] for key in
                      ("status", "verified_candidates", "verified_runs", "selected_candidate",
                       "selection_status", "april_passers", "submission_status")},
                     ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
