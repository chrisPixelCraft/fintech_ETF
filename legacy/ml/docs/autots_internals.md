# AutoTS Internals and Adaptation Plan

Use curated templates + custom episode validation for NAV backtests; reserve genetic / horizontal search for offline sweeps. Four recent episodes verify hooks, not forecasting skill.

Setup, flow, hooks and real 150-stock runtimes. `file:line`: `third_party/autots/autots/`, upstream `d35f3189` (1.0.4) + P1; `third_party/autots/UPSTREAM.md`.

## 0. Setup

```bash
# one-time
uv venv --python /opt/homebrew/bin/python3.12 .venv
uv pip install --python .venv/bin/python -r requirements-autots.txt
echo "$PWD/third_party/autots" > .venv/lib/python3.12/site-packages/fintech_etf_third_party.pth

# verify
.venv/bin/python -c "import autots; print(autots.__version__, autots.__file__)"
# -> 1.0.4 .../fintech_ETF/third_party/autots/autots/__init__.py

# run everything with this interpreter
.venv/bin/python -m unittest discover -s tests -q
PYTHONHASHSEED=0 .venv/bin/python your_script.py
```

- **Python.** CPython 3.12.10.
	- Legacy `requirements.txt`: numpy 1.24 cannot install on 3.13.
- **Pins:** pandas <3; numpy <2.3.
	- pandas 3.0.6: 21 legacy failures.
	- pandas 2.3.3: 1 failure / 295 tests.
- **Legacy `.venv`:** 295 tests, 1 failure, 2 skips.
	- Failure: `test_report_24d...test_snapshot_uses_this_checkout...`.
	- macOS `/var` / `/private/var` symlink issue, not regression.
- **Unusable system Python:** miniconda 3.13 (`python`), 259 tests, 1 failure, 11 errors.
	- Missing pyarrow / yfinance.
- **Import cost.** `import autots`: ~1.4 s; silent with matplotlib installed.

## 1. End-to-end flow

`AutoTS.fit(df)` → `AutoTS.predict()`. Default file: `evaluator/auto_ts.py`.

| Stage | What happens | Where |
|---|---|---|
| Config | Expand aliases; parse `ensemble`; build `General` / `Random` / DataFrame template; prune outside `transformer_list` | `__init__` L189–520<br>• aliases L367<br>• template L374–466 |
| Input | Wide DatetimeIndex or `long_to_wide`; infer frequency; `df_cleanup`: `na_tolerance`, `drop_most_recent`, `drop_data_older_than_periods`; categoricals via `NumericTransformer` | `fit_data` L1044–1100<br>• `tools/shaping.py:68` |
| Global preprocessing | ≤500 series: `transformer_list="auto"` → `fast`; full-df `preclean` GeneralTransformer (**validation leak**) and `profile_time_series`; align `future_regressor` | L1111–1206 |
| Validation windows | `validate_num_validations` caps count; `generate_validation_indices`: `backwards` / `even` / `seasonal n` / `similarity` / `mixed_length`; `custom` takes user list | L1208–1246<br>• `validation.py:25, 91` |
| Round-0 split | Test = last `forecast_length` (subset) rows | L1357–1372<br>• `shaping.py:401` |
| Candidate evaluation | `_run_template` → `TemplateWizard`; per row `model_forecast` → `ModelPrediction`: GeneralTransformer fit/transform → `ModelMonster(model).fit/predict` → inverse | L2056<br>• `auto_model.py:2090, 1306, 743, 166` |
| Metrics | `PredictionObject.evaluate` → `full_metric_evaluation`: ~25 metrics / series; calls `custom_metric(A, F, df_train, pi)` | `models/base.py:1370`<br>• `metrics.py:594`<br>• custom call at `metrics.py:706` |
| Scoring | `generate_score`: sum metric / best-model metric × `metric_weighting`, lower is better; horizontal/mosaic use `generate_score_per_series` | `auto_model.py:3198, 3466` |
| Genetic search | `max_generations` × `NewGeneticTemplate`: mutate/recombine class leaders; random transformers from `transformer_list` | L1430–1497<br>• `auto_model.py:2801` |
| Ensembles (pre-validation) | `EnsembleTemplateGenerator` builds simple/dist/subsample/mlensemble | L1499–1531<br>• `models/ensemble.py:1162` |
| Cross-validation | `_construct_validation_template`: top-Score `models_to_validate`, `max_per_model_class` cap + horizontal best-per-series; `_run_validations` per window | L1696–1792<br>• L2187–2316 |
| Aggregation | `validation_aggregation`: mean of each metric over rounds, `Runs` = count | `auto_model.py:3060`<br>• L1794 |
| Horizontal / mosaic | `HorizontalTemplateGenerator`: per-series `idxmin`; mosaic also per step; optional shifted-start validation | L1608–1687<br>• `ensemble.py:1577, 2140` |
| Selection | `_best_non_horizontal`: lowest recomputed Score among `Runs >= num_validations+1`; requested successful horizontal ensemble wins automatically | L1806–2002 |
| Predict | `_predict`: full-df refit via `model_forecast`; `update_fit` retains fitted object; inverse categoricals then preclean | L2318–2443<br>• `predict` L2445 |
| Output | `PredictionObject`: wide same-column `.forecast`, `.upper_forecast`, `.lower_forecast`; `.model_parameters` | `base.py:611` |

