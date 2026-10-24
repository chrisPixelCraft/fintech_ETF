"""Active Share against every active ETF's top 15 (rules p8 六(十三); docs/production_spec.md section 7).

    AS = 1/2 * sum_i |w_i(portfolio top 15) - w_i(ETF top 15)|   over the union of names

The official method has open points (docs/task.md U4), so this module is
conservative on each and says so in its output:
- Normalisation: AS is computed with raw NAV weights and with each top 15
  rescaled to sum to 1; the lower value is used.
- Near-equal weights: the portfolio's top 15 by value depends on price drift,
  so every holding within TIE_BAND of the 15th weight may enter it; the worst
  case (overlap with the ETF first) is used.
- Missing ETF data never counts as a pass: the status is ACTIVE_SHARE_UNVERIFIED.

Holdings input: CSV with columns etf, as_of (YYYY-MM-DD), ticker (4 digits),
weight (fraction of the fund, or percent when an ETF's weights sum above 1.5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math

import pandas as pd

OFFICIAL_MIN = .15          # rules: Active Share must not be below 15%
INTERNAL_MIN = .20          # internal safety margin (not a rule)
TIE_BAND = .8               # holdings with weight >= 0.8 x the 15th weight may enter the top 15
TOP = 15
MAX_AGE_DAYS = 7            # ETF top-15 data older than this is stale
PASS, CAUTION, INVALID, UNVERIFIED = 'PASS', 'CAUTION', 'INVALID', 'ACTIVE_SHARE_UNVERIFIED'


@dataclass(frozen=True)
class EtfTop10:
    etf: str
    as_of: str
    weights: dict           # ticker -> fraction of fund


def load_etf_holdings(path: Path | str) -> dict[str, EtfTop10]:
    """Latest top 15 per ETF from the holdings CSV."""
    frame = pd.read_csv(path, dtype={'etf': str, 'ticker': str})
    missing = {'etf', 'as_of', 'ticker', 'weight'} - set(frame.columns)
    if missing:
        raise ValueError(f'ETF holdings file lacks {sorted(missing)}')
    out = {}
    for etf, rows in frame.groupby('etf'):
        latest = rows[rows.as_of == rows.as_of.max()]
        weights = latest.groupby('ticker').weight.sum().astype(float)
        if weights.sum() > 1.5:                  # percent
            weights = weights / 100
        top = weights.sort_values(ascending=False).iloc[:TOP]
        out[etf] = EtfTop10(etf, str(latest.as_of.iloc[0]), {str(t): float(w) for t, w in top.items()})
    return out


def _as(portfolio: dict, benchmark: dict) -> float:
    names = set(portfolio) | set(benchmark)
    return .5 * sum(abs(portfolio.get(n, 0.) - benchmark.get(n, 0.)) for n in names)


def _normalised(weights: dict) -> dict:
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()} if total > 0 else {}


def worst_case_top10(weights: dict, against: dict) -> dict:
    """The portfolio top 15 by weight, with near-ties resolved toward overlap with ``against``."""
    ranked = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) <= TOP:
        return dict(ranked)
    tenth = ranked[TOP - 1][1]
    sure = [kv for kv in ranked if kv[1] > tenth / TIE_BAND]
    band = [kv for kv in ranked if tenth * TIE_BAND <= kv[1] <= tenth / TIE_BAND]
    band.sort(key=lambda kv: (kv[0] not in against, -kv[1], kv[0]))
    return dict((sure + band)[:TOP])


def active_share(weights: dict, etf: EtfTop10) -> float:
    """Conservative Active Share of a {ticker: weight} portfolio against one ETF top 15."""
    top = worst_case_top10(weights, etf.weights)
    return min(_as(top, etf.weights), _as(_normalised(top), _normalised(etf.weights)))


@dataclass
class ActiveShareReport:
    status: str
    minimum: float | None = None
    closest_etf: str | None = None
    by_etf: dict = field(default_factory=dict)
    stale: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    incomplete: list = field(default_factory=list)
    invalid_data: list = field(default_factory=list)
    assumptions: tuple = ('min(raw, normalised)', f'near-tie band {TIE_BAND}', 'missing ETF -> unverified')

    def as_dict(self) -> dict:
        return dict(status=self.status, minimum=self.minimum, closest_etf=self.closest_etf,
                    distance_to_official=None if self.minimum is None else self.minimum - OFFICIAL_MIN,
                    by_etf=self.by_etf, stale=self.stale, missing=self.missing,
                    incomplete=self.incomplete, invalid_data=self.invalid_data,
                    top_n=TOP, official_minimum=OFFICIAL_MIN,
                    method='heuristic_min_raw_normalised', official_method_confirmed=False,
                    assumptions=list(self.assumptions))


def check(weights: dict, etfs: dict[str, EtfTop10] | None, required: list[str], trade_date: str) -> ActiveShareReport:
    """Status of a {ticker: weight} portfolio against every required ETF."""
    etfs = etfs or {}
    required = sorted(set(required))
    missing = sorted(e for e in required if e not in etfs)
    day = pd.Timestamp(trade_date)
    stale, incomplete, invalid = [], [], []
    usable = {}
    for e in required:
        if e not in etfs:
            continue
        x = etfs[e]
        try:
            age = (day - pd.Timestamp(x.as_of)).days
            if pd.isna(age) or age < 0:
                invalid.append(e)
            elif age > MAX_AGE_DAYS:
                stale.append(e)
            if (not x.weights or any(not math.isfinite(w) or w <= 0 or w > 1
                                    for w in x.weights.values()) or sum(x.weights.values()) > 1 + 1e-6):
                invalid.append(e)
            if len(x.weights) < TOP:
                incomplete.append(e)
            if e not in invalid:
                usable[e] = x
        except (ValueError, TypeError):
            invalid.append(e)
    if not weights or any(not math.isfinite(w) or w < 0 for w in weights.values()):
        invalid.append('PORTFOLIO')
    by_etf = {e: active_share(weights, x) for e, x in usable.items()}
    closest = min(by_etf, key=by_etf.get) if by_etf else None
    minimum = by_etf[closest] if closest else None
    # An incomplete top-10 snapshot cannot establish either verdict for the top-15 rule.
    if not required or missing or stale or incomplete or invalid or minimum is None:
        status = UNVERIFIED
    elif minimum < OFFICIAL_MIN:
        status = INVALID
    else:
        status = CAUTION if minimum < INTERNAL_MIN else PASS
    return ActiveShareReport(status, minimum, closest, by_etf, sorted(stale), missing,
                             sorted(incomplete), sorted(set(invalid)))


def repair(scores: pd.Series, build, etfs: dict[str, EtfTop10], to_ticker, max_steps: int = 25):
    """Drop the lowest-scored overlapping name until every ETF clears INTERNAL_MIN.

    ``build(scores) -> {symbol: weight}`` recomputes the target with the unchanged portfolio rules, so each
    dropped name is replaced by the next-ranked one (minimum momentum loss). Returns (weights, dropped).
    """
    dropped = []
    weights = build(scores)
    for _ in range(max_steps):
        tickers = {to_ticker(s): w for s, w in weights.items()}
        values = {e: active_share(tickers, x) for e, x in etfs.items()}
        worst = min(values, key=values.get) if values else None
        if worst is None or values[worst] >= INTERNAL_MIN:
            return weights, dropped
        overlap = [s for s in weights if to_ticker(s) in etfs[worst].weights
                   and to_ticker(s) in worst_case_top10(tickers, etfs[worst].weights)]
        if not overlap:
            return weights, dropped
        victim = min(overlap, key=lambda s: (scores.get(s, float('-inf')), s))
        dropped.append(victim)
        scores = scores.drop(victim, errors='ignore')
        weights = build(scores)
    return weights, dropped
