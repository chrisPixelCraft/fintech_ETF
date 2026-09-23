"""Frozen, independently audited research release; live submission is blocked.

Daily inference uses the selected double-check parameters without re-tuning.
Research audit PASS never implies official eligibility or platform acceptance.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from types import FunctionType

ROOT = Path(__file__).resolve().parent
STUDY = ROOT / "outputs/v2_double_check_fintuned"
RELEASE = "v2_double_check_fintuned"
TRACKS = ("official_ex_post", "historical_pit")
REFERENCE_SHA256 = "0009ee5d68b74ee24da9556f5f3ebd32b23adff85eb5bd7d871250ab3d8a8b13"
REQUIRED_INPUTS = frozenset({
    "v2_double_check_fintuned.py", "daily_auto/double_check.py",
    "v2_offcial_best_deep_tuning.py", "src/double_check_tuning.py",
    "src/double_check_ledger.py", "src/official_deep_tuning.py", "src/backtest.py",
    "src/tuning_a_deep.py", "src/tuning_features.py", "src/tuning_2nd.py",
    "src/official_v2_review.py", "scripts/audit_double_check.py",
    "daily_auto/full_tuned.py", "daily_auto/validate.py", "daily_auto/operations.py",
    "daily_auto/compliance_state.py", "daily_auto/strategy_declaration.md",
    "daily_auto/reference/official_reference.json",
})
LIVE_BLOCKERS = (
    "OFFICIAL_ACTIVE_SHARE_DEFINITION_UNRESOLVED",
    "OFFICIAL_STRATEGY_DECLARATION_ACCEPTANCE_UNVERIFIED",
    "OFFICIAL_ACCOUNT_AND_SUBMISSION_ACCEPTANCE_UNVERIFIED",
    "RESEARCH_AUDIT_IS_NOT_OFFICIAL_APPROVAL",
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path):
    return json.loads(Path(path).read_text())


def verified_reference():
    path = ROOT / "daily_auto/reference/official_reference.json"
    if _sha(path) != REFERENCE_SHA256:
        raise ValueError("Audited official reference changed")
    return _json(path)


def _verify_hashes(root, hashes, required):
    if not isinstance(hashes, dict) or not required <= set(hashes):
        raise ValueError("Incomplete release hash manifest")
    for relative, expected in hashes.items():
        path = (root / relative).resolve()
        if Path(relative).is_absolute() or root.resolve() not in path.parents:
            raise ValueError("Release manifest path escapes root: " + relative)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid release digest: " + relative)
        if _sha(path) != expected:
            raise ValueError("Frozen release file changed: " + relative)


def verify_release():
    verified_reference()
    audit = _json(STUDY / "audit.json")
    if audit.get("status") != "PASS":
        raise ValueError("Release has no independent research PASS")
    _verify_hashes(ROOT, audit.get("input_hashes"), REQUIRED_INPUTS)
    required = {"selection.json"} | {f"{track}/final/{RELEASE}/config.json" for track in TRACKS}
    _verify_hashes(STUDY, audit.get("artifact_hashes"), required)
    return audit


def get_params():
    verify_release()
    return copy.deepcopy(_json(STUDY / "selection.json")["params"])


def build_config(track="official_ex_post"):
    if track not in TRACKS:
        raise ValueError("Unknown universe track")
    verify_release()
    selected = _json(STUDY / "selection.json")
    config = _json(STUDY / track / "final" / RELEASE / "config.json")
    from src.official_deep_tuning import FIXED, validate_params
    validate_params(config["full_tuning_params"])
    if config["full_tuning_params"] != selected["params"]:
        raise ValueError("Config differs from the frozen selected parameters")
    if config.get("strategy_id") != "full_tuned_v2_double_check_" + selected["candidate_id"]:
        raise ValueError("Config differs from the selected double-check strategy")
    if config.get("universe_mode") != track:
        raise ValueError("Config universe track differs")
    for key, value in FIXED.items():
        if config.get(key) != value:
            raise ValueError("Fixed rule changed: " + key)
    if (config.get("study_policy", {}).get("official_live_submission") != "BLOCK_IF_UNKNOWN"
            or config.get("official_review_policy", {}).get("active_share") != "UNKNOWN_BLOCK_SUBMISSION"):
        raise ValueError("Fail-closed submission policy changed")
    return copy.deepcopy(config)


def prepare_signals(**kwargs):
    """Reuse verified causal feature processing with this release's config only."""
    verify_release()
    import v2_offcial_best_deep_tuning as original
    namespace = dict(vars(original))
    namespace.update(build_config=build_config, verified_reference=verified_reference)
    function = FunctionType(original.prepare_signals.__code__, namespace,
                            argdefs=original.prepare_signals.__defaults__)
    function.__kwdefaults__ = original.prepare_signals.__kwdefaults__
    return function(**kwargs)


def run_backtest(track, output):
    from src import double_check_tuning, tuning_a_deep
    from src.backtest import save_result
    from scripts.audit_double_check import audit_result
    target = Path(output)
    if target.exists():
        raise FileExistsError("Preserve prior output: " + str(target))
    context = tuning_a_deep.context(track)
    result = double_check_tuning.run_model(context, build_config(track))
    audit = audit_result(result, context)
    save_result(result, target)
    for table in ("plan_audit", "compliance_daily", "rejected_trades"):
        result[table].to_csv(target / (table + ".csv"), index=False)
    (target / "independent_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("plan", "research-plan"):
        from daily_auto.double_check import main as daily_main
        return daily_main(args[1:], research_draft=args[0] == "research-plan")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show-config", action="store_true")
    parser.add_argument("--verify-release", action="store_true")
    parser.add_argument("--track", choices=TRACKS, default=TRACKS[0])
    parser.add_argument("--output", type=Path)
    options = parser.parse_args(args)
    try:
        if options.show_config:
            print(json.dumps(build_config(options.track), ensure_ascii=False, indent=2))
        elif options.verify_release:
            verify_release()
            print(json.dumps(dict(research_audit="PASS", submission_status="BLOCK_SUBMISSION",
                                  blocking_codes=LIVE_BLOCKERS)))
        elif options.output:
            run_backtest(options.track, options.output)
        else:
            parser.print_help()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps(dict(status="BLOCK", submission_status="BLOCK_SUBMISSION", error=str(exc))))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