### Selection and causality traps

**Custom round 0:** `fit` only checks non-tuple `validation_indexes[0]` max date (L1362), then splits full df (L1367). Use full index at `custom` element 0, extra episodes at 1..n. `(train_idx, test_idx)` tuples work every round (L1358, L2217, L2250).

**`custom_metric`:** `generate_score` uses `custom_weighted` (L3315–3323); `generate_score_per_series` omits custom (L3466–3677); `TemplateWizard` omits `per_series_custom` (L2519–2577). Rank IC therefore selects global models; horizontal/mosaic needs a patch.

**Score scale:** `custom_score = custom - min + 1` (L3320), versus best-model ratios ≥1. Magnitude sets weight: negative IC [-1, 1] → [1, 3], comparable to `mae/min(mae)`.

**Intervals are mostly heuristic.**

- Evidence: `LastValueNaive`, `ETS`, `GLS` and most sklearn/motif models use `Point_to_Probability` / `historic_quantile` (`tools/probabilistic.py:21, 161`).
	- Spread uses the last 100 transformed levels, not horizon-scaled error.
	- No-op zero guard: `historic_quantile` discards `np.where` (`probabilistic.py:44–48`).
- Evidence: ARIMA and UnobservedComponents use statsmodels `conf_int` (`models/statsmodels.py:742, 1155`).
- Calibrate upper/lower from validation residuals (§2f).

**Off-center bounds:** inverse uses `bounds=True`, `fillzero=True` (`transform.py:8683`), skipping `AlignLastValue`.

**Curate random sklearn (§2c):** 1-generation failures included missing `xgboost` (cause 1) and `regression_type="User"` without regressor (cause 2). `WindowRegression.get_new_params` draws slow `max_windows=5,000,000` with weight 0.9 (`sklearn.py:2528–2530`) at 150 series.

**Hash order:** `best`, `slow`, `univariate`, `all_pragmatic` use `set()` (`model_list.py`), affecting RandomTemplate. Use explicit lists and `PYTHONHASHSEED=0`; order depends on `PYTHONHASHSEED`.

**Clock:** `frequency="B"` fills Taiwan holidays (750-day panel: 8.4% NaN, mainly holidays / late listings). A 24-step forecast spans 24 business days, not trading days. Use synthetic trading clock (§2d); dates follow `frequency`.

**Short histories:** `3718.TWO`: 15 rows (listed 2026-09-03); `7828.TWO`: 341; `7769.TW`: 463. `ffill` then bfill then 0 (`tools/impute.py:196`) fabricates flat history. Mask externally or require minimum history.

