"""Audited fourth-round research replay; official policy submission is blocked."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
STUDY = ROOT / "outputs/v2_double_check_structural"
PARENT_STUDY = ROOT / "outputs/v2_double_check_refinement"
OLDER_STUDY = ROOT / "outputs/v2_double_check_expansion"
FOUNDATION_STUDY = ROOT / "outputs/v2_double_check_fintuned"
RELEASE = "structural_selected"
TRACKS = ("official_ex_post", "historical_pit")
LIVE_BLOCKERS = (
    "OFFICIAL_ACTIVE_SHARE_DEFINITION_UNRESOLVED",
    "OFFICIAL_STRATEGY_DECLARATION_ACCEPTANCE_UNVERIFIED",
    "OFFICIAL_ACCOUNT_AND_SUBMISSION_ACCEPTANCE_UNVERIFIED",
    "RESEARCH_AUDIT_IS_NOT_OFFICIAL_APPROVAL",
)


def _read(path):
    return json.loads(Path(path).read_text())


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_release():
    from scripts.verify_double_check_structural import verify_release as verify
    return verify(STUDY)


def build_config(track="official_ex_post"):
    if track not in TRACKS:
        raise ValueError("Unknown universe track")
    audit = verify_release()
    selected = _read(STUDY / "selection.json")
    config = _read(STUDY / track / "final" / RELEASE / "config.json")
    from src.official_deep_tuning import FIXED, validate_params
    validate_params(config["full_tuning_params"])
    if selected["candidate_id"] != audit["selected_candidate"] or selected["params"] != config["full_tuning_params"]:
        raise ValueError("Selected parameters differ from audited final configuration")
    if selected["source"] not in ("prior", "new") or selected["submission_status"] != "BLOCK_SUBMISSION":
        raise ValueError("Selected provenance or submission gate changed")
    if config.get("strategy_id") != "full_tuned_v2_double_check_" + selected["candidate_id"]:
        raise ValueError("Selected strategy ID differs")
    if config.get("universe_mode") != track:
        raise ValueError("Selected universe track differs")
    for key, value in FIXED.items():
        if config.get(key) != value:
            raise ValueError("Fixed rule changed: " + key)
    if (config.get("study_policy", {}).get("official_live_submission") != "BLOCK_IF_UNKNOWN"
            or config.get("official_review_policy", {}).get("active_share") != "UNKNOWN_BLOCK_SUBMISSION"):
        raise ValueError("Official submission gate changed")
    return config


def run_backtest(track, output):
    from scripts.audit_double_check import audit_result
    from src import double_check_tuning, tuning_a_deep
    from src.backtest import save_result

    target = Path(output).resolve()
    for protected in (STUDY.resolve(), PARENT_STUDY.resolve(), OLDER_STUDY.resolve(), FOUNDATION_STUDY.resolve()):
        if target == protected or protected in target.parents:
            raise ValueError("Research replay cannot write inside an audited study")
    if target.exists():
        raise FileExistsError("Preserve prior output: " + str(target))
    config = build_config(track)
    context = tuning_a_deep.context(track)
    result = double_check_tuning.run_model(context, config)
    independent = audit_result(result, context)
    if independent.get("independent_audit") != "PASS":
        raise ValueError("Independent replay audit did not pass")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".structural-replay-") as temp:
        staged = Path(temp) / "result"
        save_result(result, staged)
        for table in ("plan_audit", "compliance_daily", "rejected_trades"):
            result[table].to_csv(staged / (table + ".csv"), index=False)
        (staged / "independent_audit.json").write_text(
            json.dumps(independent, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        receipt = {path.name: _sha(path) for path in staged.iterdir() if path.is_file()}
        (staged / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        staged.rename(target)
    print(json.dumps(dict(research_audit="PASS", submission_status="BLOCK_SUBMISSION",
                          track=track, output=str(target), metrics=result["metrics"]),
                     ensure_ascii=False, indent=2, allow_nan=False))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("plan", "research-plan"):
        print(json.dumps(dict(status="BLOCK", submission_status="BLOCK_SUBMISSION",
                              code="OFFICIAL_ELIGIBILITY_UNRESOLVED",
                              blocking_codes=LIVE_BLOCKERS,
                              detail="No D-Plan exported.")))
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--verify-release", action="store_true")
    action.add_argument("--show-config", action="store_true")
    action.add_argument("--output", type=Path)
    parser.add_argument("--track", choices=TRACKS, default=TRACKS[0])
    options = parser.parse_args(args)
    try:
        if options.verify_release:
            audit = verify_release()
            print(json.dumps(dict(research_audit=audit["status"],
                                  selected_candidate=audit["selected_candidate"],
                                  submission_status="BLOCK_SUBMISSION",
                                  blocking_codes=LIVE_BLOCKERS)))
        elif options.show_config:
            print(json.dumps(build_config(options.track), ensure_ascii=False, indent=2))
        elif options.output is not None:
            run_backtest(options.track, options.output)
        else:
            parser.print_help()
    except (AssertionError, ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps(dict(status="BLOCK", submission_status="BLOCK_SUBMISSION",
                              error=str(exc)), ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
