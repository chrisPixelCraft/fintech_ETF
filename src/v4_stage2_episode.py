"""Inject V4 decisions into the sealed Stage-1 accounting and audit pipeline.

Private function namespaces avoid mutating shared module globals. The executed
ledger and baseline function code objects are exactly the Stage-1 code objects.
"""
import copy
from types import FunctionType
import pandas as pd
from src import v4_baseline, v4_ledger
from src.v4_strategy import V4Strategy


def _bind(function, **overrides):
    namespace = dict(function.__globals__)
    namespace.update(overrides)
    return FunctionType(function.__code__, namespace, function.__name__,
                        function.__defaults__, function.__closure__)


def _identity_score(rows, config):
    rows = rows.set_index('symbol', drop=False).copy()
    rows['score'] = 0.
    rows['entry_ok'] = rows.ready
    rows['exit'] = False
    return rows


class StrategyPlanner:
    def __init__(self, strategy, features, dates):
        self.strategy, self.features, self.dates = strategy, features, pd.DatetimeIndex(dates)
        self.audit, self.predictions = [], []

    def __call__(self, ranked, holdings, cash, nav, cfg, *args):
        observed = pd.Timestamp(ranked.date.iloc[0])
        upcoming = self.dates[self.dates > observed]
        reason, orders, selected = 'TERMINAL_NO_PLAN', {}, list(holdings)
        if len(upcoming):
            decision = upcoming[0]
            state = dict(holdings=holdings, cash=cash, nav=nav,
                         previous_close=ranked.close.copy())
            target = self.strategy.generate_target(decision, self.features,
                state, dict(remaining_sessions=len(upcoming)))
            reason = str(target.attrs.get('reason', 'V4_TARGET'))
            if len(target):
                saved = target.copy()
                saved['symbol'] = saved.index
                saved['signal_date'] = observed
                saved['decision_date'] = decision
                self.predictions.append(saved.reset_index(drop=True))
                orders = {s: int(q) for s, q in target.order_shares.items() if q}
                selected = target.index[target.target_shares > 0].tolist()
        self.audit.append(dict(date=str(observed.date()), original_reason=reason,
            final_reason=reason, attempts=1, replanning_triggered=False,
            orders=len(orders), nominal_failures=reason if reason.startswith('INFEASIBLE') else '',
            hold_envelope_failures='NOT_CERTIFIED'))
        return orders, reason, selected


def run_episode(daily, universe, base_config, session_dates, execution_data,
                features, strategy_config, forecaster=None):
    """Use canonical official fills and closes, preserving all failure episodes."""
    config = copy.deepcopy(base_config)
    config.update(strategy_id=strategy_config['id'], target_count=strategy_config.get('target_count',22))
    # Compatibility guard belongs to the frozen adapter; actual V4 identity is
    # separately recorded in the strategy configuration and evidence manifest.
    panel = features.copy(deep=False)
    panel.attrs = dict(features.attrs, feature_parameters=config['deep_feature_params'],
                       warmup_sessions=config['warmup_sessions'])
    strategy = V4Strategy(strategy_config, forecaster=forecaster)
    planner = StrategyPlanner(strategy, panel, session_dates)
    ledger = _bind(v4_ledger.run_ledger, _score=_identity_score)
    episode = _bind(v4_baseline.run_episode, OfficialPlanner=lambda: planner, run_ledger=ledger)
    result = episode(daily, universe, config, session_dates, execution_data,
        execution_mode='official_average', features=panel, sizing_price_mode='official_close')
    result['predictions'] = pd.concat(planner.predictions, ignore_index=True) if planner.predictions else pd.DataFrame()
    result['strategy_config'] = copy.deepcopy(strategy_config)
    return result