**`preclean` leaks:** full-df fit before split (L1159–1168). Use `preclean=None`; preprocess per cutoff.

**`introduce_na=None`:** NaN in last 2 rows blanks validation training tails (L1156, L2280). With suspensions use `introduce_na=False`.

## 2. Adaptation points

Ranked by impact on terminal NAV.

| # | Adaptation / status | Hook and boundary |
|---|---|---|
| 1 | Chronological 24-trading-day validation; benchmark 5 verified | `validation_method="custom"`, `fit(validation_indexes=...)`, `auto_ts.py:1288–1299, 1233–1246`; slicing `L2216–2260`. API, no patch; full index at element 0 or explicit tuples. |
| 2 | Rank IC / top-k / hit rate; global API verified, horizontal patch undone | `custom_metric`, `auto_ts.py:240`, `metrics.py:706`, `custom_weighting`, `auto_model.py:3315`. Add `per_series_custom` to `TemplateWizard` (L2519) and `generate_score_per_series` (L3466). |
| 3 | Walk-forward NAV; design | Wrapper calls per cutoff, maps forecasts to portfolio, scores NAV. `retrieve_validation_forecasts`, `auto_ts.py:3716`; `_predict(df_wide_numeric=...)` L2318. |
| 4 | Bounded finance search; needed | API `model_list` / `transformer_list` / `transformer_max_depth` / `models_mode`; regressor dicts `sklearn.py:761–860`, `WindowRegression.get_new_params` `sklearn.py:2492`. Patch / override dicts or curated `import_template(method="only")`. |
| 5 | Return forecasts; design | Trading-clock log-price input; h-day return = forecast − last log price. `DifferencedTransformer` `transform.py:1676`, `PctChangeTransformer` 1755, `CumSumTransformer` 1821. |
| 6 | Empirical / conformal sigma, confidence; design | Per stock / horizon validation residuals. `.upper_forecast` / `.lower_forecast`; `prediction_interval` list in `predict` L2513; `retrieve_validation_forecasts` L3716. |
| 7 | Per-stock horizontal / mosaic; cost measured | `ensemble=["horizontal-max"]`, `auto_ts.py:311`, `ensemble.py:1577, 2140, 801`. API requires patch #2 for our metric. |
| 8 | Reproducible frozen daily model; benchmark 1 verified | API: `export_template` L2576, `import_best_model` L2904, `fit_data` L1044 + `predict`; `random_seed` L199, L1321–1324. |
| 9 | Exogenous regressors; caution | API: `future_regressor` in `fit_data` L1182, validation L2268–2277; `regression_type="User"` (`model_list.py:395`). Must be known ≥h days ahead. |
| 10 | Ensemble cost dominates run 4 | Patch `horizontal_post_processors` `auto_model.py:1719–2087` (every eval L2294–2400) to 0–2 finance-sane entries; drop `HistoricValues` (246+ failures). |
| 11 | Runtime; measured | API: `n_jobs` L244, `max_generations` L194, `generation_timeout` L236, `models_to_validate` / `max_per_model_class` L1704–1713, `subset` L200, `skip_slow_models_seconds` L1723. |

### a. Episode validation

Trading-day `idx` ends at cutoff:

```python
vi = [idx] + [idx[: len(idx) - k * step] for k in range(1, n_episodes)]
model = AutoTS(forecast_length=H, validation_method="custom", num_validations=n_episodes - 1, ...)
model.fit(df, validation_indexes=vi)
```

- `step >= H`: chronological windows; `step=24`: contest-length blocks. Train ends at start, test uses next `H` rows.
- `n_episodes = 8–12`, `step = 24`: ~1–2 years; cost scales linearly (§3).
- For 24-day episodes with `H < 24`, place `(idx[:c], idx[c:c+H])` tuples at several episode offsets.
	- Do not use mosaic with tuples (upstream warning, `auto_ts.py:138`).
- Outer NAV backtest decides; AutoTS selects inner models. Both must exclude final-holdout test windows.

### b. Competition metric

