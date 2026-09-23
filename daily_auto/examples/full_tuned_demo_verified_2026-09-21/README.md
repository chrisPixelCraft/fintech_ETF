# Full-tuned CLI verified research input

- Signal date: `2026-09-21`
- Proposed next date: `2026-09-22`; it remains deliberately unverified as a trading session.
- Ledger: NTD 1,000,000,000 research initial-capital assumption with no holdings; it is not an organizer ledger.
- Local files re-read at: `2026-09-22T14:53:39+08:00`
- The unchanged daily/hourly bytes remain at their repository paths and their actual SHA-256 values are bound in `source_manifest.json`.
- This input intentionally has no official Active Share, corporate-action clearance, calendar, settlement ledger, or passive-cap history. The resulting packet must remain `BLOCK_SUBMISSION`.

Run from the repository root:

```bash
python fintune_v2.py plan --state daily_auto/examples/full_tuned_demo_verified_2026-09-21/state.json --daily data/tuning_2nd/official_universe/processed/daily.csv --hourly data/tuning_2nd/official_universe/processed/hourly.csv --source-manifest daily_auto/examples/full_tuned_demo_verified_2026-09-21/source_manifest.json --output daily_auto/runs/my_fintune_demo
```

Expected exit code is 2: a complete local packet is generated, but submission remains blocked by the explicitly missing official evidence. This is a successful negative-path demonstration, not a successful competition submission. Use a new output directory on every run.
