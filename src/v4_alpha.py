"""Seven transparent alpha experts and the V4 momentum structural baseline."""
import numpy as np
import pandas as pd

EXPERT_NAMES = ('short_momentum', 'medium_momentum', 'long_momentum', 'trend',
                'volume_momentum', 'mean_reversion', 'risk_adjusted', 'ridge')


def momentum_score(rows, horizons=(5, 10, 20), family='full'):
    frame = rows.set_index('symbol', drop=False) if 'symbol' in rows.columns else rows
    score = sum(frame[f'R{int(h)}'].rank(pct=True) for h in horizons) / len(horizons)
    if family in ('trend', 'full'):
        score = score + .2 * frame.price_ema20.rank(pct=True)
    if family in ('volume', 'full'):
        score = score + .2 * frame.volume_ratio.rank(pct=True)
    if family in ('volatility', 'full'):
        score = score - .2 * frame.vol20.rank(pct=True)
    if family not in ('pure', 'momentum', 'trend', 'volume', 'volatility', 'full'):
        raise ValueError('Unknown momentum score family')
    return score.rename('score')


def expert_scores(rows):
    f = rows.set_index('symbol', drop=False) if 'symbol' in rows.columns else rows
    return pd.DataFrame({
        'short_momentum': (f.R3 / 3 + f.R5 / 5) / 2,
        'medium_momentum': (f.R10 / 10 + f.R20 / 20) / 2,
        'long_momentum': (f.R30 / 30 + f.R60 / 60) / 2,
        'trend': (f.price_ema20 / 20 + f.ema_slope20 / 5) / 2,
        'volume_momentum': f.R10 / 10 * f.volume_ratio.clip(.5, 2),
        'mean_reversion': -f.R3 / 3,
        'risk_adjusted': f.R20 / 20 * (.02 / f.vol20.clip(lower=.005)).clip(.25, 2),
    }, index=f.index).replace([np.inf, -np.inf], np.nan)