`custom_metric(A, F, df_train, prediction_interval)`: input-unit numpy A/F (`H × n_series`), raw `df_train`; return lower-is-better length-`n_series`.

- **Rank IC:** log-price `IC = spearman(F[h] - df_train[-1], A[h] - df_train[-1])`; broadcast `-IC`. Verified: `bench.py`, benchmark 5.
- **Top-k return:** broadcast negative realized mean of top-k forecasts.
- **Direction hit rate:** built-in `oda` (`metrics.py:746`); use `oda_weighting`.
- **Starting weights:** `{"custom_weighting": 2, "mae_weighting": 1, "oda_weighting": 1}`.
	- Weight `smape` 0: near-zero return denominator, uninformative log levels. Still computed and used by `generate_score` to scale runtime/contour/oda (L3421).
- **Per-series:** broadcast IC cannot rank stocks; patch row 2 for horizontal selection.

### c. Search space

Starting pool: all <2.5 s / 150-series fit (§3).

- `model_list`
	- `LastValueNaive`, `AverageValueNaive`, `SeasonalNaive`, `ETS`
	- `ARIMA` with small p/q
	- `DatepartRegression`, `WindowRegression`, `MultivariateRegression`, `SectionalMotif`, `BasicLinearModel`
	- Retain `ARIMA` / `ETS` for model-based intervals or trend damping.
- `transformer_list`: a dict such as `{"DifferencedTransformer": 1, None: 0.5, "ClipOutliers": 0.3, "StandardScaler": 0.3, "AlignLastValue": 0.3, "Detrend": 0.2}`
	- Also set `transformer_max_depth=2`.
	- Applies to random / genetic transformers; prunes initial template (L426–466).
- **Regressor pool.**
	- Module-level sklearn dicts include xgboost, SVM, RadiusNeighbors.
	- Option 1: patch P2 to Ridge/ElasticNet/LightGBM/HistGradientBoost; cap `max_windows`.
	- Option 2: wrapper runtime override before AutoTS construction.
	- Log either in `UPSTREAM.md`.
- **Curated alternative (benchmark 2):** `initial_template=<curated DataFrame>`, `max_generations=0` or small, `models_to_validate=0.99`.
- **Add model:** `elif` in `ModelMonster` (`auto_model.py:166–740`), name in `model_list.py`; `ModelObject` subclass (`models/base.py`) implements `fit`, `predict`, `get_new_params`, `get_params`.

### d. Returns vs prices

- **Clock:** synthetic contiguous business dates per cutoff, indexed by trading-day ordinal. `forecast_length=H` means H trading days without holiday NaNs. Losing real-date `DatepartRegression` / holiday semantics is acceptable for daily returns.
- **Series:** log(Adj Close), optionally causally rebased to first value; MAE ≈ comparable log-return error. Raw prices favor expensive stocks unless using `weights` (`fit(weights=...)`, L1130–1149).
- `DifferencedTransformer` models increments then inverts to levels; `PctChangeTransformer` does so for simple returns.
	- `PctChangeTransformer.fit` anchors on a NaN-filled tail (`transform.py:1776`), so keep `fillna="ffill"`.
- Direct daily-return input makes level metrics (smape, contour, spl scaler) noisy.
	- Log levels + differences retain meaningful built-in metrics.
- **Excess return:** subtract log 0050 / ^TWII (same folder) before fit, or demean forecasts cross-sectionally; latter preserves rank IC.

### e. Horizontal / mosaic

- **Selection:** `horizontal-max` takes per-series `idxmin` among fully validated models (`ensemble.py:1590`); mosaic also selects per step (`ensemble.py:2140, 2269`).
- **Cost:** prediction refits distinct components (`model_forecast`, `auto_model.py:1392–1525`); `no_shared` fits assigned series only (`auto_model.py:1537–1547`). Sum component costs; naive / ETS cheap.
- **Validation cost.** Extra shifted-start pass with `horizontal_ensemble_validation=True` (L1648–1660).
- **Hypothesis:** 2–4 H-day windows give noisy per-stock selection. Use ≥8 episodes or `horizontal-profile` pooling; test NAV benefit before adoption.

