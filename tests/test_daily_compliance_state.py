import copy
import unittest
from daily_auto.compliance_state import _derive_ages, HistoryUnknown, resolve_passive_cap_days


class PassiveHistory(unittest.TestCase):
    def book(self,price=100,cash=10000,shares=100,tsmc=False):
        symbol='2330' if tsmc else '1101';value=price*shares
        return dict(cash=str(cash),nav=str(cash+value+80000),holdings=[
            dict(ticker=symbol,shares=str(shares),market_value=str(value)),
            *[dict(ticker=str(2000+i),shares='100',market_value='4000') for i in range(20)]])
    def test_five_sessions_then_sixth_overdue_excludes_weekend(self):
        dates=['2026-10-27','2026-10-28','2026-10-29','2026-10-30','2026-11-02','2026-11-03','2026-11-04']
        books={d:self.book(100 if i==0 else 102) for i,d in enumerate(dates)}
        self.assertEqual(_derive_ages(books,dates,dates[5])[0]['1101'],5)
        self.assertEqual(_derive_ages(books,dates,dates[6])[0]['1101'],6)
    def test_drop_under_cap_resets_age(self):
        ds=['2026-10-27','2026-10-28','2026-10-29','2026-10-30']
        bs={d:self.book(p) for d,p in zip(ds,[100,102,99,104])}
        self.assertEqual(_derive_ages(bs,ds,ds[-1])[0]['1101'],1)
    def test_added_shares_cannot_claim_passive_grace(self):
        ds=['2026-10-27','2026-10-28'];bs={ds[0]:self.book(),ds[1]:self.book(shares=110,cash=9000)}
        self.assertIsNone(_derive_ages(bs,ds,ds[-1])[0]['1101'])
    def test_missing_official_session_not_carried_forward(self):
        ds=['2026-10-27','2026-10-28','2026-10-29'];bs={ds[0]:self.book(),ds[2]:self.book(102)}
        self.assertIn('1101',_derive_ages(bs,ds,ds[-1])[1])
    def test_missing_valuation_is_unknown_not_zero(self):
        b=self.book();b['holdings'][0].pop('market_value')
        with self.assertRaisesRegex(HistoryUnknown,'VALUES_MISSING'):_derive_ages({'2026-10-27':b},['2026-10-27'],'2026-10-27')
    def test_tsmc_cap_is_twenty_five_percent(self):
        b=self.book(250,tsmc=True)
        self.assertEqual(_derive_ages({'2026-10-27':b},['2026-10-27'],'2026-10-27')[0]['2330'],0)
    def test_manual_age_never_used_without_official_evidence(self):
        ages,r,_=resolve_passive_cap_days(state={'holdings':[{'ticker':'1101','passive_cap_days':1}]})
        self.assertEqual(ages,{});self.assertEqual(r['status'],'UNKNOWN');self.assertIsNone(r['official_warning_count'])


if __name__=='__main__':unittest.main()
