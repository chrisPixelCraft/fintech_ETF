import json
import unittest

import numpy as np
import pandas as pd

from autots_strategy.targets import forward_log_return
from competition import execution
from competition.backtest import ExecutionConfig
from competition.rules import ROOT
from lgbm_strategy import targets
from tests.synthetic import synthetic_market

H = 10


def wide(values: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(values, index=pd.bdate_range('2020-01-01', periods=len(values)),
                        columns=[f'S{i:03d}' for i in range(values.shape[1])])


class RawReturnTest(unittest.TestCase):
    def setUp(self):
        self.market = synthetic_market(n_days=200, n_symbols=25)
        self.view = self.market.asof(self.market.calendar[-1])
        self.ret = self.market.ret

    def test_matches_previous_jpx2_definition(self):
        observed = self.ret.notna().rolling(H).sum().shift(-H) == H
        before = np.expm1(forward_log_return(self.ret, H)).where(observed)   # label code of the JPX2 run
        pd.testing.assert_frame_equal(targets.raw_forward_return(self.ret, H), before)
        pd.testing.assert_frame_equal(targets.build_target(self.view, H, 'raw_return'), before)

    def test_is_compounded_forward_return(self):
        raw = targets.raw_forward_return(self.ret, H)
        self.assertAlmostEqual(raw.iloc[50, 3], np.prod(1 + self.ret.iloc[51:51 + H, 3]) - 1)
        self.assertTrue(raw.iloc[-H:].isna().all().all())   # not matured inside the sample

    def test_modes(self):
        self.assertEqual(targets.TARGET_MODES, ('raw_return', 'relative_alpha', 'execution_alpha'))
        with self.assertRaises(ValueError):
            targets.build_target(self.view, H, 'excess')


class RelativeAlphaTest(unittest.TestCase):
    def test_cross_section_mean_is_zero(self):
        market = synthetic_market(n_days=200, n_symbols=25)
        for mode in ('relative_alpha', 'execution_alpha'):
            with self.subTest(mode=mode):
                alpha = targets.build_target(market.asof(market.calendar[-1]), H, mode)
                valid = alpha.dropna(how='all')
                self.assertGreater(len(valid), 150)
                self.assertLess(valid.mean(axis=1).abs().max(), 1e-12)
        raw = targets.raw_forward_return(market.ret, H)
        pd.testing.assert_frame_equal(targets.build_target(market.asof(market.calendar[-1]), H, 'relative_alpha'),
                                      raw.sub(raw.mean(axis=1).where(raw.notna().sum(axis=1) >= 20), axis=0))

    def test_market_mean_uses_finite_labels_only(self):
        raw = wide(np.arange(1., 26.).reshape(1, 25) / 100)
        raw.iloc[0, [0, 24]] = np.nan                   # 23 finite labels remain
        market = targets.market_forward_return(raw)
        self.assertAlmostEqual(market.iloc[0], np.mean(np.arange(2., 25.)) / 100)
        alpha = targets.relative_alpha(raw)
        self.assertTrue(np.isnan(alpha.iloc[0, 0]))
        self.assertAlmostEqual(alpha.iloc[0].mean(), 0.)

    def test_low_coverage_date_is_invalid(self):
        raw = wide(np.full((2, 25), .01))
        raw.iloc[0, 19:] = np.nan                       # 19 finite labels -> invalid
        raw.iloc[1, 20:] = np.nan                       # 20 finite labels -> valid
        alpha = targets.relative_alpha(raw)
        self.assertTrue(alpha.iloc[0].isna().all())
        self.assertEqual(int(alpha.iloc[1].notna().sum()), 20)
        self.assertEqual(targets.MIN_MARKET_LABELS, 20)


class ExecutionAlphaTest(unittest.TestCase):
    def setUp(self):
        self.market = synthetic_market(n_days=200, n_symbols=25, official_from=120)   # proxy era, then official
        self.view = self.market.asof(self.market.calendar[-1])

    def test_execution_price_is_the_backtest_fill_price(self):
        price, source = targets.execution_price(self.view)
        self.assertEqual(targets.EXECUTION, ExecutionConfig())
        for day in (self.market.calendar[60], self.market.calendar[150]):
            fill, fill_source = execution.fill_prices(self.market, day, self.market.symbols)
            pd.testing.assert_series_equal(price.loc[day], fill, check_names=False)
            pd.testing.assert_series_equal(source.loc[day], fill_source, check_names=False)
        self.assertTrue((source.loc[self.market.calendar[60]] == 'proxy_hlc3').all())
        self.assertTrue((source.loc[self.market.calendar[150]] == 'official_vwap').all())

    def test_missing_vwap_falls_back_to_the_same_hlc3(self):
        vwap = self.market.official_vwap.copy()
        day = self.market.calendar[150]
        vwap.loc[day, 'S003.TW'] = np.nan
        view = self.market.with_frames(official_vwap=vwap).asof(self.market.calendar[-1])
        price, source = targets.execution_price(view)
        hlc3 = (view.high + view.low + view.close) / 3
        self.assertEqual(price.at[day, 'S003.TW'], hlc3.at[day, 'S003.TW'])
        self.assertEqual(source.at[day, 'S003.TW'], 'proxy_hlc3')

    def test_timeline_and_value(self):
        self.assertEqual(targets.RawReturnTarget().label_span(H), H)
        self.assertEqual(targets.RelativeAlphaTarget().label_span(H), H)
        self.assertEqual(targets.ExecutionAlphaTarget().label_span(H), H + 1)
        r = targets.execution_forward_return(self.view, H)
        price, _ = targets.execution_price(self.view)
        t = 40                                          # no corporate actions in the synthetic panel
        self.assertAlmostEqual(r.iloc[t, 5], price.iloc[t + 1 + H, 5] / price.iloc[t + 1, 5] - 1)
        n = len(self.view.close)
        self.assertTrue(r.iloc[n - 1 - H:].isna().all().all())   # exit_session t+1+h is past the cutoff
        self.assertTrue(r.iloc[n - 2 - H].notna().all())

    def test_split_between_entry_and_exit_is_neutral(self):
        m, t, s = self.market, 40, 'S005.TW'
        base = targets.execution_forward_return(m.asof(m.calendar[-1]), H).iloc[t][s]
        split_day = m.calendar[t + 5]                   # a 2-for-1 split inside the label window
        after = m.calendar >= split_day
        frames = {name: getattr(m, name).copy() for name in ('open', 'high', 'low', 'close', 'official_vwap')}
        for frame in frames.values():
            frame.loc[after, s] = frame.loc[after, s] / 2
        split = targets.execution_forward_return(m.with_frames(**frames).asof(m.calendar[-1]), H).iloc[t][s]
        self.assertAlmostEqual(base, split)             # ret (action-neutral) is unchanged by the split


class ConfigDiffTest(unittest.TestCase):
    def test_configs_differ_only_in_target(self):
        load = lambda name: json.loads((ROOT / f'research/configs/{name}.json').read_text())
        configs = {mode: load(name) for mode, name in (('raw_return', 'baseline_lgbm_raw'),
                                                       ('relative_alpha', 'baseline_lgbm_alpha'),
                                                       ('execution_alpha', 'baseline_lgbm_execution_alpha'))}
        strip = lambda c: {k: v for k, v in c.items() if k not in ('name', 'description')}
        for mode, config in configs.items():
            self.assertEqual(config['params'].pop('target_mode'), mode)
            self.assertEqual(strip(config), strip(configs['raw_return']))
            self.assertEqual(config['execution'], dict(mode=targets.EXECUTION.mode, proxy=targets.EXECUTION.proxy))
        jpx2 = load('baseline_lgbm_jpx2')
        self.assertEqual(strip(jpx2), strip(load('baseline_lgbm_raw')))


if __name__ == '__main__':
    unittest.main()
