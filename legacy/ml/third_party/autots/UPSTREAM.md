# Vendored AutoTS

This directory is a pinned copy of AutoTS. It is the primary forecasting engine of the fintech_ETF strategy. We edit it in place, and every local change is listed below.

## Source

| Field | Value |
|---|---|
| Repository | https://github.com/winedarksea/AutoTS |
| Commit | `d35f3189e0d2bab84732e957ca3f4c737da08bf0` (Merge PR #282 from winedarksea/dev, 2026-08-19) |
| Version | `1.0.4` (git tag `1.0.4` points at this commit; `autots/__init__.py:34`) |
| Vendored on | 2026-09-24 |
| License | MIT, Copyright (c) 2024 Colin Catlin. Full text in `LICENSE` next to this file |

## What was copied

- `autots/`: the whole Python package, including `datasets/data/*.zip` and `mcp/`. Size is about 5.2 MB. `__pycache__` was excluded.
- `LICENSE`: upstream MIT license. The license requires that it stays with the code.
- Not copied: `.git`, `tests/`, `docs/`, `examples/`, `site/`, `pwa/`, and the upstream root files (`production_example.py`, `extended_tutorial.md`, `setup.py`, `pyproject.toml`).

Upstream's `production_example.py` is referenced in `docs/autots_internals.md`. To read it, check out the same commit:

```bash
git clone https://github.com/winedarksea/AutoTS.git && cd AutoTS && git checkout d35f3189e0d2bab84732e957ca3f4c737da08bf0
```

## How it is imported

The project venv has `.venv/lib/python3.12/site-packages/fintech_etf_third_party.pth`, which contains the absolute path of `third_party/autots`. So `import autots` resolves to `third_party/autots/autots/__init__.py`. Do not `pip install autots` into the same venv, because that would shadow or conflict with this copy.

## Local patches

Each patch is marked in the source with a `fintech_ETF patch Pn` comment.

| ID | File | Change | Why |
|---|---|---|---|
| P1 | `autots/tools/window_functions.py` `chunk_reshape` | `excess_series = num_series % num_chunks` became `max(num_series - num_chunks * chunk_size, 0)` | With more than 100 series and a count that is not a multiple of 100 (our 150 stocks), upstream silently dropped series 101–150. It also left `np.empty` rows uninitialized, which fed garbage or NaN windows to `WindowRegression` and made Ridge fail with "Input X contains NaN". Verified afterwards: all series are present and the windows are finite for n = 6, 100, 150, 250 and 1000. |

To see our changes against upstream, diff this directory against a fresh checkout of the commit above.
