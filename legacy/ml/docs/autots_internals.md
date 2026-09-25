# AutoTS Internals and Adaptation Plan

This note is for whoever builds the AutoTS-first ETF strategy. It covers:

1. how to run the vendored AutoTS;
2. how AutoTS turns a panel of series into a forecast, with `file:line` references;
3. where to hook in for our competition;
4. measured runtimes on the real 150-stock panel.

All line numbers refer to `third_party/autots/autots/` at upstream `d35f3189` (1.0.4) plus local patch P1. See `third_party/autots/UPSTREAM.md`.

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

- **Python.** The venv uses CPython 3.12.10.
  - The legacy `requirements.txt` pins numpy 1.24, which cannot install on 3.13.
- **Library pins.** pandas is held below 3 and numpy below 2.3.
  - With pandas 3.0.6, the legacy suite failed 21 tests.
  - With pandas 2.3.3, it fails 1 test out of 295.
- **Legacy tests.** Under `.venv` the suite ran 295 tests, with 1 failure and 2 skips.
  - The failure is `test_report_24d...test_snapshot_uses_this_checkout...`.
  - Its cause is macOS `/var` vs `/private/var` symlink path resolution, not a code regression.
- **System Python is not usable.** Under miniconda 3.13 (`python`), 259 tests ran with 1 failure and 11 errors.
  - pyarrow and yfinance are missing there.
- **Import cost.** `import autots` takes about 1.4 s and prints nothing once matplotlib is installed.

## 1. End-to-end flow

`AutoTS.fit(df)` → `AutoTS.predict()`. Everything is in `evaluator/auto_ts.py` unless another file is named.

| Stage | What happens | Where |
|---|---|---|
| Config | • Model-list aliases expanded<br>• `ensemble` parsed<br>• initial template built (`General` / `Random` / DataFrame)<br>• transformers outside `transformer_list` pruned from it | `__init__` L189–520<br>• aliases L367<br>• template L374–466 |
| Input | • Wide df with a DatetimeIndex, or long df via `long_to_wide`<br>• frequency inferred<br>• `df_cleanup` does `na_tolerance`, `drop_most_recent`, `drop_data_older_than_periods`<br>• `NumericTransformer` for categoricals | `fit_data` L1044–1100<br>• `tools/shaping.py:68` |
| Global preprocessing | • `transformer_list="auto"` becomes `fast` for ≤500 series<br>• optional `preclean` GeneralTransformer on the full df (**leaks into validation**)<br>• `profile_time_series` on the full df<br>• `future_regressor` aligned to history | L1111–1206 |
| Validation windows | • `validate_num_validations` caps the count<br>• `generate_validation_indices` builds the windows (`backwards` / `even` / `seasonal n` / `similarity` / `mixed_length`)<br>• `custom` uses the user list | L1208–1246<br>• `validation.py:25, 91` |
| Round-0 split | Last `forecast_length` rows of the (subset) df become the test set | L1357–1372<br>• `shaping.py:401` |
| Candidate evaluation | • `_run_template` → `TemplateWizard`<br>• each row: `model_forecast` → `ModelPrediction` (GeneralTransformer fit/transform → `ModelMonster(model).fit/predict` → inverse transform) | L2056<br>• `auto_model.py:2090, 1306, 743, 166` |
| Metrics | • `PredictionObject.evaluate` → `full_metric_evaluation`, giving one column of about 25 metrics per series<br>• `custom_metric(A, F, df_train, pi)` is called here | `models/base.py:1370`<br>• `metrics.py:594`<br>• custom call at `metrics.py:706` |
| Scoring | • `generate_score`: each metric is divided by the best model's value and multiplied by `metric_weighting`, then summed; lower is better<br>• `generate_score_per_series` scores each series for horizontal/mosaic | `auto_model.py:3198, 3466` |
| Genetic search | • `max_generations` loops of `NewGeneticTemplate`<br>• top models per class get mutated and recombined<br>• new random transformers are drawn from `transformer_list` | L1430–1497<br>• `auto_model.py:2801` |
| Ensembles (pre-validation) | `EnsembleTemplateGenerator` builds simple/dist/subsample/mlensemble | L1499–1531<br>• `models/ensemble.py:1162` |
| Cross-validation | • `_construct_validation_template`: top `models_to_validate` by Score, capped at `max_per_model_class`, plus best-per-series models if horizontal<br>• `_run_validations` re-runs them on each window | L1696–1792<br>• L2187–2316 |
| Aggregation | `validation_aggregation`: mean of each metric over rounds, `Runs` = count | `auto_model.py:3060`<br>• L1794 |
| Horizontal / mosaic | • `HorizontalTemplateGenerator` picks the `idxmin` of per-series score<br>• mosaic also works per forecast step<br>• optional shifted-start re-validation | L1608–1687<br>• `ensemble.py:1577, 2140` |
| Selection | • `_best_non_horizontal`: models with `Runs >= num_validations+1`, lowest re-computed Score<br>• a horizontal ensemble wins automatically if one was requested and succeeded | L1806–2002 |
| Predict | • `_predict` refits the chosen template on the full df via `model_forecast`<br>• `update_fit` models keep the fitted object<br>• categorical inverse, then preclean inverse | L2318–2443<br>• `predict` L2445 |
| Output | `PredictionObject` with `.forecast`, `.upper_forecast`, `.lower_forecast` (wide, same columns) and `.model_parameters` | `base.py:611` |

