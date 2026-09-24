"""Determinism and adversarial evidence tampering for Stage 1 accounting."""
from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest

import pandas as pd

from scripts.v4_verify import audit_episode, verify_directory, verify_execution_provenance
from scripts.audit_24d import verify_hashes
from src.strategy_24d import build_config
from src.v4_baseline import run_episode
from test_strategy_24d import fixture
from test_v4_causality import official_fixture


def result_hash(result):
    digest = hashlib.sha256()
    for key in sorted(result):
        value = result[key]
        if isinstance(value, pd.DataFrame):
            payload = value.to_csv(index=False, lineterminator='\n', float_format='%.17g')
        elif isinstance(value, dict):
            payload = json.dumps(value, sort_keys=True, default=str)
        else:
            continue
        digest.update(key.encode())
        digest.update(payload.encode())
    return digest.hexdigest()


def write_official_fixture_provenance(path, official):
    official = official.copy()
    official['date'] = pd.to_datetime(official.date).dt.strftime('%Y-%m-%d')
    attempts = []
    for date, rows in official.groupby('date'):
        fields = ['證券代號', '開盤價', '最高價', '最低價', '收盤價', '成交股數', '成交金額']
        data = [[r.symbol.split('.')[0], r.open, r.high, r.low, r.close, r.volume, r.trading_value]
                for r in rows.itertuples(index=False)]
        content = json.dumps(dict(date=date, tables=[dict(fields=fields, data=data)])).encode()
        raw = path.parent / (date + '.json')
        raw.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        official.loc[rows.index, 'raw_sha256'] = digest
        official.loc[rows.index, 'source_url'] = 'https://example.test/' + date
        attempts.append(dict(date=date, market='TWSE', status='OK', raw_path=str(raw),
                             raw_sha256=digest, url='https://example.test/' + date))
    official.to_csv(path, index=False)
    path.with_suffix('.manifest.json').write_text(json.dumps(dict(attempts=attempts,
        normalized_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), rows=len(official),
        available_rows=len(official))))


