"""Raw-input causality and fixed-entry contract, independent of live credentials."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
import numpy as np
import v2_offcial_best_deep_tuning as entry
from src.official_deep_tuning import config_for


class RawSignalContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.path=Path(cls.temp.name)
        ref=json.loads((entry.ROOT/'daily_auto/reference/official_reference.json').read_text())
        cls.symbols=[s['ticker'] for s in ref['stocks']]
        dates=pd.bdate_range(end='2026-09-21',periods=205)
        rows=[]
        for i,s in enumerate(cls.symbols):
            for j,date in enumerate(dates):
                close=30+i*.1+j*.03
                rows.append(dict(date=str(date.date()),symbol=s,open=close,high=close+1,low=close-1,
                    close=close,volume=100000.,dividend=0.,split=1.))
        cls.daily=pd.DataFrame(rows);cls.daily_path=cls.path/'daily.csv';cls.daily.to_csv(cls.daily_path,index=False)
        hourly=[]
        for s in cls.symbols:
            for d in dates[-60:]:
                for h in [9,10,11,12]:
                    hourly.append(dict(timestamp=f'{d.date()}T{h:02d}:00:00+08:00',symbol=s,
                        open=50.,high=51.,low=49.,close=50.,volume=1000.))
        cls.hourly=pd.DataFrame(hourly);cls.hourly_path=cls.path/'hourly.csv';cls.hourly.to_csv(cls.hourly_path,index=False)
        study=json.loads((entry.ROOT/'config/official_deep_study.json').read_text())
        base=json.loads((entry.ROOT/'config/strategy_v2.json').read_text())
        cls.cfg=config_for(dict(base=base,track='official_ex_post'),study['candidates'][0])
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def manifest(self,daily=None,hourly=None):
        paths={'daily':daily or self.daily_path,'hourly':hourly or self.hourly_path}
        return dict(as_of='2026-09-21',known_at='2026-09-21T19:30:00+08:00',sources=[
            dict(role=k,authority='other',source_url='https://example.com/test-only',
                content_as_of='2026-09-21T13:30:00+08:00',known_at='2026-09-21T19:00:00+08:00',
                sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for k,p in paths.items()])
    def prepare(self,**kwargs):
        with patch.object(entry,'build_config',return_value=copy.deepcopy(self.cfg)):
            return entry.prepare_signals(daily_path=kwargs.get('daily',self.daily_path),
                hourly_path=kwargs.get('hourly',self.hourly_path),state={'as_of':'2026-09-21'},
                source_manifest=kwargs.get('manifest',self.manifest()))
    def test_hash_mismatch_blocks(self):
        manifest=self.manifest();manifest['sources'][0]['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'hash mismatch'):self.prepare(manifest=manifest)
    def test_ambiguous_hourly_clock_blocks(self):
        path=self.path/'no_timezone.csv';x=self.hourly.copy();x.timestamp=x.timestamp.str[:-6];x.to_csv(path,index=False)
        with self.assertRaisesRegex(ValueError,'timezone'):
            self.prepare(hourly=path,manifest=self.manifest(hourly=path))
    def test_future_daily_values_cannot_change_prior_signals(self):
        original=self.prepare()['frame'].sort_values('symbol').reset_index(drop=True)
        future=self.daily[self.daily.date.eq('2026-09-21')].copy();future['date']='2026-09-22'
        for c in ['open','high','low','close']:future[c]=future[c]*100
        path=self.path/'future.csv';pd.concat([self.daily,future]).to_csv(path,index=False)
        clipped=self.prepare(daily=path,manifest=self.manifest(daily=path))['frame'].sort_values('symbol').reset_index(drop=True)
        pd.testing.assert_frame_equal(original,clipped,check_exact=True)
        self.assertEqual(set(clipped.symbol),set(self.symbols))
        self.assertTrue(np.isfinite(clipped.score).all())
    def test_missing_current_quote_is_not_forward_filled(self):
        path=self.path/'missing.csv';self.daily.iloc[:-1].to_csv(path,index=False)
        with self.assertRaisesRegex(ValueError,'150 current'):
            self.prepare(daily=path,manifest=self.manifest(daily=path))


if __name__=='__main__':unittest.main()