### Behaviors that matter for us

**Round 0 ignores the first custom index.**

- Evidence: for a non-tuple `validation_indexes[0]`, `fit` only checks its max date (L1362). It then splits the whole df (L1367).
- Conclusion: in `custom` mode, element 0 must be the full index, and elements 1..n are the extra episodes.
- Tuple elements `(train_idx, test_idx)` are honored in every round (L1358, L2217, L2250).

**`custom_metric` is not used by per-series (horizontal/mosaic) selection.**

- Evidence: `generate_score` uses `custom_weighted` (L3315–3323). `generate_score_per_series` has no custom term (L3466–3677), and `TemplateWizard` stores no `per_series_custom` (L2519–2577).
- Conclusion: a rank-IC metric can pick the global model, but it cannot pick per-stock models without a patch.

**The custom score is shifted, not scaled.**

- Evidence: `custom_score = custom - min + 1` (L3320). Other metrics are ratios to the best model, roughly ≥1.
- Conclusion: the magnitude of the custom value sets its effective weight. Negative rank IC in [-1, 1] maps to [1, 3], which is comparable to `mae/min(mae)`.

**Intervals are mostly heuristic.**

- Evidence: `LastValueNaive`, `ETS`, `GLS` and most sklearn/motif models use `Point_to_Probability` / `historic_quantile` (`tools/probabilistic.py:21, 161`).
  - This measures the spread of the last 100 transformed levels, not horizon-scaled error.
  - `historic_quantile` also contains a no-op zero guard (`probabilistic.py:44–48`, where the `np.where` result is discarded).
- Evidence: ARIMA and UnobservedComponents use statsmodels `conf_int` (`models/statsmodels.py:742, 1155`).
- Conclusion: do not treat upper/lower as calibrated. Calibrate from validation residuals (§2f).

**Interval inverse transform.**

- Evidence: bounds are inverse-transformed with `bounds=True`, `fillzero=True` (`transform.py:8683`). The `AlignLastValue` adjustment is skipped for bounds.
- Conclusion: bounds can sit off-center from the point forecast.

**Random search picks unusable parameters.**

- Evidence: in the 1-generation benchmark, some random candidates failed.
  - Cause 1: `xgboost` is not installed.
  - Cause 2: `regression_type="User"` was drawn without a regressor.
- Evidence: `WindowRegression.get_new_params` draws `max_windows=5,000,000` with weight 0.9 (`sklearn.py:2528–2530`), which is slow at 150 series.
- Conclusion: the random sklearn search space needs curation (§2c).

**Model-list aliases built from `set()` depend on hash order.**

- Evidence: `best`, `slow`, `univariate`, `all_pragmatic` are built with `set()` (`model_list.py`).
- Conclusion: RandomTemplate order then depends on `PYTHONHASHSEED`. Use explicit lists and export `PYTHONHASHSEED=0`.

**Forecast dates follow `frequency`, not the TWSE calendar.**

- Evidence: with `frequency="B"`, Taiwan holidays become NaN rows that get filled (8.4% NaN in the 750-day panel, mostly late listings and holidays). A 24-step forecast then spans 24 business days, not 24 trading days.
- Conclusion: prefer a synthetic trading-day clock (§2d).

