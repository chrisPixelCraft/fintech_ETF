"""Read-only strategy regression; writes only this result directory."""
import argparse
import collections
import hashlib
import json
import subprocess
import time
from pathlib import Path
import pandas as pd
from competition import backtest, episodes as ep
from competition.data import load_market
from competition.rules import load_rules, RULES_PATH
from production import replay

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-prefix', default='baseline')
args = parser.parse_args()
if not args.output_prefix.replace('_', '').isalnum():
    raise ValueError('Output prefix must contain only letters, numbers or underscore')
prefix = args.output_prefix
strategy_path = ROOT / 'production/strategy.json'
strategy = json.loads(strategy_path.read_text())
start = time.perf_counter()
market, rules = load_market(), load_rules()
data_end = market.close.dropna(how='all').index[-1]
original = backtest.run_episode
summaries = []

def capture(*args, **kwargs):
    result = original(*args, **kwargs)
    problems = backtest.verify_episode(args[0], args[1], result, args[3])
    summary = dict(result['summary'], verification_problems=problems,
                   cash_ratio_min=float(result['ledger'].cash_ratio.min()),
                   cash_ratio_max=float(result['ledger'].cash_ratio.max()),
                   holdings_min=int(result['ledger'].holdings.min()),
                   holdings_max=int(result['ledger'].holdings.max()),
                   issues=result['issues'].to_dict('records'))
    summaries.append(summary)
    return result

backtest.run_episode = capture
batches = []
all_rows = []
for split, n in [('dev',3), ('validation',3), ('holdout',40)]:
    windows = ep.build_episodes(market.calendar, split, rules.episode_sessions,
                               tuple(strategy['episodes']['offsets']), data_end, start='2019-01-01' if split == 'dev' else None)
    chosen = ep.select(windows,n)
    before = time.perf_counter()
    rows = []
    for episode in chosen:
        rows.extend(replay.replay_episode(market,episode,strategy,rules))
        print(episode.episode_id, len(rows), round(time.perf_counter()-before,2), flush=True)
    batch = dict(split=split, elapsed_seconds=time.perf_counter()-before,
                 selected=[e.episode_id for e in chosen], **replay.verdict(rows))
    batches.append(batch)
    all_rows.extend(rows)
    pd.DataFrame(all_rows).to_csv(OUT/f'{prefix}_days.csv',index=False)
    (OUT/f'{prefix}_progress.json').write_text(json.dumps(batches,indent=2,default=str))
    if split == 'validation' and time.perf_counter()-start > 120:
        break

sources = collections.Counter()
for s in summaries:
    sources.update(s['fill_sources'])
result = dict(status='REPLAY_REGRESSION_ONLY', elapsed_seconds=time.perf_counter()-start,
              command='PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/rules_v43_validation/run_baseline.py --output-prefix ' + prefix,
              strategy=strategy, strategy_sha256=hashlib.sha256(strategy_path.read_bytes()).hexdigest(),
              code_manifest={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for folder in ['competition', 'production', 'research']
                             for p in sorted((ROOT / folder).glob('*.py'))},
              rules_sha256=hashlib.sha256(RULES_PATH.read_bytes()).hexdigest(),
              data_end=str(data_end), market_provenance=market.provenance,
              git_commit=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True).stdout.strip(),
              batches=batches, episode_count=len(summaries), day_count=len(all_rows),
              canonical_summaries=summaries, fill_sources=dict(sources),
              warning_days=sum(s['warning_days'] for s in summaries),
              disqualified=sum(s['disqualified'] for s in summaries),
              verification_problems=sum(len(s['verification_problems']) for s in summaries),
              active_share=dict(etf_datasets_supplied=0, coverage='UNVERIFIED',
                                reason='replay DayInput supplies no etfs or required_etfs'),
              limitations=['Historical holdout already observed; replay only, no tuning or fresh generalization claim.',
                           'Monthly and mid-month 24-day windows overlap; observations are not independent.',
                           'Universe is fixed 2026 list; survivorship/selection bias, strongest for early dev years.',
                           'Historical local D-Plan schema and local rules at execution; not independent official v4.3 certification.',
                           'No real organizer DataPack account/settlement integration or historical ETF holdings supplied.',
                           'Some historical fills may use HLC3 proxies.'])
(OUT/f'{prefix}.json').write_text(json.dumps(result,indent=2,default=str))
print(json.dumps({k:result[k] for k in ['elapsed_seconds','episode_count','day_count','warning_days','disqualified','verification_problems','fill_sources']},indent=2),flush=True)
