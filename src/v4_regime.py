"""Small Taiwan-only regime summary, evaluated on a prior-session cross section."""
import numpy as np


def classify_regime(rows):
    frame = rows.loc[rows.feature_ready] if 'feature_ready' in rows else rows
    if frame.empty:
        return dict(regime='neutral', breadth=0.5, median_R5=0., median_R20=0.,
                    dispersion=0., market_volatility=0., above_EMA20=.5, above_EMA50=.5)
    above = float(frame.price_ema20.gt(0).mean())
    medium = float(frame.R20.median())
    regime = 'risk_on' if above > .6 and medium > 0 else ('risk_off' if above < .4 and medium < 0 else 'neutral')
    return dict(regime=regime, breadth=float(frame.R5.gt(0).mean()), median_R5=float(frame.R5.median()),
                median_R20=medium, dispersion=float(frame.R20.std(ddof=0)),
                market_volatility=float(frame.vol20.median()), above_EMA20=above,
                above_EMA50=float(frame.price_ema50.gt(0).mean()))
