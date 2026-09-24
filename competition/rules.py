"""Competition constraints as one frozen object.

Source of truth: data/reference/competition_rules.json (page citations inside)
and docs/task.md. The cash ceiling is strict (< 25%, task.md U3).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / 'data/reference/competition_rules.json'
UNIVERSE_PATH = ROOT / 'data/reference/universe_competition_20260731.csv'


@dataclass(frozen=True, eq=False)
class CompetitionRules:
    initial_capital: float
    episode_sessions: int
    min_positions: int
    max_positions: int
    max_weight: float
    ticker_max_weights: Mapping[str, float]
    cash_min: float
    cash_max_exclusive: float
    lot_size: int
    commission_buy: float
    commission_sell: float
    tax_sell: float
    passive_grace_days: int
    warnings_for_disqualification: int

    def cap(self, symbol: str) -> float:
        """Single-name cap by ticker (``2330.TW`` -> 0.25, others 0.10)."""
        return self.ticker_max_weights.get(str(symbol).split('.')[0], self.max_weight)

    @property
    def round_trip_cost(self) -> float:
        return self.commission_buy + self.commission_sell + self.tax_sell


def load_rules(path: Path | str = RULES_PATH) -> CompetitionRules:
    raw = json.loads(Path(path).read_text())
    p, e, v = raw['portfolio'], raw['execution'], raw['violations']
    if p['cash_max_exclusive'] > .25 or p['general_stock_max_weight'] > .10:
        raise ValueError('Rules file loosens official limits')
    return CompetitionRules(
        initial_capital=float(raw['initial_capital_twd']),
        episode_sessions=int(raw['contest']['trading_days']),
        min_positions=int(p['minimum_positions']),
        max_positions=int(p['maximum_positions']),
        max_weight=float(p['general_stock_max_weight']),
        ticker_max_weights={str(k): float(x) for k, x in p['ticker_max_weights'].items()},
        cash_min=float(p['cash_min']),
        cash_max_exclusive=float(p['cash_max_exclusive']),
        lot_size=int(p['order_lot_size_shares']),
        commission_buy=float(e['commission_buy_rate']),
        commission_sell=float(e['commission_sell_rate']),
        tax_sell=float(e['transaction_tax_sell_rate']),
        passive_grace_days=int(v['passive_weight_drift_grace_trading_days']),
        warnings_for_disqualification=int(v['warnings_for_disqualification']),
    )
