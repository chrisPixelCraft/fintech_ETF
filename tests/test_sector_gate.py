import tempfile
import unittest
from pathlib import Path
import pandas as pd
from src.sector_gate import SectorGate


class SectorGateTests(unittest.TestCase):
    def setup_gate(self, late=False, future_price=100):
        folder=Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree,folder)
        dates=pd.bdate_range('2024-08-01',periods=120)
        pd.DataFrame([dict(symbol=f'S{i}',sector_id='GOOD' if i<25 else 'BAD',effective_from='2024-01-01',effective_to='',known_at='2026-01-01T00:00:00Z' if late else '2024-01-01T00:00:00Z') for i in range(50)]).to_csv(folder/'history.csv',index=False)
        rows=[]
        for j,day in enumerate(dates):
            for sector in ['GOOD','BAD']:
                price=(100+j if sector=='GOOD' else 220-j) if j<100 else future_price
                rows.append(dict(date=str(day.date()),sector_id=sector,close=price,available_at=str(day.tz_localize('Asia/Taipei')+pd.Timedelta(hours=19))))
        pd.DataFrame(rows).to_csv(folder/'index.csv',index=False)
        ranked=pd.DataFrame([dict(symbol=f'S{i}',score=.5,entry_ok=True,exit=False) for i in range(50)]).set_index('symbol',drop=False)
        return SectorGate(folder/'history.csv',folder/'index.csv',dates),ranked,dates[99]

    def test_future_prices_cannot_change_gate(self):
        gate,ranked,day=self.setup_gate(future_price=100)
        other,_,_=self.setup_gate(future_price=1e12)
        a,am=gate(day,ranked);b,bm=other(day,ranked)
        pd.testing.assert_frame_equal(a,b);self.assertEqual(am,bm)
        self.assertEqual(int(a.entry_ok.sum()),25)

    def test_late_classification_never_backfills(self):
        gate,ranked,day=self.setup_gate(late=True)
        out,meta=gate(day,ranked)
        self.assertTrue(out.sector_id.eq('UNKNOWN').all())
        self.assertEqual(meta['sector_unknown_count'],50)

    def test_gate_changes_entry_not_exit(self):
        gate,ranked,day=self.setup_gate()
        out,_=gate(day,ranked)
        self.assertTrue(out.exit.equals(ranked.exit))
        self.assertFalse(out.loc['S49','entry_ok'])

if __name__=='__main__':unittest.main()
