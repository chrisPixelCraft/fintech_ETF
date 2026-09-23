"""Independently reconstruct the fixed-candidate monthly horizon comparison.

The screening receipts authenticate archived evidence; all new ledgers are
reconstructed from market inputs, including failed/disqualified episodes.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import verify_double_check_release as prior
from scripts.audit_double_check import audit_result
from src import double_check_tuning

DEFAULT_OUTPUT = ROOT / "outputs/monthly_horizon_20260923"
STUDIES = ("v2_double_check_fintuned", "v2_double_check_expansion",
           "v2_double_check_refinement", "v2_double_check_structural")
SCREEN_FILES = {"config.json", "equity.csv", "metrics.json", "independent_audit.json"}
RUN_FILES = {t + ".csv" for t in prior.TABLES} | {"config.json", "metrics.json", "independent_audit.json"}
require, read, sha, same = prior.require, prior.read, prior.sha, prior.same


def independent_months(equity, initial):
    """Compute month-end ratios with the preceding month's ending wealth."""
    require(math.isfinite(initial) and initial > 0, "Invalid initial capital")
    dates = list(equity.date.astype(str))
    require(bool(dates) and dates == sorted(set(dates)), "Unordered or duplicate NAV dates")
    groups = {}
    for date, value in zip(dates, equity.economic_nav):
        require(math.isfinite(value) and value > 0, "Invalid economic NAV")
        groups.setdefault(date[:7], []).append(float(value))
    previous, rows = float(initial), []
    for month, values in groups.items():
        peak, drawdown = previous, 0.
        for value in values:
            peak = max(peak, value)
            drawdown = max(drawdown, 1 - value / peak)
        rows.append(dict(month=month, sessions=len(values), return_net=values[-1] / previous - 1,
                         start_nav=previous, end_nav=values[-1], max_drawdown=drawdown))
        previous = values[-1]
    require(math.isclose(math.prod(1 + r["return_net"] for r in rows), previous / initial,
                         rel_tol=1e-12), "Monthly compounding differs")
    return rows


def independent_ranking(candidate_id, equity, initial):
    rows = [r for r in independent_months(equity, initial) if r["month"].startswith("2025-")]
    require([r["month"] for r in rows] == [f"2025-{m:02}" for m in range(1, 13)],
            "Missing 2025 selection month")
    turnover = equity.loc[equity.date.astype(str).str.startswith("2025-"), "turnover"].tolist()
    require(all(math.isfinite(x) and x >= 0 for x in turnover), "Invalid 2025 turnover")
    return dict(candidate_id=candidate_id, median_2025=statistics.median(r["return_net"] for r in rows),
                worst_2025=min(r["return_net"] for r in rows), turnover_2025=sum(turnover))


def independent_windows(calendar):
    sessions = sorted({str(d)[:10] for d in calendar if "2025-01-01" <= str(d)[:10] <= "2026-09-21"})
    require(len(sessions) == 417 and sessions[-1] == "2026-09-21", "Frozen calendar differs")
    windows = [dict(episode="full", kind="continuous", start=sessions[0], end=sessions[-1])]
    for year, months in ((2025, range(1, 13)), (2026, range(1, 9))):
        for month in months:
            prefix = f"{year}-{month:02}"
            start = next(d for d in sessions if d.startswith(prefix))
            start_index = sessions.index(start)
            require(start_index + 24 < len(sessions), "Partial reset window")
            windows.append(dict(episode="m25_" + prefix, kind="reset_25_sessions", start=start,
                                end=sessions[start_index + 24]))
    windows.append(dict(episode="contest_2025", kind="reset_calendar_analogue",
                        start="2025-10-26", end="2025-11-27"))
    return windows


