"""Bounded portfolio allocation using explicitly supplied D-1 information only."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy.optimize import minimize, differential_evolution
from src.official_deep_tuning import representable_weight

BUY_FEE = 0.001425
SELL_COST = 0.004425


@dataclass
class PortfolioPlan:
    target_weights: pd.Series
    target_shares: pd.Series
    orders: pd.Series
    estimated_cash: float
    status: str
    reason: str
    utility: float


def portfolio_utility(weights, expected_returns, covariance, previous_weights,
                      risk_penalty=1., turnover_penalty=0.):
    w, mu, prev = map(np.asarray, (weights, expected_returns, previous_weights))
    delta = w - prev
    cost = BUY_FEE * np.maximum(delta, 0).sum() + SELL_COST * np.maximum(-delta, 0).sum()
    return float(mu @ w - risk_penalty * (w @ covariance @ w)
                 - turnover_penalty * np.abs(delta).sum() - cost)


def _bounded_weights(raw, caps, budget):
    """Project positive preferences onto a capped simplex by water filling."""
    raw = np.maximum(np.asarray(raw, dtype=float), 1e-12)
    caps = np.asarray(caps, dtype=float)
    if budget > caps.sum() + 1e-10:
        raise ValueError('Insufficient position-cap capacity')
    low, high = 0., budget / raw.min()
    for _ in range(70):
        mid = (low + high) / 2
        if np.minimum(raw * mid, caps).sum() < budget:
            low = mid
        else:
            high = mid
    return np.minimum(raw * high, caps)


def optimize_portfolio(expected_returns, previous_close, previous_nav, current_shares=None,
                       *, current_cash=None, confidence=None, covariance=None, method='equal', target_count=22,
                       cash_target=.05, risk_penalty=1., turnover_penalty=0.,
                       max_replacements=1, replacement_margin=.05, seed=0,
                       de_maxiter=8, de_popsize=5, rebalance=False):
    """Plan lots and estimated fees at D-1 prices, without consulting fill-day data.

    Margin is in expected-return units. Replacements require the incoming return
    advantage to exceed both one-way commissions, sell tax, and this margin.
    Actual fill feasibility remains the execution ledger's responsibility.
    """
    if not 20 <= target_count <= 30 or not 0 <= max_replacements <= 3:
        raise ValueError('Invalid count or replacement budget')
    if not 0 <= cash_target < .25 or not np.isfinite(previous_nav) or previous_nav <= 0:
        raise ValueError('Invalid cash target or NAV')
    if method not in {'equal', 'score', 'continuous', 'de'}:
        raise ValueError('Unknown optimizer')
    if any(not np.isfinite(x) or x < 0 for x in (risk_penalty, turnover_penalty, replacement_margin)):
        raise ValueError('Penalties and replacement margin must be finite and nonnegative')
    mu = pd.Series(expected_returns, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    prices = pd.Series(previous_close, dtype=float)
    mu = mu.loc[prices.reindex(mu.index).gt(0) & np.isfinite(prices.reindex(mu.index))]
    if confidence is not None:
        conf = pd.Series(confidence).reindex(mu.index).fillna(0).clip(0, 1)
        mu = mu * conf
    old = pd.Series(dtype=float) if current_shares is None else pd.Series(current_shares, dtype=float)
    if old.index.has_duplicates or not np.isfinite(old).all() or (old < 0).any():
        raise ValueError('Holdings must be unique, finite, and nonnegative')
    if mu.index.has_duplicates or prices.index.has_duplicates:
        raise ValueError('Scores and prices must have unique symbols')
    old = old[old > 0]
    settled_cash = (previous_nav - (old * prices.reindex(old.index)).sum()
                    if current_cash is None else float(current_cash))
    if not np.isfinite(settled_cash) or settled_cash < 0:
        raise ValueError('Invalid settled cash')
    if not old.index.isin(mu.index).all():
        raise ValueError('A current holding lacks a finite score or D-1 close')
    ranked = sorted(mu.index, key=lambda s: (-mu[s], str(s)))
    if len(ranked) < target_count:
        raise ValueError('Insufficient eligible names')
    chosen = list(old.index)
    if len(chosen) > target_count:
        raise ValueError('Count reduction requires an explicit liquidation policy')
    for s in ranked:
        if len(chosen) >= target_count:
            break
        if s not in chosen:
            chosen.append(s)
    if len(old):
        replacements = 0
        for incoming in ranked:
            if incoming in chosen or replacements >= max_replacements:
                continue
            outgoing = min((s for s in chosen if s in old.index), key=lambda s: (mu[s], str(s)), default=None)
            if outgoing is None:
                break
            if mu[incoming] - mu[outgoing] > BUY_FEE + SELL_COST + replacement_margin:
                chosen.remove(outgoing)
                chosen.append(incoming)
                replacements += 1
    chosen = sorted(chosen)
    all_names = sorted(set(chosen) | set(old.index))
    p = prices.reindex(all_names)
    prev = old.reindex(all_names).fillna(0) * p / previous_nav
    caps = np.array([.25 if str(s).split('.')[0] == '2330' else .10 for s in chosen])
    budget = 1 - max(cash_target, BUY_FEE + .0001)
    selected_mu = mu.reindex(chosen).to_numpy()
    if covariance is None:
        cov = np.eye(len(chosen)) * .0004
    elif isinstance(covariance, pd.DataFrame):
        cov = covariance.reindex(index=chosen, columns=chosen).to_numpy()
    else:
        cov = np.asarray(covariance, dtype=float)
    if cov.shape != (len(chosen), len(chosen)) or not np.isfinite(cov).all():
        raise ValueError('Invalid selected-name covariance')
    cov = (cov + cov.T) / 2
    if np.linalg.eigvalsh(cov).min() < -1e-10:
        raise ValueError('Covariance must be positive semidefinite')
    selected_prev = prev.reindex(chosen).to_numpy()
    def utility(w):
        return portfolio_utility(w, selected_mu, cov, selected_prev, risk_penalty, turnover_penalty)
    raw = np.ones(len(chosen)) if method == 'equal' else selected_mu - selected_mu.min() + .001
    w = _bounded_weights(raw, caps, budget)
    # With a fixed incumbent set, every proposed weight is overwritten by
    # the held share count below. Skip only those observationally dead solves;
    # cap/cash repair and the realized-plan utility still run unchanged.
    allocation_fixed = not rebalance and set(chosen) == set(old.index)
    if method == 'continuous' and not allocation_fixed:
        fit = minimize(lambda x: -utility(x), w, method='SLSQP', bounds=list(zip(np.zeros(len(w)), caps)),
                       constraints={'type': 'eq', 'fun': lambda x: x.sum() - budget},
                       options={'maxiter': 80, 'ftol': 1e-10})
        if fit.success:
            w = _bounded_weights(fit.x, caps, budget)
    elif method == 'de' and not allocation_fixed:
        fit = differential_evolution(lambda x: -utility(_bounded_weights(x, caps, budget)),
                                     [(0.001, 1.)] * len(w), seed=seed, maxiter=de_maxiter,
                                     popsize=de_popsize, polish=False, workers=1)
        proposal = _bounded_weights(fit.x, caps, budget)
        if utility(proposal) > utility(w):
            w = proposal
    weights = pd.Series(w, index=chosen).reindex(all_names).fillna(0)
    # Official D-Plan floor formula; never round up after observing execution.
    shares = (np.floor(weights * previous_nav / p / 1000) * 1000).astype('int64')
    shares.loc[chosen] = shares.loc[chosen].clip(lower=1000)
    old_all = old.reindex(all_names).fillna(0)
    if not rebalance:
        for symbol in old.index.intersection(chosen):
            shares[symbol] = int(old[symbol])
    if ((old_all % 1000) != 0).any():
        raise ValueError('Odd-lot legacy holdings require separate corporate-action handling')
    def account(q):
        delta = q - old_all
        buy = (delta.clip(lower=0) * p).sum()
        sell = (-delta.clip(upper=0) * p).sum()
        fees = buy * BUY_FEE + sell * SELL_COST
        cash = settled_cash - buy * (1 + BUY_FEE) + sell * (1 - SELL_COST)
        return float(cash), float(previous_nav - fees)
    proposed_buys = (shares - old_all).clip(lower=0)
    required_cash = float((proposed_buys * p).sum()) * 1.10 * (1 + BUY_FEE)
    if required_cash > settled_cash and required_cash > 0:
        funded_buys = np.floor(proposed_buys * settled_cash / required_cash / 1000) * 1000
        funded_buys.loc[proposed_buys > 0] = funded_buys.loc[proposed_buys > 0].clip(lower=1000)
        buying = proposed_buys > 0
        shares.loc[buying] = (old_all.loc[buying] + funded_buys.loc[buying]).astype('int64')
    # Repair caps against post-cost NAV, and reserve fee cash. Trim one lot
    # at a time; never silently erase a name below the minimum holding count.
    for _ in range(10000):
        cash, nav = account(shares)
        cap_s = pd.Series({s: .25 if str(s).split('.')[0] == '2330' else .10 for s in all_names})
        over = shares * p > cap_s * nav + 1e-8
        buys = ((shares - old_all).clip(lower=0) * p).sum()
        funding_ok = buys * 1.10 * (1 + BUY_FEE) <= settled_cash + 1e-8
        if cash >= -1e-8 and funding_ok and not over.any():
            break
        trim_mask = over if over.any() else (shares > old_all)
        candidates = shares.index[(shares >= 2000) & trim_mask]
        if not len(candidates):
            break
        trim = max(candidates, key=lambda s: shares[s] * p[s])
        excess = max(1000., np.ceil((shares[trim] * p[trim] - cap_s[trim] * nav) / p[trim] / 1000) * 1000) if over.any() else 1000.
        shares[trim] -= int(min(excess, shares[trim] - 1000))
    cash, nav = account(shares)
    actual_weights = shares * p / nav
    good = (cash >= -1e-8 and cash / nav < .25 and 20 <= (shares > 0).sum() <= 30
            and (actual_weights <= cap_s + 1e-10).all())
    good = good and (((shares - old_all).clip(lower=0) * p).sum() * 1.10 * (1 + BUY_FEE) <= settled_cash + 1e-8)
    # Use the same cap-bounded interior lot bin as the sealed D-Plan ledger.
    # A one-ULP boundary offset is lost by ordinary CSV parsing and can turn
    # the declared target into one fewer lot without changing physical shares.
    weights = pd.Series({s: representable_weight(shares[s], p[s], previous_nav, cap_s[s])
                         for s in all_names}, dtype=float)
    orders = (shares - old_all).astype('int64')
    return PortfolioPlan(weights, shares, orders[orders != 0], cash,
                         'PASS_MEASURED' if good else 'NO_VALID_PLAN',
                         'D-1 estimated accounting; fill-day constraints require ledger audit',
                         utility((shares * p / previous_nav).reindex(chosen).to_numpy())
                         - (SELL_COST + turnover_penalty) * float(prev.drop(index=chosen).sum()))