class V4ReproducibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.universe, cls.dates = fixture()
        cls.config = build_config()
        cls.official = official_fixture(cls.daily)
        cls.result = run_episode(cls.daily, cls.universe, cls.config, cls.dates,
                                execution_data=cls.official)

    def audit(self, result):
        return audit_episode(result, self.daily, self.universe, self.official)

    def test_same_inputs_and_config_have_identical_full_output_hash(self):
        repeated = run_episode(self.daily.copy(), self.universe.copy(), deepcopy(self.config),
                               self.dates.copy(), execution_data=self.official.copy())
        self.assertEqual(result_hash(self.result), result_hash(repeated))
        self.assertEqual(self.audit(repeated)['independent_audit'], 'PASS_INTERNAL_ACCOUNTING')

    def test_auditor_rejects_cash_and_price_tampering(self):
        for table, column, delta in [('equity', 'cash', 1000.), ('trades', 'price', .5),
                                     ('trades', 'fee', 100.), ('holdings', 'shares', 1000.),
                                     ('orders', 'target_weight', .01)]:
            with self.subTest(table=table, column=column):
                changed = deepcopy(self.result)
                changed[table].loc[changed[table].index[0], column] += delta
                with self.assertRaises(AssertionError):
                    self.audit(changed)

    def test_auditor_rejects_missing_fill_and_forged_return(self):
        changed = deepcopy(self.result)
        changed['trades'] = changed['trades'].iloc[1:].copy()
        with self.assertRaises(AssertionError):
            self.audit(changed)
        changed = deepcopy(self.result)
        changed['metrics']['episode_return'] += .1
        with self.assertRaises(AssertionError):
            self.audit(changed)

    def test_auditor_retains_failed_missing_execution_episode(self):
        missing = self.official.loc[self.official.date.ne(self.dates[0])].copy()
        result = run_episode(self.daily, self.universe, self.config, self.dates,
                             execution_data=missing)
        audit = audit_episode(result, self.daily, self.universe, missing)
        self.assertEqual(audit['independent_audit'], 'PASS_INTERNAL_ACCOUNTING')
        self.assertFalse(audit['measured_pass'])
        self.assertGreater(audit['unfilled_orders'], 0)

    def test_audit_uses_official_ratio_instead_of_open_proxy(self):
        official = self.official.copy()
        official['trading_value'] *= 1.005
        official['average_execution_price'] *= 1.005
        result = run_episode(self.daily, self.universe, self.config, self.dates,
                             execution_data=official)
        self.assertEqual(audit_episode(result, self.daily, self.universe, official)
                         ['independent_audit'], 'PASS_INTERNAL_ACCOUNTING')
        with self.assertRaises(AssertionError):
            audit_episode(result, self.daily, self.universe, self.official)

    def test_dividend_and_split_accounting_is_independently_reconstructed(self):
        daily = self.daily.copy()
        event = self.dates[4]
        symbol = self.result['holdings'].symbol.iloc[0]
        mask = daily.symbol.eq(symbol)
        daily.loc[mask & daily.date.ge(event), ['open', 'high', 'low', 'close']] /= 2
        daily.loc[mask & daily.date.eq(event), ['split', 'dividend']] = [2., 1.]
        official = official_fixture(daily)
        result = run_episode(daily, self.universe, self.config, self.dates,
                             execution_data=official)
        audit = audit_episode(result, daily, self.universe, official)
        self.assertEqual(audit['independent_audit'], 'PASS_INTERNAL_ACCOUNTING')
        self.assertGreater(result['equity'].dividend_receivable.max(), 0)

    def test_official_close_pipeline_is_independently_audited(self):
        official = self.official.copy()
        official[['open', 'high', 'low', 'close', 'average_execution_price', 'trading_value']] *= 1.005
        result = run_episode(self.daily, self.universe, self.config, self.dates,
                             execution_data=official, sizing_price_mode='official_close')
        audit = audit_episode(result, self.daily, self.universe, official)
        self.assertEqual(audit['independent_audit'], 'PASS_INTERNAL_ACCOUNTING')
        self.assertTrue(result['metrics']['canonical_sizing_available'])

    def test_untrusted_official_close_cannot_be_certified(self):
        official = self.official.copy()
        official.loc[official.date.lt(self.dates[0]), 'source'] = 'VENDOR_PROXY'
        result = run_episode(self.daily, self.universe, self.config, self.dates,
                             execution_data=official, sizing_price_mode='official_close')
        audit = audit_episode(result, self.daily, self.universe, official)
        self.assertFalse(audit['measured_pass'])
        self.assertFalse(result['metrics']['canonical_sizing_available'])

    def test_serialized_directory_is_audited_and_cannot_hide_an_attempt(self):
        canonical = run_episode(self.daily, self.universe, self.config, self.dates,
                                execution_data=self.official, sizing_price_mode='official_close')
        for track, result in [('official_average', self.result),
                              ('official_average_official_close', canonical)]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                inputs = {}
                for name, frame in [('daily', self.daily), ('universe', self.universe),
                                    ('execution_data', self.official)]:
                    path = root / (name + '_input.csv')
                    frame.to_csv(path, index=False)
                    inputs[name] = str(path)
                write_official_fixture_provenance(Path(inputs['execution_data']), self.official)
                location = root / f'ledgers/{track}/example'
                location.mkdir(parents=True)
                for key, value in result.items():
                    if isinstance(value, pd.DataFrame):
                        value.to_csv(location / (key + '.csv'), index=False)
                for key in ['config', 'metrics']:
                    (location / (key + '.json')).write_text(json.dumps(result[key], default=str))
                pd.DataFrame([dict(result['metrics'], episode_id='example', execution_mode=track)]).to_csv(root / 'results.csv', index=False)
                (root / 'study.json').write_text(json.dumps({'execution_modes': [track]}))
                (root / 'episodes.json').write_text(json.dumps([dict(episode_id='example',
                    start=str(self.dates[0].date()), end=str(self.dates[-1].date()), session_count=24,
                    sessions=[str(d.date()) for d in self.dates])]))
                manifest = dict(inputs=inputs,
                    input_hashes={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*_input.csv')},
                    output_hashes={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in root.rglob('*') if p.is_file()})
                (root / 'manifest.json').write_text(json.dumps(manifest))
                self.assertEqual(verify_directory(root)['attempted'], 1)
                pd.read_csv(root / 'results.csv').iloc[:0].to_csv(root / 'results.csv', index=False)
                manifest['output_hashes']['results.csv'] = hashlib.sha256((root / 'results.csv').read_bytes()).hexdigest()
                (root / 'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(AssertionError, 'denominator'):
                    verify_directory(root)

    def test_normalized_prices_are_bound_to_raw_payload_even_after_rehash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'execution.csv'
            write_official_fixture_provenance(path, self.official.iloc[:25])
            self.assertEqual(verify_execution_provenance(path)['status'], 'PASS_RAW_PROVENANCE')
            frame = pd.read_csv(path)
            frame.loc[0, 'close'] *= 1.1
            frame.to_csv(path, index=False)
            metadata = json.loads(path.with_suffix('.manifest.json').read_text())
            metadata['normalized_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            path.with_suffix('.manifest.json').write_text(json.dumps(metadata))
            with self.assertRaisesRegex(AssertionError, 'differs from raw: close'):
                verify_execution_provenance(path)

    def test_manifest_hashes_detect_changes_and_reject_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data.csv').write_text('date,price\n2020-01-01,10\n')
            hashes = {'data.csv': hashlib.sha256((root / 'data.csv').read_bytes()).hexdigest()}
            self.assertEqual(verify_hashes(root, hashes)['status'], 'PASS_HASHES')
            (root / 'data.csv').write_text('date,price\n2020-01-01,20\n')
            with self.assertRaisesRegex(AssertionError, 'hash mismatch'):
                verify_hashes(root, hashes)
            with self.assertRaisesRegex(AssertionError, 'escapes'):
                verify_hashes(root, {'../outside': 'x'})


if __name__ == '__main__':
    unittest.main()
