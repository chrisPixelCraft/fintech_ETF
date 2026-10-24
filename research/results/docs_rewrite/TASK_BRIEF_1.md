Goal:
Rewrite every Markdown file listed in research/results/docs_rewrite/shard1.txt to be shorter and easier to read, without losing facts.

Required instruction (apply the installed human-readable-docs skill before editing; report Invoked/Unavailable):
幫我整理的更好閱讀 簡明、精準、一目了然的方式呈現給我
$human-readable-docs

Rules:
- Keep each file's language (Chinese stays Traditional Chinese; English stays English). Do not translate.
- Keep the conclusion first; shallow headings (#, ##, ### only when needed); nested bullets indented with one tab; max 2 bullet levels; tables only for comparisons.
- Remove repetition, generic prose and meta-commentary. Merge sections that say the same thing.
- Be noticeably more concise: for prose-heavy files aim for 25–40% fewer characters (pilot only reached 3–4%). Shorten sentences, delete restated context, collapse duplicated explanations; tables of results may stay. Fidelity (checker) still wins over length.
- Preserve every number, link target, file path, code identifier, command, date, decision (PASS/STOP/HOLD etc.) and caveat that changes interpretation. Do not invent facts, do not change any result or claim strength.
- Keep relative links working; do not rename or move files; do not touch files outside shard1.txt.
- Do not edit: PREREGISTRATION.md, *BRIEF*.md, CODEX_*.md, official_docs/, third_party/, any non-Markdown file.

Verification (mandatory):
- After rewriting, run: .venv/bin/python research/results/docs_rewrite/check_fidelity.py research/results/docs_rewrite/shard1.txt --json research/results/docs_rewrite/fidelity_1.json
- Target: failing 0. Missing numbers/links/code spans must be restored. If a token is genuinely a duplicate that only appeared in removed repetition, it still has to appear once; the checker requires set containment.
- Report per file: old chars → new chars.

Pilot / checkpoint:
- PILOT_ONLY=no (pilot done: the first 3 files of shard1.txt are already rewritten and approved; you may tighten them further but must keep checker passing). If yes: rewrite only the first 3 files of shard1.txt (in file order), run the checker on those 3 (write a 3-line list to research/results/docs_rewrite/pilot.txt and pass it to the checker), write the report, and stop.

Execution mode: main directory. Allowed write paths: the files in shard1.txt; research/results/docs_rewrite/fidelity_1.json, pilot.txt (shard 1 only), CODEX_REPORT_1.md.
Report: research/results/docs_rewrite/CODEX_REPORT_1.md (Status, Files processed expected/processed/failed, checker output summary, per-file size table, Human-readable-docs skill: Invoked/Unavailable, deviations, blockers).
Git: do not commit, push, branch, or merge.