### f. Expected return, uncertainty, confidence

Per stock / horizon h, with log-level `pred = model.predict()`: 

- `mu_h = pred.forecast.iloc[h-1] - last_log_price`
- `band_h = pred.upper_forecast.iloc[h-1] - pred.lower_forecast.iloc[h-1]`: heuristic log width; use `prediction_interval` normal quantile only after calibration.
- `sigma_h`: RMS `(A - F)` at h across windows, from `model.retrieve_validation_forecasts()` refits (L3716) or stored per-series metrics.
- `confidence = mu_h / sigma_h`, or the empirical hit rate from `oda` per series (`initial_results.per_series_oda`).
- `predict(prediction_interval=[0.5, 0.8, 0.9])` returns several bands (L2513) and refits once per interval.

### g. Future regressors and causality

- Validation slices full-frame test regressors (L2268–2277); prediction takes future values. Same-day index return / forecast-day SOX close leak.
- **Safe:** calendar features; ≥H-trading-day lags; D-1-close known values held constant over horizon, built from ≤cutoff data.
- D-1 US close before TW D open is causal at D-open, but known only one day ahead; steps 2..H require carrying it forward.
- Exclude `regression_type="User"` without regressors; those models fail.

### h. Runtime knobs

- `n_jobs`: parallel per-series fitting inside ETS/ARIMA/etc. Default is 0.5 × cores = 6 on this M2 Max.
- `max_generations`: 0 evaluates only initial template; each generation adds ~ `len(model_list) × 5` candidates.
- `models_to_validate`: 0.99 means all; the float fraction applies to evaluated models.
- `subset`: random series subset per window (except mosaic).
- `skip_slow_models_seconds`, `generation_timeout` (minutes): hard caps.

### i. Reproducibility

- Seeding: `random_seed` seeds `random` and `np.random` in `__init__` and `fit` (L302, L1321–1324), and is passed to models.
	- Use explicit model lists and `PYTHONHASHSEED=0`.
	- Pin the env via `requirements-autots.txt` and the vendored commit.
- **Freeze:** `model.export_template("best.csv", models="best", n=1)` (L2576). **Daily:** `AutoTS(...).import_best_model("best.csv")` → `.fit_data(df)` → `.predict()`; no search (L2904, L1044), benchmark 1. Registry: template CSV hash, cutoff, AutoTS commit.
- **Warm start the next search:** `import_template(file, method="add_on", force_validation=True)` (L2822). This is upstream's `production_example.py` "evolve" pattern.
- **`update_fit`:** `fit_data` reuses model and transformer (`model_list.py:461`, `auto_model.py:1028`); clean daily refit requires `model.model = None` first.

## 3. Runtime benchmarks

Environment: Apple M2 Max (12 cores, 32 GB), `.venv` Python 3.12.10, `n_jobs=6`, `PYTHONHASHSEED=0`, `random_seed=7`, `verbose=0`.

Panel:

- 150 universe stocks (`data/reference/universe_competition_20260731.csv`), `Adj Close` from `data/yahoo_daily/v3_20260923/*.parquet`.
- Last 750 trading days, log-transformed, reindexed to `asfreq("B")`. That gives 804 rows × 150 series with 8.4% NaN (holidays and late listings).
- Data ends 2026-09-23.

Scratch `bench.py` was not committed; configurations below support reproduction.

### Single fixed model, one `model_forecast` call (fit + predict on all 150 series)

| Model (params / transform) | H=5 | H=24 |
|---|---|---|
| LastValueNaive, no transform | <0.01 s | <0.01 s |
| AverageValueNaive `mean, window=60`, Differenced | <0.01 s | <0.01 s |
| SeasonalNaive `lastvalue, lag_1=5`, Differenced | 0.01 s | 0.02 s |
| ETS damped additive trend | 1.8 s | 1.8 s |
| ARIMA(1,1,0) | 1.7 s | 1.7 s |
| DatepartRegression Ridge `simple`, Differenced | 0.02 s | 0.03 s |
| WindowRegression Ridge `window=20`, Differenced | 0.01 s | 0.01 s |
| MultivariateRegression Ridge, Differenced | 0.3 s | 1.25 s |

