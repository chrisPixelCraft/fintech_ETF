"""Causal V5 observations (restored from V4; passed future-corruption tests); a row dated D is usable only for decisions after D."""
from __future__ import annotations
import numpy as np
import pandas as pd
from src.strategy_24d import build_features as frozen_features, build_config

HORIZONS = (1, 3, 5, 10, 15, 20, 30, 60)
EMA_SPANS = (5, 10, 20, 50, 100, 200)


def build_features(daily, warmup_sessions=200):
    """Reuse proven nominal/action-quality segmentation, then add V5 indicators.

    Corporate-action-neutral signal prices are distinct from nominal sizing
    closes. Missing observations never receive backward-filled indicators.
    """
    config = build_config()
    config['warmup_sessions'] = warmup_sessions
    panel = frozen_features(daily, config)
    frames = []
    for _, source in panel.groupby(['symbol', 'signal_history_segment'], sort=True):
        rows = source.copy()
        valid = rows.signal_available & rows.signal_price.notna()
        obs = rows.loc[valid]
        p = obs.signal_price
        r = p.pct_change(fill_method=None)
        # Split-adjust volume only using actions already observed.
        volume = rows.volume / rows.split.cumprod()
        v = volume.loc[valid]
        computed = {}
        for h in HORIZONS:
            computed[f'R{h}'] = p.pct_change(h, fill_method=None)
        for span in EMA_SPANS:
            ema = p.ewm(span=span, adjust=False, min_periods=span).mean()
            computed[f'EMA{span}'] = ema
            computed[f'price_ema{span}'] = p / ema - 1
            computed[f'ema_slope{span}'] = ema.pct_change(5, fill_method=None)
        computed['ema_cross'] = (computed['EMA5'] > computed['EMA20']).astype(float)
        computed['volume_ratio'] = v.rolling(5).mean() / v.shift().rolling(20).mean().replace(0, np.nan)
        computed['volume_percentile'] = v.rolling(20).rank(pct=True)
        computed['volume_acceleration'] = v.rolling(5).mean() / v.shift(5).rolling(5).mean().replace(0, np.nan) - 1
        computed['vol5'] = r.rolling(5).std()
        computed['vol20'] = r.rolling(20).std()
        computed['vol_ratio'] = computed['vol5'] / computed['vol20'].replace(0, np.nan)
        computed['momentum_quality'] = computed['R20'] / computed['vol20'].replace(0, np.nan)
        computed['positive_day_ratio'] = r.gt(0).where(r.notna()).rolling(20).mean()
        computed['drawdown20'] = p / p.rolling(20).max() - 1
        clean_range = ((obs.high - obs.low) / obs.close).where(obs.high.ge(obs.low) & obs.low.gt(0))
        computed['range20'] = clean_range.rolling(20).mean()
        for name, values in computed.items():
            rows[name] = values.reindex(rows.index)
        rows['feature_ready'] = rows.ready & rows.signal_available & rows.R60.notna() & rows.EMA200.notna()
        frames.append(rows)
    return pd.concat(frames).sort_values(['date', 'symbol']).reset_index(drop=True)


def history_at(panel, decision_date):
    """Return the last prior-session cross section, never each name's stale row."""
    past = panel.loc[pd.to_datetime(panel.date) < pd.Timestamp(decision_date)]
    if past.empty:
        return panel.iloc[:0].copy()
    return past.loc[past.date.eq(past.date.max())].copy()