def verify_screening(root):
    candidates = read(root / "candidates.json")
    require(isinstance(candidates, list) and len(candidates) == 48, "Screening must contain 48 candidates")
    require(len({r["candidate_id"] for r in candidates}) == 48, "Duplicate screening ID")
    expected, audits = {}, {}
    for study, count in zip(STUDIES, (4, 40, 4, 0)):
        source = ROOT / "outputs" / study
        audit = read(source / "audit.json")
        require(audit.get("status") == "PASS" and audit.get("submission_status") == "BLOCK_SUBMISSION",
                "Untrusted parent study")
        require(sha(source / "trials.csv") == audit["artifact_hashes"]["trials.csv"],
                "Parent trials table changed")
        audits["outputs/" + study] = audit
        frame = pd.read_csv(source / "trials.csv", float_precision="round_trip")
        entries = [row for row in frame.to_dict("records") if row["track"] == "official_ex_post"
                   and prior.measured_eligible(dict(row, independent_audit="PASS"))]
        require(len(entries) == count, "Parent eligibility count differs")
        for row in entries:
            require(row["candidate_id"] not in expected, "Duplicate parent ID")
            expected[row["candidate_id"]] = "outputs/" + study
    require({r["candidate_id"] for r in candidates} == set(expected), "Screening omits/substitutes a candidate")
    ranking, signatures = [], set()
    for entry in candidates:
        cid = entry["candidate_id"]
        require(entry["source_study"] == expected[cid], "Candidate source study differs")
        require(entry["source_receipt_key"] == "official_ex_post/" + cid, "Candidate receipt key differs")
        folder = root / "screening" / cid
        require(sha(folder / "receipt.json") == audits[entry["source_study"]]["trial_receipt_sha256"][entry["source_receipt_key"]],
                "Screening receipt not pinned by source audit")
        receipt = read(folder / "receipt.json")
        require(SCREEN_FILES <= set(receipt), "Incomplete original receipt")
        for name in SCREEN_FILES:
            require(sha(folder / name) == receipt[name], "Screening artifact changed: " + cid + "/" + name)
        cfg = read(folder / "config.json")
        require(cfg["tuning_candidate_id"] == cid and cfg["universe_mode"] == "official_ex_post",
                "Screening config identity differs")
        same(entry["params"], cfg["full_tuning_params"], "Screening parameters")
        signature = json.dumps(entry["params"], sort_keys=True, allow_nan=False)
        require(signature not in signatures, "Duplicate effective candidate")
        signatures.add(signature)
        audit = read(folder / "independent_audit.json")
        require(prior.measured_eligible(audit), "Unqualified screening candidate")
        equity = pd.read_csv(folder / "equity.csv", float_precision="round_trip")
        require(len(equity) == 417 and equity.date.iloc[0] == "2025-01-02"
                and equity.date.iloc[-1] == "2026-09-21", "Screening calendar incomplete")
        ranking.append(independent_ranking(cid, equity, cfg["initial_cash"]))
    ranking.sort(key=lambda r: (-r["median_2025"], -r["worst_2025"], r["turnover_2025"], r["candidate_id"]))
    return candidates, ranking


def verify_run(folder, context, trial, window):
    prior.verify_receipt(folder, RUN_FILES)
    result = prior.load_result(folder)
    cfg = double_check_tuning.config_for(context, trial)
    cfg.update(start=window["start"], end=window["end"])
    same(result["config"], cfg, "Frozen episode config")
    rebuilt = audit_result(result, context)
    same(read(folder / "independent_audit.json"), rebuilt, "Episode independent audit")
    require(rebuilt["official_compliance"] == "UNKNOWN_BLOCK_SUBMISSION", "False formal certification")
    return result, rebuilt, prior.measured_eligible(rebuilt)


def verify_csv(path, expected, keys):
    actual = pd.read_csv(path, keep_default_na=False, float_precision="round_trip").to_dict("records")
    order = lambda row: tuple(row[key] for key in keys)
    require(len(actual) == len(expected), "CSV row count differs: " + str(path))
    require(len({order(r) for r in actual}) == len(actual), "Duplicate CSV identity: " + str(path))
    for row, wanted in zip(sorted(actual, key=order), sorted(expected, key=order)):
        prior.compare_csv_row(row, wanted, str(path))


