"""Technical Momentum (docs/technical_momentum_spec.md): 19 indicators rerank Mom20's top 40.

Indicator panels (calendar x symbol) are built once per process from the
market since DATA_START with rolling or recursive (adjust=False) operations
only, so row t depends on rows <= t. The strategy reads the row of the view
date (D-1). Prices are action-neutral adjusted (section 4).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields

import numpy as np
import pandas as pd

from autots_strategy import portfolio
from competition.backtest import PortfolioState
from competition.data import AsOfView, MarketData, load_market
from competition.rules import CompetitionRules
from lgbm_strategy.features import signal_price
from research.baselines import MomentumConfig, momentum_score

DATA_START = '2014-01-01'
SHORTLIST = 40
MIN_SHARE = .8
FAMILIES = {
    'trend': ('macd_hist', 'macd_slope', 'adx_dir', 'ema_slope', 'ema_align', 'roc_accel', 'dist_high60',
              'channel20', 'clv5'),
    'exhaustion': ('rsi14', 'stoch_k', 'pct_b', 'bandwidth', 'mfi14'),
    'volatility': ('atr_pct', 'vol_ratio'),
    'volume': ('obv_slope', 'abn_volume', 'pv_corr'),
}
BAD = {'exhaustion', 'volatility'}           # high = bad -> 1 - rank
INDICATORS = tuple(i for members in FAMILIES.values() for i in members)
FAMILY_OF = {i: f for f, members in FAMILIES.items() for i in members}
_PANELS: dict = {}


def _roll(frame: pd.DataFrame, window: int):
    return frame.rolling(window, min_periods=math.ceil(MIN_SHARE * window))


def _ema(frame: pd.DataFrame, span: int) -> pd.DataFrame:
    return frame.ewm(span=span, adjust=False, min_periods=span).mean()


def _wilder(frame: pd.DataFrame, n: int) -> pd.DataFrame:
    return frame.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adjusted_bars(market: MarketData) -> dict[str, pd.DataFrame]:
    """Adjusted O/H/L/C (section 4) and volume on tradable days."""
    p = signal_price(market.ret)
    factor = p / market.close
    bars = {k: getattr(market, k) * factor for k in ('open', 'high', 'low')}
    bars['close'] = p.where(market.close.notna())
    bars['volume'] = market.volume.where(market.valid)
    return bars


def indicators(market: MarketData) -> dict[str, pd.DataFrame]:
    b = adjusted_bars(market)
    c, h, l, v = b['close'], b['high'], b['low'], b['volume']
    prev = c.shift()
    lr = np.log(c / prev)
    out = {}
    macd = _ema(c, 12) - _ema(c, 26)
    hist = macd - _ema(macd, 9)
    out['macd_hist'] = hist / c
    out['macd_slope'] = (hist - hist.shift(5)) / c
    tr = np.fmax(np.fmax(h - l, (h - prev).abs()), (l - prev).abs())
    up, down = h - h.shift(), l.shift() - l
    plus_dm = up.where((up > down) & (up > 0), 0.).where(up.notna() & down.notna())
    minus_dm = down.where((down > up) & (down > 0), 0.).where(up.notna() & down.notna())
    atr = _wilder(tr, 14)
    plus_di, minus_di = 100 * _wilder(plus_dm, 14) / atr, 100 * _wilder(minus_dm, 14) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    out['adx_dir'] = _wilder(dx, 14) * np.sign(plus_di - minus_di)
    ema20 = _ema(c, 20)
    out['ema_slope'] = ema20 / ema20.shift(5) - 1
    out['ema_align'] = ema20 / _ema(c, 50) - 1
    roc10 = c / c.shift(10) - 1
    out['roc_accel'] = roc10 - roc10.shift(10)
    out['dist_high60'] = c / _roll(h, 60).max() - 1
    hi20, lo20 = _roll(h, 20).max(), _roll(l, 20).min()
    out['channel20'] = (c - lo20) / (hi20 - lo20)
    rng = (h - l).where(lambda x: x > 0)
    out['clv5'] = _roll(((c - l) - (h - c)) / rng, 5).mean()
    delta = c - prev
    gain, loss = _wilder(delta.clip(lower=0), 14), _wilder((-delta).clip(lower=0), 14)
    out['rsi14'] = 100 - 100 / (1 + gain / loss)
    hi14, lo14 = _roll(h, 14).max(), _roll(l, 14).min()
    out['stoch_k'] = _roll((c - lo14) / (hi14 - lo14), 3).mean()
    ma20, sd20 = _roll(c, 20).mean(), _roll(c, 20).std(ddof=0)
    out['pct_b'] = (c - (ma20 - 2 * sd20)) / (4 * sd20)
    out['bandwidth'] = 4 * sd20 / ma20
    tp = (h + l + c) / 3
    flow = tp * v
    tp_up, tp_down = tp > tp.shift(), tp < tp.shift()
    pos, neg = _roll(flow.where(tp_up, 0.).where(flow.notna()), 14).sum(), \
        _roll(flow.where(tp_down, 0.).where(flow.notna()), 14).sum()
    out['mfi14'] = 100 - 100 / (1 + pos / neg)
    out['atr_pct'] = atr / c
    out['vol_ratio'] = _roll(lr, 10).std() / _roll(lr, 60).std()
    obv = (np.sign(delta) * v).fillna(0.).cumsum()
    out['obv_slope'] = (obv - obv.shift(20)) / (20 * _roll(v, 20).mean())
    out['abn_volume'] = _roll(v, 5).mean() / _roll(v, 60).mean()
    dlogv = np.log(v.where(v > 0)).diff()
    out['pv_corr'] = _roll(lr, 20).corr(dlogv)
    return {k: f.replace([np.inf, -np.inf], np.nan) for k, f in out.items()}


def panels() -> dict[str, pd.DataFrame]:
    if not _PANELS:
        _PANELS.update(indicators(load_market().since(DATA_START)))
    return _PANELS


# ---------------------------------------------------------------- scoring

def _rank(values: pd.Series, names, bad: bool = False) -> pd.Series:
    """Percentile rank among ``names`` (higher is better; ``bad`` ranks low values first); missing -> 0.5."""
    return values.reindex(names).replace([np.inf, -np.inf], np.nan).rank(pct=True, ascending=not bad).fillna(.5)


def technical_score(date, names, variant: str, frames: dict | None = None) -> pd.Series:
    """variant: 'composite', 'family:<name>' or 'ind:<code>' (section 6)."""
    frames = frames if frames is not None else panels()

    def indicator_rank(code):
        row = frames[code].loc[date] if date in frames[code].index else pd.Series(dtype=float)
        return _rank(row, names, bad=FAMILY_OF[code] in BAD)

    def family_rank(family):
        mean = sum(indicator_rank(i) for i in FAMILIES[family]) / len(FAMILIES[family])
        return mean.rank(pct=True)

    if variant == 'composite':
        return sum(family_rank(f) for f in FAMILIES) / len(FAMILIES)
    kind, name = variant.split(':')
    return family_rank(name) if kind == 'family' else indicator_rank(name)


def rerank(view: AsOfView, variant: str, frames: dict | None = None) -> tuple[pd.Series, list]:
    """Final score: Mom20's top 40 ordered by the technical score first, the rest in Mom20 order."""
    mom = momentum_score(view, MomentumConfig())
    order = portfolio.ranked(mom)
    top = order[:SHORTLIST]
    mom_pct = mom.rank(pct=True)
    tech = technical_score(view.date, top, variant, frames)
    score = mom_pct.copy()
    score[top] = 1 + tech + 1e-6 * mom_pct[top]        # Mom20 order breaks technical ties
    return score, top


