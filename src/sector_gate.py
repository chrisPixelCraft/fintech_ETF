"""Point-in-time sector gate. Unknown historical taxonomy never becomes a guessed label."""
from pathlib import Path
import numpy as np
import pandas as pd


class SectorGate:
    def __init__(self, history_path, index_path, calendar):
        self.calendar = pd.DatetimeIndex(pd.to_datetime(calendar)).sort_values().unique()
        self.history = pd.read_csv(history_path, dtype={'symbol': str})
        self.history['known_at'] = pd.to_datetime(self.history.known_at, utc=True, errors='coerce')
        for name in ['effective_from', 'effective_to']:
            self.history[name] = pd.to_datetime(self.history[name], errors='coerce')
        self.indices = pd.read_csv(index_path)
        if 'index_type' in self.indices:
            self.indices=self.indices[self.indices.index_type.eq('TOTAL_RETURN')].copy()
        # Parent aggregates overlap their child industries and must not get extra votes.
        self.indices=self.indices[~self.indices.sector_id.astype(str).str.contains(r'(?:_|:)(?:07|13)$',regex=True)].copy()
        self.indices['date'] = pd.to_datetime(self.indices.date)
        self.indices['available_at'] = pd.to_datetime(self.indices.available_at, utc=True, errors='coerce')
        self.indices = self.indices.sort_values(['sector_id','date'])

    def __call__(self, signal_day, ranked):
        day = pd.Timestamp(signal_day)
        later = self.calendar[self.calendar > day]
        next_day = later[0] if len(later) else day + pd.offsets.BDay()
        cutoff = (next_day.tz_localize('Asia/Taipei') + pd.Timedelta(hours=8,minutes=55)).tz_convert('UTC')
        out = ranked.copy()
        out['base_score'] = out['score']
        out['sector_id'] = 'UNKNOWN'
        out['sector_score'] = .5
        out['sector_gate'] = True
        history = self.history
        valid = history[(history.effective_from <= day) & (history.effective_to.isna() | (day < history.effective_to)) & (history.known_at <= cutoff)]
        if valid.symbol.duplicated().any():
            raise ValueError('Overlapping point-in-time sector classifications')
        mapping = valid.set_index('symbol').sector_id.to_dict()
        out['sector_id'] = out.symbol.map(mapping).fillna('UNKNOWN')
        available = self.indices[(self.indices.date <= day) & (self.indices.available_at <= cutoff)]
        stats = []
        expected=self.calendar[self.calendar<=day][-51:]
        for sector, rows in available.groupby('sector_id',sort=True):
            rows = rows.sort_values('date').drop_duplicates('date',keep='last')
            p = rows.close.astype(float)
            if len(p) < 51 or not np.isfinite(p).all() or (p <= 0).any(): continue
            if len(expected)<51 or not pd.DatetimeIndex(rows.date.tail(51)).equals(pd.DatetimeIndex(expected)):
                continue
            stats.append(dict(sector_id=sector,return20=p.iloc[-1]/p.iloc[-21]-1,
                              return50=p.iloc[-1]/p.iloc[-51]-1,
                              trend=bool(p.iloc[-1] > p.ewm(span=20,adjust=False).mean().iloc[-1] > p.ewm(span=50,adjust=False).mean().iloc[-1]),
                              last_date=str(rows.date.iloc[-1].date())))
        meta = dict(sector_cutoff=cutoff.isoformat(),sector_unknown_count=int(out.sector_id.eq('UNKNOWN').sum()),sector_fallback=[])
        if not stats:
            out['sector_status'] = 'UNKNOWN_NEUTRAL_BASELINE_FALLBACK'
            meta.update(sector_status='UNKNOWN_NEUTRAL_BASELINE_FALLBACK',sector_fallback=['NO_PIT_SECTOR_DATA'])
            return out, meta
        sectors = pd.DataFrame(stats).set_index('sector_id')
        sectors['score'] = (8/15)*sectors.return20.rank(pct=True)+(7/15)*sectors.return50.rank(pct=True)
        sectors = sectors.sort_values(['score','sector_id'],ascending=[False,True])
        upper = set(sectors.index[:int(np.ceil(len(sectors)/2))])
        allowed = {s for s in upper if sectors.at[s,'trend']}
        def count(): return int((out.entry_ok & out.sector_id.isin(allowed)).sum())
        if count() < 25:
            for sector in sectors.index:
                if sector not in allowed and sectors.at[sector,'trend']:
                    allowed.add(sector)
                    meta['sector_fallback'].append('SECTOR_RANK_FALLBACK:'+sector)
                    if count() >= 25: break
        if count() < 20:
            for sector in sectors.index:
                if sector not in allowed:
                    allowed.add(sector)
                    meta['sector_fallback'].append('WEAK_MARKET_FALLBACK:'+sector)
                    if count() >= 20: break
        known = out.sector_id.isin(sectors.index)
        # Missing taxonomy is explicitly neutral. It cannot support a sector-effect claim.
        out['sector_gate'] = ~known | out.sector_id.isin(allowed)
        out['entry_ok'] &= out.sector_gate
        out['sector_score'] = out.sector_id.map(sectors.score).fillna(.5)
        stale_sectors={s for s in sectors.index if (day-pd.Timestamp(sectors.at[s,'last_date'])).days>7}
        out['sector_status'] = np.where(known,np.where(out.sector_id.isin(stale_sectors),'STALE_INDEX','AVAILABLE'),'UNKNOWN_NEUTRAL_BASELINE_FALLBACK')
        meta.update(sector_status='PARTIAL' if (~known).any() or stale_sectors else 'AVAILABLE',
                    sector_known_count=int(known.sum()),sector_allowed=sorted(allowed),
                    stale_sectors=sorted(stale_sectors),
                    sector_scores=sectors.reset_index().to_dict('records'))
        return out, meta