def independent_summary(records, monthly, ids):
    candidates, paired = [], []
    for track in prior.TRACKS:
        for cid in ids:
            episodes = [r for r in records if r["track"] == track and r["candidate_id"] == cid]
            full = next(r for r in episodes if r["episode"] == "full")
            months = [r for r in monthly if r["track"] == track and r["candidate_id"] == cid]
            complete = [r["return_net"] for r in months if not r["partial_period"]]
            require(len(complete) == 20 and len(months) == 21, "Full month coverage differs")
            reset = [r for r in episodes if r["kind"] == "reset_25_sessions"]
            valid = [r["economic_total_return"] for r in reset if r["research_eligible"]]
            nav_2025 = next(r["end_nav"] for r in months if r["month"] == "2025-12")
            candidates.append(dict(track=track, candidate_id=cid,
                full_research_eligible=full["research_eligible"],
                book_total_return=full["total_return"], economic_total_return=full["economic_total_return"],
                annual_2025=nav_2025 / 1e9 - 1,
                ytd_2026=months[-1]["end_nav"] / nav_2025 - 1,
                complete_months=len(complete), median_month=statistics.median(complete),
                worst_month=min(complete), negative_months=sum(v < 0 for v in complete),
                reset_count=len(reset), reset_eligible=len(valid),
                eligible_reset_median=statistics.median(valid) if valid else None,
                eligible_reset_worst=min(valid) if valid else None,
                eligible_reset_negative=sum(v < 0 for v in valid),
                max_daily_volume_participation=full["max_daily_volume_participation"]))
        if len(ids) == 2:
            selected = next(cid for cid in ids if cid != "x0352")
            by_id = {cid: {r["episode"]: r for r in records if r["track"] == track
                          and r["candidate_id"] == cid and r["kind"] == "reset_25_sessions"} for cid in ids}
            base, other = by_id["x0352"], by_id[selected]
            require(set(base) == set(other) and len(base) == 20, "Dropped comparison episodes")
            common = sorted(k for k in base if base[k]["research_eligible"] and other[k]["research_eligible"])
            differences = [other[k]["economic_total_return"] - base[k]["economic_total_return"] for k in common]
            paired.append(dict(track=track, candidate_id=selected, paired_eligible=len(common),
                selected_wins=sum(d > 1e-12 for d in differences), ties=sum(abs(d) <= 1e-12 for d in differences),
                mean_difference=statistics.mean(differences) if differences else None, episodes=common))
    return dict(scope="EX_POST_DEVELOPMENT_NOT_UNSEEN_TEST", submission_status="BLOCK_SUBMISSION",
                candidates=candidates, paired_resets=paired)