Before patch P1, WindowRegression failed on 150 series with "Input X contains NaN" (see `UPSTREAM.md`).

### AutoTS runs

| Run | H=5 fit | H=24 fit | Evals | Notes |
|---|---|---|---|---|
| 1. Frozen best model: `import_best_model` + `fit_data` + `predict` (WindowRegression) | 0.2 s total | 0.2 s total | 1 | Daily production path |
| 2. Fixed 8-model template, `max_generations=0`, `num_validations=2`, all validated | 6.2 s + 1.0 s predict | 8.8 s + 1.0 s predict | 24 | 0 failures<br>best = ARIMA by default weights |
| 3. Random init (84 models) + 1 generation, 7 models (no ARIMA), curated regressors, `nval=2` | 172 s | 290 s | 164–165 | • 13–16 failures, all `regression_type=User` without regressor or poisson loss<br>• best = naive |
| 4. Run 3 + `ensemble=["simple","horizontal-max"]` | 702 s + 0.9 s predict | 676 s + 5.7 s predict | 3935–4420 | • best = horizontal ensemble<br>• most evals are `horizontal_post_processors` (`auto_model.py:1719`), 14 per ensemble per round<br>• 246–294 `HistoricValues` postprocessor failures |
| 5. Fixed template + `validation_method="custom"` (4 episodes spaced 24 rows) + rank-IC `custom_metric` | 7.9 s | 11.5 s | 32 | • hooks work<br>• 4 runs per model<br>• best = WindowRegression by rank IC |

Run 5 verifies adaptation hooks:

- **Chronological, non-overlapping starts:** H=5: 2026-09-17, 08-14, 07-13, 06-09; H=24: 2026-08-21, 07-20, 06-16, 05-13.
- **Aggregation:** mean `custom` (−rank IC) over rounds.
- **Ranking at H=24.**

| Model | Mean rank IC | oda |
|---|---|---|
| WindowRegression | +0.062 | 0.55 |
| ARIMA | −0.049 | 0.47 |
| LastValueNaive | 0 (constant forecast) | 0.01 |

- **Caveat.** 4 episodes in one recent period verify plumbing, not skill.

### First-attempt failure

First run 3 attempt used ARIMA + default regressors.

- **xgboost.** Missing `xgboost` caused candidate failures.
- **Stall.** One generation-1 candidate occupied 6 workers >12 minutes; run killed.
	- No `current_model_file`; stalled model unknown.
	- Random ARIMA allowed p/q up to 12+.
- **Rerun changes.** Removed random ARIMA; runtime regressors restricted to Ridge/ElasticNet/LightGBM/HistGradientBoost.
- **Conclusion.** Bound random search with curated params, `skip_slow_models_seconds`, `generation_timeout`, and diagnostic `current_model_file`.

### Budget for a walk-forward backtest with 150–200 refits

| Per-refit strategy | Per refit | 200 refits |
|---|---|---|
| Frozen template, refit only | 0.2–2 s | under 7 min |
| Fixed 8–10 model template, 3 windows | 7–12 s | 25–40 min |
| Same, 8–12 episodes | about 25–45 s (linear in windows) | 1.5–2.5 h |
| 1-generation random search, 3 windows | 3–5 min | 10–16 h |
| Plus horizontal-max | 11–12 min | about 38 h |

Recommendation:

- Backtest curated templates + custom episode validation (rows 2–3); parallelize independent cutoffs with 6–12 processes, `n_jobs=1` inside AutoTS.
- Reserve genetic search / horizontal ensembles for a few offline single-cutoff sweeps.
- Patch `horizontal_post_processors` before loop use to remove most horizontal-ensemble cost.
