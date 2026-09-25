"""Portfolio state: the organizer's settled holdings (source of truth) and the local shadow ledger.

The organizer reports holdings but not NAV or cash (D-Plan guide ⑥), so NAV
and cash come from the local ledger, which settles exactly like the backtest
(competition.ledger.settle). The two are reconciled before every decision;
a mismatch is never resolved silently in favour of the local book.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from competition import execution, ledger
from competition.backtest import ExecutionConfig
from competition.data import MarketData
from competition.planner import PlannerPolicy
from competition.rules import UNIVERSE_PATH, CompetitionRules

SHARE_TOL = 1e-6
TICKER_KEYS = ('ticker', 'stock_id', 'symbol', 'code', '股票代號', '代號')
SHARE_KEYS = ('shares', 'quantity', 'qty', 'volume', '持有股數', '股數')


def ticker_map(universe_path: Path | str = UNIVERSE_PATH) -> dict[str, str]:
    """4-digit ticker -> panel symbol (e.g. 2330 -> 2330.TW)."""
    frame = pd.read_csv(universe_path, dtype={'ticker': str})
    return dict(zip(frame.ticker, frame.yahoo_symbol))


def symbol_to_ticker(symbol: str) -> str:
    return str(symbol).split('.')[0]


@dataclass(frozen=True)
class SettledBook:
    """Holdings and cash after the close of ``date`` (the local shadow ledger)."""
    date: str
    holdings: dict = field(default_factory=dict)     # symbol -> shares
    cash: float = 0.
    receivable: float = 0.                           # accrued cash dividends, credited at the end
    marks: dict = field(default_factory=dict)        # symbol -> last valid close
    cap_ages: dict = field(default_factory=dict)
    warnings: int = 0

    @property
    def nav(self) -> float:
        return self.cash + sum(q * self.marks[s] for s, q in self.holdings.items())

    def to_ledger(self) -> ledger.Book:
        return ledger.Book(holdings=dict(self.holdings), cash=self.cash, receivable=self.receivable,
                           marks=dict(self.marks), cap_ages=dict(self.cap_ages), warnings=self.warnings)

    @classmethod
    def from_ledger(cls, date, book: ledger.Book) -> 'SettledBook':
        return cls(date=str(pd.Timestamp(date).date()), holdings=dict(book.holdings), cash=book.cash,
                   receivable=book.receivable, marks=dict(book.marks), cap_ages=dict(book.cap_ages),
                   warnings=book.warnings)

    def save(self, path: Path | str):
        Path(path).write_text(json.dumps(asdict(self), indent=1, sort_keys=True))

    @classmethod
    def load(cls, path: Path | str) -> 'SettledBook':
        return cls(**json.loads(Path(path).read_text()))


def initial_book(rules: CompetitionRules, date: str) -> SettledBook:
    """All cash before the first trading day."""
    return SettledBook(date=date, cash=rules.initial_capital)


def settle_day(book: SettledBook, day, orders: dict, market: MarketData, rules: CompetitionRules,
               execution_config: ExecutionConfig = ExecutionConfig(),
               policy: PlannerPolicy = PlannerPolicy()) -> tuple[SettledBook, dict, list, list]:
    """Advance the shadow ledger through trade date ``day`` exactly as competition.backtest does."""
    t = market.position(day)
    prev = market.calendar[t - 1]
    prev_close = market.close.loc[prev].copy()
    for s in book.holdings:
        if not pd.notna(prev_close.get(s)):
            prev_close[s] = book.marks[s]
    price, source = execution.fill_prices(market, day, sorted(orders), execution_config.mode, execution_config.proxy)
    new, record, trades, issues = ledger.settle(
        book.to_ledger(), day, orders, price, source, market.close.iloc[t], market.split.iloc[t],
        market.dividend.iloc[t], {s: float(prev_close[s]) for s in orders}, rules, set(market.symbols),
        market.quality_flag.iloc[t], (policy.sell_price_buffer, policy.buy_price_buffer))
    return SettledBook.from_ledger(day, new), record, trades, issues


def _pick(row: dict, keys) -> str | None:
    for key in keys:
        if key in row and str(row[key]).strip() != '':
            return str(row[key]).strip()
    return None


def load_official_holdings(path: Path | str, universe_path: Path | str = UNIVERSE_PATH) -> dict[str, float]:
    """Organizer holdings export -> {symbol: shares}.

    Accepts JSON ({ticker: shares}, a list of rows, or {"holdings": [...]}) or CSV with a ticker and a
    share column (TICKER_KEYS / SHARE_KEYS). Unknown tickers or malformed rows raise.
    """
    path = Path(path)
    if path.suffix.lower() == '.csv':
        with path.open(newline='', encoding='utf-8-sig') as handle:
            rows = list(csv.DictReader(handle))
    else:
        raw = json.loads(path.read_text())
        raw = raw.get('holdings', raw) if isinstance(raw, dict) and 'holdings' in raw else raw
        rows = [dict(ticker=k, shares=v) for k, v in raw.items()] if isinstance(raw, dict) else raw
    mapping, holdings = ticker_map(universe_path), {}
    for row in rows:
        ticker, shares = _pick(row, TICKER_KEYS), _pick(row, SHARE_KEYS)
        if ticker is None or shares is None:
            raise ValueError(f'Holding row without ticker/shares: {row}')
        ticker = symbol_to_ticker(ticker)
        if ticker not in mapping:
            raise ValueError(f'Holding {ticker} is outside the 150-stock universe')
        quantity = float(str(shares).replace(',', ''))
        if quantity < 0:
            raise ValueError(f'Negative holding {ticker}')
        if quantity > SHARE_TOL:
            holdings[mapping[ticker]] = holdings.get(mapping[ticker], 0.) + quantity
    return holdings


@dataclass(frozen=True)
class Reconciliation:
    ok: bool
    only_official: dict
    only_local: dict
    different: dict       # symbol -> (official, local)

    def summary(self) -> str:
        if self.ok:
            return 'MATCH'
        return (f'MISMATCH only_official={sorted(self.only_official)} only_local={sorted(self.only_local)} '
                f'different={sorted(self.different)}')


def reconcile(official: dict, local: dict) -> Reconciliation:
    """Share-by-share comparison of the organizer's holdings with the shadow ledger."""
    only_official = {s: q for s, q in official.items() if s not in local}
    only_local = {s: q for s, q in local.items() if s not in official}
    different = {s: (official[s], local[s]) for s in set(official) & set(local)
                 if abs(official[s] - local[s]) > SHARE_TOL}
    return Reconciliation(not (only_official or only_local or different), only_official, only_local, different)