def verify(root=DEFAULT_OUTPUT):
    root = Path(root)
    manifest = read(root / "manifest.json")
    require(manifest.get("completed") is True and not (root / "failure.json").exists(),
            "Study unfinished or contains engineering failure")
    prior.verify_hashes(ROOT, manifest["input_sha256"])
    candidates, ranking = verify_screening(root)
    verify_csv(root / "ranking.csv", ranking, ("candidate_id",))
    selection = read(root / "selection.json")
    require(selection["baseline"] == "x0352" and selection["candidate_id"] == ranking[0]["candidate_id"],
            "Monthly winner differs")
    trials = {r["candidate_id"]: r for r in candidates}
    same(selection["params"], trials[selection["candidate_id"]]["params"], "Selected parameters")
    require(selection["submission_status"] == "BLOCK_SUBMISSION", "False submission certification")
    require(selection["scope"] == manifest["study"]["scope"] == "EX_POST_ELIGIBILITY_AND_DEVELOPMENT_NOT_UNSEEN_TEST",
            "False unseen-test scope")
    require(manifest["study"]["selection_rule"] == "2025_ECONOMIC_MONTH_MEDIAN_WORST_TURNOVER_ID",
            "Selection objective differs")
    prior.load_contexts()
    windows = independent_windows(prior.CONTEXTS["official_ex_post"]["daily"].date)
    same(manifest["study"]["windows"], windows, "Frozen windows")
    require(manifest["study"]["candidate_count"] == 48, "Candidate count differs")
    ids = sorted({"x0352", selection["candidate_id"]})
    expected_count = len(ids) * len(prior.TRACKS) * len(windows)
    require(manifest["expected_runs"] == manifest["completed_runs"] == expected_count, "Run count differs")
    expected_receipts = {f"{track}/{cid}/{window['episode']}/receipt.json" for track in prior.TRACKS
                         for cid in ids for window in windows}
    actual_receipts = {str(path.relative_to(root / "runs")) for path in (root / "runs").rglob("receipt.json")}
    require(actual_receipts == expected_receipts, "Missing or unexpected run receipt")
    records, monthly = [], []
    for track in prior.TRACKS:
        context = prior.CONTEXTS[track]
        same(independent_windows(context["daily"].date), windows, "Track calendar differs")
        for cid in ids:
            trial = dict(candidate_id=cid, params=trials[cid]["params"])
            for window in windows:
                folder = root / "runs" / track / cid / window["episode"]
                result, rebuilt, eligible = verify_run(folder, context, trial, window)
                records.append(dict(track=track, candidate_id=cid, **window, research_eligible=eligible, **rebuilt))
                if window["episode"] == "full":
                    monthly.extend(dict(track=track, candidate_id=cid, **r, partial_period=r["month"] == "2026-09",
                                        research_eligible=eligible)
                                   for r in independent_months(result["equity"], result["config"]["initial_cash"]))
    verify_csv(root / "episodes.csv", records, ("track", "candidate_id", "episode"))
    verify_csv(root / "monthly.csv", monthly, ("track", "candidate_id", "month"))
    same(read(root / "summary.json"), independent_summary(records, monthly, ids), "Independent summaries")
    history = pd.read_csv(root / "history_monthly.csv", keep_default_na=False)
    require(history.month.tolist() == [f"{y}-{m:02}" for y in range(2010, 2025) for m in range(1, 13)],
            "Historical month coverage differs")
    require(history.return_net.eq("").all(), "Fabricated historical return")
    require(history.status.eq("UNVERIFIABLE").all(), "False historical certification")
    for row in history.itertuples(index=False):
        year, month = map(int, row.month.split("-"))
        expected_reason = ("NO_DAILY_OR_INTRADAY_OR_PIT_UNIVERSE" if year < 2024 else
                           "NO_PIT_UNIVERSE_AND_NO_INTRADAY" if month < 10 else
                           "NO_MONTH_START_KNOWN_PIT_UNIVERSE")
        require(row.reason == expected_reason, "Historical missing-data explanation differs")
    require(len(records) == expected_count, "Missing failed or successful episode")
    artifacts = {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob("*"))
                 if path.is_file() and path.name != "audit.json"}
    return dict(status="PASS", verified_candidates=len(candidates), verified_runs=len(records),
                selected_candidate=selection["candidate_id"], history_months=len(history),
                submission_status="BLOCK_SUBMISSION", scope="EX_POST_DEVELOPMENT_NOT_UNSEEN_TEST",
                verifier_sha256=sha(Path(__file__)),
                test_sha256=sha(ROOT / "tests/test_monthly_study.py"),
                input_sha256=manifest["input_sha256"], artifact_sha256=artifacts)


def check_saved_audit(root, result, write=False):
    path = Path(root) / "audit.json"
    if path.exists():
        same(read(path), result, "Saved monthly release audit")
    elif write:
        path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write", action="store_true", help="Write the independently reconstructed audit.json")
    args = parser.parse_args()
    result = verify(args.output)
    check_saved_audit(args.output, result, write=args.write)
    print(json.dumps({key: result[key] for key in ("status", "verified_candidates", "verified_runs",
                     "selected_candidate", "history_months", "submission_status")}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
