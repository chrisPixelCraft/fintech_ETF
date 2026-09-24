import json
import unittest

import numpy as np
import pandas as pd

from autots_strategy.targets import forward_log_return
from competition.rules import ROOT
from lgbm_strategy import targets
from tests.synthetic import synthetic_market

H = 10


def wide(values: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(values, index=pd.bdate_range('2020-01-01', periods=len(values)),
                        columns=[f'S{i:03d}' for i in range(values.shape[1])])


class RawReturnTest(unittest.TestCase):
    def setUp(self):
        self.ret = synthetic_market(n_days=200, n_symbols=25).ret

    def test_matches_previous_jpx2_definition(self):
        observed = self.ret.notna().rolling(H).sum().shift(-H) == H
        before = np.expm1(forward_log_return(self.ret, H)).where(observed)   # label code of the JPX2 run
        pd.testing.assert_frame_equal(targets.raw_forward_return(self.ret, H), before)
        pd.testing.assert_frame_equal(targets.build_target(self.ret, H, 'raw_return'), before)

    def test_is_compounded_forward_return(self):
        raw = targets.raw_forward_return(self.ret, H)
        self.assertAlmostEqual(raw.iloc[50, 3], np.prod(1 + self.ret.iloc[51:51 + H, 3]) - 1)
        self.assertTrue(raw.iloc[-H:].isna().all().all())   # not matured inside the sample

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            targets.build_target(self.ret, H, 'excess')


class RelativeAlphaTest(unittest.TestCase):
    def test_cross_section_mean_is_zero(self):
        ret = synthetic_market(n_days=200, n_symbols=25).ret
        alpha = targets.build_target(ret, H, 'relative_alpha')
        valid = alpha.dropna(how='all')
        self.assertGreater(len(valid), 150)
        self.assertLess(valid.mean(axis=1).abs().max(), 1e-12)
        raw = targets.raw_forward_return(ret, H)
        pd.testing.assert_frame_equal(alpha, raw.sub(raw.mean(axis=1).where(raw.notna().sum(axis=1) >= 20), axis=0))

    def test_market_mean_uses_finite_labels_only(self):
        raw = wide(np.arange(1., 26.).reshape(1, 25) / 100)
        raw.iloc[0, [0, 24]] = np.nan                   # 23 finite labels remain
        market = targets.cross_section_market_return(raw)
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


class ConfigDiffTest(unittest.TestCase):
    def test_alpha_config_differs_only_in_target(self):
        load = lambda name: json.loads((ROOT / f'research/configs/{name}.json').read_text())
        raw, alpha = load('baseline_lgbm_jpx2'), load('baseline_lgbm_alpha')
        self.assertEqual((raw['params'].pop('target_mode'), alpha['params'].pop('target_mode')),
                         ('raw_return', 'relative_alpha'))
        strip = lambda c: {k: v for k, v in c.items() if k not in ('name', 'description')}
        self.assertEqual(strip(raw), strip(alpha))


if __name__ == '__main__':
    unittest.main()