**Short histories.**

- `3718.TWO` has 15 rows (listed 2026-09-03), `7828.TWO` 341, `7769.TW` 463.
- `ffill` in AutoTS is ffill, then bfill, then 0 (`tools/impute.py:196`). This creates flat fake history.
- Mask these names outside AutoTS, or set a minimum-history rule.

**`preclean` leaks into validation.**

- Evidence: it is fit on the full df before splitting (L1159–1168).
- Conclusion: keep `preclean=None`. Do causal preprocessing ourselves, per cutoff.

**The default `introduce_na=None` blanks tail values.**

- Evidence: if the last 2 rows have any NaN (L1156, L2280), validation training tails are overwritten with NaN.
- Conclusion: with suspensions in the panel, pass `introduce_na=False`.

## 2. Adaptation points

Ranked by how much each one changes whether AutoTS helps terminal NAV.

| # | Need | Hook (file:line) | Mechanism | Status |
|---|---|---|---|---|
| 1 | Validation on 24-trading-day episodes, strictly chronological | • `validation_method="custom"` + `fit(validation_indexes=...)` `auto_ts.py:1288–1299, 1233–1246`<br>• slicing `L2216–2260` | • API param; no patch<br>• element 0 = full index<br>• or tuples for explicit train/test | Verified in benchmark 5 |
| 2 | Competition-aware metric (rank IC, top-k return, hit rate) | • `custom_metric` ctor arg `auto_ts.py:240`<br>• called `metrics.py:706`<br>• weighted by `custom_weighting` `auto_model.py:3315` | • API for the global model<br>• **in-tree patch** to add `per_series_custom` to `TemplateWizard` (L2519) and `generate_score_per_series` (L3466) for horizontal | API verified; patch not done |
| 3 | Walk-forward NAV evaluation instead of forecast error | • `retrieve_validation_forecasts` `auto_ts.py:3716`<br>• `_predict(df_wide_numeric=...)` L2318 | Wrapper outside AutoTS: our backtester calls AutoTS per cutoff, maps forecasts to a portfolio, and scores NAV | Design |
| 4 | Bounded, finance-appropriate search space | • `model_list` / `transformer_list` / `transformer_max_depth` / `models_mode` ctor<br>• regressor dicts `sklearn.py:761–860`<br>• `WindowRegression.get_new_params` `sklearn.py:2492` | • API for model and transformer lists<br>• **patch or runtime override** of the regressor dicts<br>• or skip random sklearn generation: `import_template(method="only")` with a curated template | Needed |
| 5 | Forecast returns, not price levels | • transformers `DifferencedTransformer` `transform.py:1676`, `PctChangeTransformer` 1755, `CumSumTransformer` 1821<br>• input shaping is ours | Wrapper: feed a log-price index on a trading-day clock; derive h-day returns from forecast minus last log price | Design |
| 6 | Uncertainty → expected return, sigma, confidence | • `.upper_forecast` / `.lower_forecast`<br>• `prediction_interval` list in `predict` L2513<br>• `retrieve_validation_forecasts` L3716 | Wrapper: empirical or conformal sigma from validation residuals per stock and horizon | Design |
| 7 | Per-stock best model (horizontal/mosaic) | • `ensemble=["horizontal-max"]` etc. `auto_ts.py:311`<br>• `ensemble.py:1577, 2140, 801` | API, but only with patch #2 so selection uses our metric | Measured cost |
| 8 | Daily frozen model, reproducible | • `export_template` L2576<br>• `import_best_model` L2904<br>• `fit_data` L1044 + `predict`<br>• `random_seed` L199, L1321–1324 | API | Verified (benchmark 1) |
| 9 | Exogenous regressors | • `future_regressor` in `fit_data` L1182<br>• validation slicing L2268–2277<br>• models with `regression_type="User"` (`model_list.py:395`) | API, but regressors must be known ≥h days ahead | Caution |
| 10 | Ensemble post-processing cost | `horizontal_post_processors` `auto_model.py:1719–2087`, applied to every ensemble eval at L2294–2400 | **In-tree patch**: trim to 0–2 finance-sane entries (drop `HistoricValues`, which failed 246+ times) | Measured: dominates run 4 |
| 11 | Runtime | • `n_jobs` L244<br>• `max_generations` L194<br>• `generation_timeout` L236<br>• `models_to_validate`, `max_per_model_class` L1704–1713<br>• `subset` L200<br>• `skip_slow_models_seconds` L1723 | API | Measured |