VARIANTS = ('composite', *(f'family:{f}' for f in FAMILIES), *(f'ind:{i}' for i in INDICATORS))


@dataclass(frozen=True)
class TechConfig:
    variant: str = 'composite'
    portfolio: portfolio.PortfolioConfig = field(default_factory=portfolio.PortfolioConfig)

    def __post_init__(self):
        if self.variant not in VARIANTS:
            raise ValueError(f'Unknown technical variant {self.variant}')

    @classmethod
    def from_dict(cls, raw: dict) -> 'TechConfig':
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f'Unknown technical keys {sorted(unknown)}')
        return cls(variant=raw.get('variant', 'composite'),
                   portfolio=portfolio.PortfolioConfig.from_dict(raw.get('portfolio', {})))


class TechStrategy:
    def __init__(self, config: TechConfig, rules: CompetitionRules):
        self.c, self.rules, self.log = config, rules, []

    def decide(self, view: AsOfView, state: PortfolioState) -> dict:
        score, top = rerank(view, self.c.variant)
        missing = {i: int(panels()[i].loc[view.date].reindex(top).isna().sum()) for i in INDICATORS} \
            if view.date in panels()['rsi14'].index else {}
        self.log.append(dict(date=str(state.date.date()), shortlist=len(top),
                             missing={k: v for k, v in missing.items() if v}))
        return portfolio.target_weights(score, state.weights, state.sessions_remaining, self.rules,
                                        self.c.portfolio)
