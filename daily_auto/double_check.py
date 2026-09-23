"""Fail-closed daily adapter for the frozen double-check research release."""
from __future__ import annotations

import copy
from dataclasses import replace
import inspect
import json
from pathlib import Path
from types import FunctionType

from daily_auto import full_tuned
import v2_double_check_fintuned as entry


def _load_fixed_strategy():
    audit = entry.verify_release()
    config = entry.build_config("official_ex_post")
    from src.double_check_tuning import OfficialPlanner
    paths = [entry.ROOT / relative for relative in audit["input_hashes"]]
    paths.extend([entry.STUDY / "audit.json", entry.STUDY / "selection.json",
                  entry.STUDY / "official_ex_post/final" / entry.RELEASE / "config.json"])
    paths.append(Path(inspect.getsourcefile(OfficialPlanner)))
    return entry, config, OfficialPlanner(), paths


def build_research_packet(**kwargs):
    """Keep original source/ledger gates; bind exact new selected configuration."""
    if not full_tuned._same_json(kwargs.get("config"), entry.build_config("official_ex_post")):
        raise full_tuned.PacketBuildError("DOUBLE_CHECK_CONFIG", "Daily config differs from the audited release")
    return full_tuned.build_packet(**kwargs)


def _write_research_draft(packet, output_dir):
    """Archive diagnostics without producing an ingestible D-Plan document."""
    receipt = copy.deepcopy(dict(packet.receipt))
    receipt.update(mode="RESEARCH_DRAFT_NEVER_SUBMIT", submission_status="BLOCK_SUBMISSION",
                   preflight_status="RESEARCH_ONLY", release=entry.RELEASE,
                   release_blocking_codes=list(entry.LIVE_BLOCKERS))
    draft = dict(kind="RESEARCH_DRAFT_NEVER_SUBMIT", submission_status="BLOCK_SUBMISSION",
                 release_blocking_codes=list(entry.LIVE_BLOCKERS), candidate_plan=packet.plan)
    renamed = "RESEARCH-DRAFT_" + packet.filename.removeprefix("D-Plan_")
    return full_tuned.write_packet(replace(packet, filename=renamed, plan=draft, receipt=receipt), output_dir)


def main(argv=None, *, research_draft=False):
    if not research_draft:
        # This release has no verified official AS definition or accepted strategy
        # declaration. A local PASS, booleans, or a forged state cannot unlock it.
        try:
            entry.verify_release()
        except (ValueError, KeyError, TypeError, OSError) as exc:
            print(json.dumps(dict(status="BLOCK", submission_status="BLOCK_SUBMISSION",
                                  code="RELEASE_NOT_VERIFIED", error=str(exc))))
            return 2
        print(json.dumps(dict(status="BLOCK", submission_status="BLOCK_SUBMISSION",
                              code="OFFICIAL_ELIGIBILITY_UNRESOLVED",
                              blocking_codes=entry.LIVE_BLOCKERS,
                              detail="No D-Plan exported. research-plan creates a non-submittable diagnostic draft.")))
        return 2
    # Function-local bindings preserve the old release and its frozen source.
    namespace = dict(vars(full_tuned))
    namespace.update(_load_fixed_strategy=_load_fixed_strategy,
                     build_packet=build_research_packet, write_packet=_write_research_draft)
    function = FunctionType(full_tuned.main.__code__, namespace, argdefs=full_tuned.main.__defaults__)
    return function(argv)


if __name__ == "__main__":
    raise SystemExit(main())