### a. Episode validation

Build the list ourselves from a trading-day index `idx` that ends at the cutoff:

```python
vi = [idx] + [idx[: len(idx) - k * step] for k in range(1, n_episodes)]
model = AutoTS(forecast_length=H, validation_method="custom", num_validations=n_episodes - 1, ...)
model.fit(df, validation_indexes=vi)
```

- For fully chronological windows, use `step >= H`. `step=24` gives one window per contest-length block.
  - In each window, train ends at the window start and test is the next `H` rows.
- Regime coverage: `n_episodes = 8–12` with `step = 24` covers about 1–2 years of recent history.
  - More episodes cost linearly (see §3).
- For true 24-day episodes with `H < 24`, use tuples `(idx[:c], idx[c:c+H])` at several offsets inside each episode.
  - Do not use mosaic with tuples (upstream warning, `auto_ts.py:138`).
- Our outer walk-forward backtest is still the final judge. AutoTS validation is only the inner model-selection loop, so the two must not share test windows with the final holdout.

### b. Competition metric

`custom_metric(A, F, df_train, prediction_interval)` receives numpy arrays in **input units** (A and F have shape `H × n_series`; `df_train` is the raw training slice). It must return a length-`n_series` array where lower is better.

- **Cross-sectional rank IC.**
  - Compute `IC = spearman(F[h] - df_train[-1], A[h] - df_train[-1])` on log prices.
  - Broadcast `-IC` to every series.
  - This is implemented and verified in `bench.py` (benchmark 5).
- **Top-k return.**
  - Take the realized mean return of the k highest-forecast series.
  - Broadcast its negative.
- **Direction hit rate.** The built-in `oda` metric already measures it (`metrics.py:746`). Use `oda_weighting`, no custom code.
- **Weighting.** Recommended starting point: `{"custom_weighting": 2, "mae_weighting": 1, "oda_weighting": 1}`.
  - `smape` has a denominator near zero on returns and is uninformative on log levels. Give it weight 0 (it is always computed; `generate_score` still uses it as a scaler for runtime/contour/oda terms, L3421).
- **Per-series scores.** A broadcast cross-sectional score is identical across series, so it cannot rank per-stock models. Horizontal selection on a per-series custom score needs the patch in row 2.

### c. Search space

Recommended starting pool (all under 2.5 s per 150-series fit, §3):

- `model_list`
  - `LastValueNaive`, `AverageValueNaive`, `SeasonalNaive`, `ETS`
  - `ARIMA` with small p/q
  - `DatepartRegression`, `WindowRegression`, `MultivariateRegression`, `SectionalMotif`, `BasicLinearModel`
  - Keep `ARIMA` and `ETS` because they are among the few with model-based intervals or trend damping.
- `transformer_list`: a dict such as `{"DifferencedTransformer": 1, None: 0.5, "ClipOutliers": 0.3, "StandardScaler": 0.3, "AlignLastValue": 0.3, "Detrend": 0.2}`
  - Also set `transformer_max_depth=2`.
  - This dict is applied to random and genetic transformers and prunes the initial template (L426–466).
- **Regressor pool.**
  - Random sklearn params come from module-level dicts that include xgboost, SVM and RadiusNeighbors.
  - Option 1: an in-tree patch (P2) that restricts them to Ridge/ElasticNet/LightGBM/HistGradientBoost and caps `max_windows`.
  - Option 2: override the dicts at runtime in our wrapper before building AutoTS.
  - Either way, log it in `UPSTREAM.md`.
- **Alternative that avoids random parameter noise.**
  - Use `initial_template=<curated DataFrame>`, `max_generations=0` or a small number, `models_to_validate=0.99`.
  - Benchmark 2 is exactly this setup.
- **Adding a model.**
  - Add an `elif` in `ModelMonster` (`auto_model.py:166–740`) and a name in `model_list.py`.
  - Implement `fit`, `predict`, `get_new_params` and `get_params` on a `ModelObject` subclass (`models/base.py`).

