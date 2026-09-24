"""Common causal target interface for the three Stage-2 strategy families."""
from __future__ import annotations
import copy
import json
import numpy as np
import pandas as pd
from src.v4_features import history_at
from src.v4_alpha import momentum_score
from src.v4_regime import classify_regime
from src.v4_portfolio_optimizer import optimize_portfolio
from src.v4_direct_optimizer import DirectModel, DEFAULT_FEATURES

OUTPUT_COLUMNS = ['target_weight', 'target_shares', 'order_shares', 'expected_return',
                  'score', 'confidence', 'reason', 'family', 'model_ensemble_id', 'regime']


class V4Strategy:
    def __init__(self, config, forecaster=None):
        self.config = copy.deepcopy(config)
        self.family = str(config.get('family', 'momentum')).lower().removeprefix('v4-').removeprefix('v4_')
        if self.family not in ('momentum', 'adaptive', 'direct'):
            raise ValueError('Unknown V4 family')
        self.forecaster = forecaster
        if (self.family == 'adaptive' or config.get('confidence', False)) and forecaster is None:
            raise ValueError('A causal forecaster is required for adaptive/confidence')
        if self.family == 'direct':
            names = tuple(config.get('feature_names', DEFAULT_FEATURES))
            coefficients = tuple(config.get('coefficients', ()))
            if len(coefficients) != len(names) or not np.isfinite(coefficients).all():
                raise ValueError('Direct requires frozen finite coefficients for each feature')
            self.direct = DirectModel(names, coefficients, int(config.get('seed', 0)), ())

    def _failed(self, reason, **audit):
        frame = pd.DataFrame(columns=OUTPUT_COLUMNS, index=pd.Index([], name='symbol'))
        frame.attrs.update(status='NO_VALID_PLAN', reason='INFEASIBLE_V4:' + reason, family=self.family, **audit)
        return frame

    def generate_target(self, decision_date, history, portfolio_state, competition_state):
        """Use only the last date strictly before decision_date, then size D-1 lots.

        previous_close supplied by the execution adapter is authoritative for
        sizing; feature close remains the signal observation. A failed plan is
        explicit and contains no executable orders.
        """
        rows = history_at(history, decision_date)
        if rows.empty:
            return self._failed('NO_PRIOR_OBSERVATIONS')
        observed = str(pd.Timestamp(rows.date.max()).date())
        rows = rows.loc[rows.feature_ready].set_index('symbol', drop=False)
        if rows.empty:
            return self._failed('NO_READY_FEATURES', observed_date=observed)
        c = self.config
        horizon = c.get('horizon', 5)
        if horizon == 'remaining' or c.get('remaining_horizon', False):
            horizon = min(24, max(1, int(competition_state.get('remaining_sessions', 24))))
        horizon = int(horizon)
        if not 1 <= horizon <= 24:
            raise ValueError('Forecast horizon outside [1,24]')
        regime_info = classify_regime(rows)
        regime = regime_info['regime'] if c.get('regime', False) else 'disabled'
        forecast = None
        audit = dict(observed_date=observed, horizon=horizon, regime_features=regime_info)
        if self.family == 'adaptive' or c.get('confidence', False):
            forecast = self.forecaster.predict(decision_date, horizon)
            rows = rows.loc[rows.index.intersection(forecast.index)]
            audit['forecast'] = copy.deepcopy(forecast.attrs)
        confidence = pd.Series(1., index=rows.index)
        if forecast is not None:
            confidence = forecast.confidence.reindex(rows.index)
        if self.family == 'momentum':
            raw = momentum_score(rows, tuple(c.get('horizons', (c.get('short', 5), c.get('medium', 10), c.get('long', 20)))), c.get('score_family', 'full'))
            expected = .1 * (raw.rank(pct=True) - .5)
            identity = 'V4_MOMENTUM_' + str(c.get('score_family', 'full'))
        elif self.family == 'direct':
            raw = self.direct.score(rows)
            expected = .1 * raw
            identity = 'V4_DIRECT_FROZEN_LINEAR_PORTFOLIO'
        else:
            expected = forecast.expected_return.reindex(rows.index)
            raw = expected.copy()
            identity = forecast.attrs.get('model_identity', 'V4_ADAPTIVE')
        prices = pd.Series(portfolio_state.get('previous_close', rows.close), dtype=float)
        cash_target = float(c.get('cash_target', .05))
        risk_penalty = float(c.get('risk_penalty', 1.))
        rotation = int(c.get('max_replacements', 1))
        if regime == 'risk_off':
            cash_target = min(.20, cash_target + .05)
            risk_penalty *= 2
            rotation = max(0, rotation - 1)
        variances = rows.vol20.reindex(expected.index).fillna(.02).clip(lower=.001) ** 2 * horizon
        covariance = pd.DataFrame(np.diag(variances), index=expected.index, columns=expected.index)
        try:
            plan = optimize_portfolio(expected, prices, float(portfolio_state['nav']),
                portfolio_state.get('holdings', {}), current_cash=float(portfolio_state['cash']),
                confidence=confidence if c.get('confidence', False) else None, covariance=covariance,
                method=c.get('optimizer', 'equal'), target_count=int(c.get('target_count', c.get('count', 22))),
                cash_target=cash_target, risk_penalty=risk_penalty,
                turnover_penalty=float(c.get('turnover_penalty', 0.)), max_replacements=rotation,
                replacement_margin=float(c.get('replacement_margin', .05)), seed=int(c.get('seed', 0)))
        except (ValueError, KeyError) as error:
            return self._failed(str(error), **audit)
        if plan.status != 'PASS_MEASURED':
            return self._failed(plan.reason, estimated_cash=plan.estimated_cash, **audit)
        names = plan.target_weights.index
        output = pd.DataFrame(dict(target_weight=plan.target_weights, target_shares=plan.target_shares,
            order_shares=plan.orders.reindex(names).fillna(0).astype('int64'),
            expected_return=expected.reindex(names), score=raw.reindex(names), confidence=confidence.reindex(names),
            reason=plan.reason, family=self.family, model_ensemble_id=identity, regime=regime), index=names)
        output.index.name = 'symbol'
        output['observed_date'] = observed
        output['prior_regime'] = regime_info['regime']
        for key in ('market_volatility', 'median_R5', 'median_R20', 'breadth', 'dispersion', 'above_EMA20', 'above_EMA50'):
            output[key] = regime_info[key]
        output['model_train_end'] = forecast.attrs.get('model_train_end') if forecast is not None else None
        output['latest_label_end'] = forecast.attrs.get('latest_label_end') if forecast is not None else None
        output['expert_weights'] = json.dumps(forecast.attrs.get('expert_weights', {}), sort_keys=True) if forecast is not None else '{}'
        if forecast is not None:
            for name in ('uncertainty', 'sign_agreement'):
                output[name] = forecast[name].reindex(names)
            for name in ('lower_bound', 'upper_bound'):
                output[name] = (forecast[name] - forecast.expected_return + expected).reindex(names)
        output.attrs.update(status=plan.status, reason=plan.reason, estimated_cash=plan.estimated_cash,
                            utility=plan.utility, family=self.family,
                            plan=dict(status=plan.status, estimated_cash=plan.estimated_cash), **audit)
        return output