### d. Returns vs prices

- **Input clock.**
  - Reindex each cutoff's panel to a synthetic contiguous business-day index (trading-day ordinal).
  - Then `forecast_length=H` means H trading days and there are no holiday NaN rows.
  - Calendar-based models (`DatepartRegression`, holiday features) lose real-date meaning, which is acceptable for daily stock returns.
- **Input series.**
  - Use log(Adj Close), optionally rebased by the series' first value (causal). MAE is then roughly log-return error and comparable across stocks.
  - Raw prices make MAE favor high-priced stocks, unless `weights` is used (`fit(weights=...)`, L1130–1149).
- **Inside a template**, `DifferencedTransformer` models increments and is inverted back to levels. `PctChangeTransformer` does the same for simple returns.
  - `PctChangeTransformer.fit` anchors on a NaN-filled tail (`transform.py:1776`), so keep `fillna="ffill"`.
- **Feeding returns directly** (series = daily returns) is possible, but level-based metrics (smape, contour, spl scaler) become noisy.
  - The log-level plus difference route keeps all built-in metrics meaningful.
- **Relative or excess return.**
  - Subtract the log market index (0050 or ^TWII from the same data folder) before fitting.
  - Or compute the cross-sectional demeaned forecast after predicting. Rank IC is invariant to the latter.

### e. Horizontal / mosaic

- **Selection.** `horizontal-max` picks each series' `idxmin` over the models that went through all validations (`ensemble.py:1590`).
  - Mosaic picks per series and per forecast step (`ensemble.py:2140, 2269`).
- **Prediction cost.** At predict time, every distinct component model is refit (`model_forecast` ensemble branch, `auto_model.py:1392–1525`).
  - Models in `no_shared` fit only on their assigned series (`auto_model.py:1537–1547`).
  - So cost ≈ sum of the distinct component costs, with naive and ETS models cheap.
- **Validation cost.** An extra shifted-start pass runs with `horizontal_ensemble_validation=True` (L1648–1660).
- **Data need.** Per-series selection from 2–4 windows of H days is a very noisy choice for financial returns.
  - Use at least 8 episodes, or pool by `horizontal-profile`.
  - Treat per-stock selection as a hypothesis to test in the NAV backtest, not a default.

### f. Expected return, uncertainty, confidence

For each stock and horizon h, from `pred = model.predict()` on log levels:

- `mu_h = pred.forecast.iloc[h-1] - last_log_price`
- `band_h = pred.upper_forecast.iloc[h-1] - pred.lower_forecast.iloc[h-1]`
  - This is a heuristic width in log units.
  - Convert it via the normal quantile of `prediction_interval` only after calibration.
- Calibrated `sigma_h`: the RMS of `(A - F)` at step h across validation windows.
  - Source: `model.retrieve_validation_forecasts()` (L3716), which refits the chosen model on each window, or the stored per-series metrics.
- `confidence = mu_h / sigma_h`, or the empirical hit rate from `oda` per series (`initial_results.per_series_oda`).
- `predict(prediction_interval=[0.5, 0.8, 0.9])` returns several bands (L2513) and refits once per interval.

### g. Future regressors and causality

- In validation, the regressor for the test window is sliced from the same full regressor frame (L2268–2277). In predict, the caller passes the future values.
  - Any regressor that is only known after the fact (same-day index return, SOX close of the forecast day) therefore leaks.
- **Safe regressors:**
  - calendar features;
  - features lagged by at least H trading days;
  - values known at the D-1 close and held constant over the horizon, built from data ≤ cutoff.
- US overnight data (D-1 US close before the TW D open) is causal for a D-open decision. It is still only known one day ahead, so it cannot be a regressor for steps 2..H without being carried forward.
- `regression_type="User"` models fail if no regressor is passed. Keep them out of the pool unless regressors are provided.

### h. Runtime knobs

- `n_jobs`: parallel per-series fitting inside ETS/ARIMA/etc. Default is 0.5 × cores = 6 on this M2 Max.
- `max_generations`: 0 = evaluate only the initial template; each generation adds about `len(model_list) × 5` candidates.
- `models_to_validate`: 0.99 means all; the float fraction applies to evaluated models.
- `subset`: evaluate on a random subset of series per window (random per window unless mosaic).
- `skip_slow_models_seconds`, `generation_timeout` (minutes): hard caps.

### i. Reproducibility

- Seeding: `random_seed` seeds `random` and `np.random` in `__init__` and `fit` (L302, L1321–1324), and is passed to models.
  - Use explicit model lists and `PYTHONHASHSEED=0`.
  - Pin the env via `requirements-autots.txt` and the vendored commit.
- **Frozen daily model:**
  - After a search: `model.export_template("best.csv", models="best", n=1)` (L2576).
  - Daily: `AutoTS(...).import_best_model("best.csv")`, then `.fit_data(df)`, then `.predict()`. There is no search (L2904, L1044); benchmark 1 does this.
  - Record the template CSV hash, the data cutoff and the AutoTS commit in the experiment registry.
- **Warm start the next search:** `import_template(file, method="add_on", force_validation=True)` (L2822). This is upstream's `production_example.py` "evolve" pattern.
- **`update_fit` caveat.** For models in `update_fit` (`model_list.py:461`), `fit_data` reuses the fitted model and does not re-fit the transformer (`auto_model.py:1028`). For a clean daily refit, set `model.model = None` first.

## 3. Runtime benchmarks

Environment: Apple M2 Max (12 cores, 32 GB), `.venv` Python 3.12.10, `n_jobs=6`, `PYTHONHASHSEED=0`, `random_seed=7`, `verbose=0`.

Panel:

- 150 universe stocks (`data/reference/universe_competition_20260731.csv`), `Adj Close` from `data/yahoo_daily/v3_20260923/*.parquet`.
- Last 750 trading days, log-transformed, reindexed to `asfreq("B")`. That gives 804 rows × 150 series with 8.4% NaN (holidays and late listings).
- Data ends 2026-09-23.

The benchmark script was scratch work (`bench.py`, not committed). The configurations are listed below so the numbers can be reproduced.

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

Run 5 checks that the adaptation hooks work end to end:

- **Test windows.** Test-window starts were 2026-09-17, 08-14, 07-13, 06-09 (H=5) and 2026-08-21, 07-20, 06-16, 05-13 (H=24). The windows are chronological and do not overlap.
- **Aggregation.** `custom` (−rank IC) was aggregated as the mean over rounds.
- **Ranking at H=24.**

| Model | Mean rank IC | oda |
|---|---|---|
| WindowRegression | +0.062 | 0.55 |
| ARIMA | −0.049 | 0.47 |
| LastValueNaive | 0 (constant forecast) | 0.01 |

- **Caveat.** These are 4 episodes on one recent period. The numbers only show the plumbing works. They are not evidence of skill.

### First-attempt failure

The first attempt at run 3 included ARIMA and the default regressor pool.

- **xgboost.** Random candidates failed on missing `xgboost`.
- **Stall.** One generation-1 candidate kept all 6 workers busy for more than 12 minutes. The run was killed.
  - That run had no `current_model_file`, so the stalled model is not identified.
  - The model list contained random-param ARIMA (p/q up to 12+).
- **Rerun changes.** ARIMA was removed from the random list and the regressor pools were overridden at runtime to Ridge/ElasticNet/LightGBM/HistGradientBoost.
- **Conclusion.** Random search must be bounded: curated params, `skip_slow_models_seconds`, `generation_timeout`, and `current_model_file` for diagnosis.

### Budget for a walk-forward backtest with 150–200 refits

| Per-refit strategy | Per refit | 200 refits |
|---|---|---|
| Frozen template, refit only | 0.2–2 s | under 7 min |
| Fixed 8–10 model template, 3 windows | 7–12 s | 25–40 min |
| Same, 8–12 episodes | about 25–45 s (linear in windows) | 1.5–2.5 h |
| 1-generation random search, 3 windows | 3–5 min | 10–16 h |
| Plus horizontal-max | 11–12 min | about 38 h |

Recommendation:

- Do the backtest with curated templates and custom episode validation (rows 2–3). Refits are independent, so they parallelize across cutoffs. Use `n_jobs=1` inside AutoTS with 6–12 processes outside.
- Keep genetic search and horizontal ensembles for a few offline sweeps, each on data up to a single cutoff.
- Before using horizontal ensembles in the loop, trim `horizontal_post_processors` with a local patch. That removes most of their cost.
