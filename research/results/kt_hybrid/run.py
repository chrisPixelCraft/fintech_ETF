"""Mechanical execution of the frozen KT protocol; no strategy changes."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import hashlib
import json
import multiprocessing as mp
import subprocess
import time
import traceback
from datetime import datetime, timezone
from fractions import Fraction
import numpy as np
import pandas as pd
from competition import backtest
from competition.data import load_market
from competition.episodes import Episode, build_episodes
from competition.rules import ROOT, load_rules
from production import engine

DEST = Path(__file__).resolve().parent
sys.path.insert(0, str(DEST))
FROZEN = {
 DEST/'PREREGISTRATION.md': '3a9b758cfc72d7f2383c2e1924a8b9866057818c38c6b06a2c00f2e939e264cd',
 DEST/'strategies.py': '74c80550071d73d81fdf52be8e39a0e02441c58863fadcbb0aa06f1a070a1a4d',
 DEST.parent/'conc_tournament/strategies.py': 'c05cb770ff1eaec3ffec8f61d10cfc8e18dc693b7aecb5373402e0c6f6c31121',
 DEST.parent/'k3_weights/results.csv': 'd43c02400f18d73799d8000a49a8af20098a7e5705f1ce4f880bf7811895fce9'}
COMMAND = 'PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/kt_hybrid/run.py'
LOG = ROOT/'research/holdout_access_log.jsonl'
WORKERS = 8
STARTED = time.perf_counter()
ROWS, PROBLEMS, PHASES, CRASH = [], {}, {}, {}
CRASH_DATES = ['2026-10-15','2026-10-22','2026-10-20','2026-10-13','2026-10-12']


def digest(path):
 return hashlib.sha256(path.read_bytes()).hexdigest()


def check_frozen():
 for path, expected in FROZEN.items():
  assert digest(path) == expected, f'Frozen hash mismatch: {path}'


def dump(name, value):
 (DEST/name).write_text(json.dumps(value, indent=2, allow_nan=False, default=str)+'\n')


def git(*args):
 return subprocess.check_output(['git',*args],cwd=ROOT,text=True)


def execute(job):
 name, ep, phase = job
 strategy = STRATEGIES.KTHybrid(MARKET.calendar, RULES, MARKET.close.columns, ALPHAS[name])
 result = backtest.run_episode(MARKET, ep, strategy, RULES, engine.policy_of(BASE),
                              backtest.ExecutionConfig(**BASE['execution']))
 problems = backtest.verify_episode(MARKET, ep, result, RULES)
 s = result['summary']
 assert s['complete'], (name, ep.episode_id, 'incomplete')
 reasons = [dict(date=str(r.date),warning_reasons=str(r.warning_reasons))
            for r in result['ledger'].itertuples() if r.warning]
 assert len(reasons) == s['warning_days']
 row = dict(strategy=name, alpha=ALPHAS[name], window_id=ep.episode_id, phase=phase, split=ep.split,
            set='recent' if ep.split=='recent_diagnostic' else 'historical',
            start=str(ep.start.date()),end=str(ep.end.date()),
            **{k:s[k] for k in ['terminal_return','max_drawdown','costs','turnover','warning_days',
                                'disqualified','benchmark_0050_return','complete']},
            delta_vs_0050=s['terminal_return']-s['benchmark_0050_return'],
            warning_reasons=json.dumps(reasons,allow_nan=False),verify_problem_count=len(problems))
 crash = {}
 if ep.episode_id == 'W5':
  ledger = result['ledger']
  daily = ledger.nav / np.r_[RULES.initial_capital, ledger.nav.to_numpy()[:-1]] - 1
  by_date = dict(zip(pd.to_datetime(ledger.date).dt.strftime('%Y-%m-%d'),daily))
  crash = dict(daily_returns={d:float(by_date[d]) for d in CRASH_DATES},
               compounded_return=float(np.prod([1+by_date[d] for d in CRASH_DATES])-1))
 return row, problems, crash


def collect(value,pilot=False):
 row,problems,crash = value
 ROWS.append(row)
 PROBLEMS[f"{row['strategy']}:{row['window_id']}"] = problems
 if crash:
  CRASH[row['strategy']] = crash
 if pilot:
  assert not problems, problems
 if pilot or len(ROWS)%50==0:
  print('PILOT' if pilot else 'PROGRESS',len(ROWS),row['phase'],row['strategy'],flush=True)


def run_phase(phase,windows,names,pilot=False):
 start = time.perf_counter()
 jobs = [(n,e,phase) for e in windows for n in names]
 done = set()
 if pilot:
  for ep in windows[:3]:
   collect(execute((names[0],ep,phase)),pilot=True)
   done.add((names[0],ep.episode_id))
 pending = [j for j in jobs if (j[0],j[1].episode_id) not in done]
 with mp.get_context('fork').Pool(WORKERS) as pool:
  for value in pool.imap(execute,pending,chunksize=1):
   collect(value)
 frame = pd.DataFrame([r for r in ROWS if r['phase']==phase])
 assert len(frame)==len(jobs) and frame.complete.all()
 assert not frame.duplicated(['strategy','window_id']).any()
 finite(frame)
 PHASES[phase] = dict(expected=len(jobs),processed=len(frame),failed=0,seconds=time.perf_counter()-start,strategies=names)
 pd.DataFrame(ROWS).to_csv(DEST/'results.csv',index=False)
 dump('metrics.json',dict(status='IN_PROGRESS',phases=PHASES,verification_problems=PROBLEMS))
 return frame


def finite(frame):
 assert not frame.isna().any().any()
 assert np.isfinite(frame.select_dtypes(include='number').to_numpy()).all()


def metric(frame):
 assert len(frame)
 d = frame.delta_vs_0050
 up,down = int((d>=.05).sum()),int((d<=-.05).sum())
 return dict(count=len(frame),gain_5pp_count=up,loss_5pp_count=down,
  p_gain_5pp=up/len(frame),p_gain_10pp=float((d>=.10).mean()),p_loss_5pp=down/len(frame),
  tail_edge=(up-down)/len(frame),stability_score=(up-2*down)/len(frame),
  mean_delta_vs_0050=float(d.mean()),median_delta_vs_0050=float(d.median()),win_rate=float((d>0).mean()),
  total_warning_days=int(frame.warning_days.sum()),max_warning_days=int(frame.warning_days.max()),
  disqualifications=int(frame.disqualified.sum()),mean_turnover=float(frame.turnover.mean()),
  mean_costs=float(frame.costs.mean()),p90_max_drawdown=float(frame.max_drawdown.quantile(.9)),
  verify_problem_count=int(frame.verify_problem_count.sum()))


def summary(frame,names,period):
 return [dict(period=period,strategy=n,**metric(frame[frame.strategy==n])) for n in names]


def reference_for(reference,windows,names):
 part = reference[reference.window_id.isin([e.episode_id for e in windows]) & reference.strategy.isin(names)].copy()
 assert len(part)==len(windows)*len(names)
 assert not part.duplicated(['strategy','window_id']).any()
 for ep in windows:
  refs = part[part.window_id==ep.episode_id]
  assert len(refs)==len(names)
  assert all(r.split==ep.split and pd.Timestamp(r.start)==ep.start and pd.Timestamp(r.end)==ep.end for r in refs.itertuples())
 return part


def main():
 global MARKET,RULES,BASE,STRATEGIES,ALPHAS
 check_frozen()
 before_status = git('status','--porcelain')
 assert len(before_status.splitlines())==49
 log_before = LOG.read_bytes()
 assert len(log_before.splitlines())==99 and log_before.endswith(b'\n')
 protected = {p:digest(p) for p in DEST.iterdir() if p.is_file()}
 outside = {}
 raw = subprocess.check_output(['git','status','--porcelain','-z','--untracked-files=all'],cwd=ROOT)
 for entry in raw.split(b'\0'):
  if entry:
   p = ROOT/entry[3:].decode()
   if p.is_file() and not p.is_relative_to(DEST) and p!=LOG:
    outside[p]=digest(p)
 import strategies as STRATEGIES
 assert tuple(STRATEGIES.ALPHAS)==(.25,.5,.75)
 ALPHAS = {f'KT_{a:g}':a for a in STRATEGIES.ALPHAS}
 names = list(ALPHAS)
 BASE = json.loads((ROOT/'production/strategy.json').read_text())
 RULES = load_rules()
 source = ROOT/'production_runs/preflight_20261026'
 MARKET = load_market(yahoo_path=source/'strategy_retest/corrected_nominal_daily.parquet',
                      official_path=source/'official_combined.csv',calendar_path=source/'calendar_v2.json')
 windows = {s:build_episodes(MARKET.calendar,s,offsets=('month_start','mid_month'),start='2015-01-01',data_end='2026-10-22')
            for s in ['dev','validation','holdout']}
 assert {s:len(w) for s,w in windows.items()}==dict(dev=166,validation=70,holdout=40)
 reference = pd.read_csv(DEST.parent/'k3_weights/results.csv',float_precision='round_trip')
 end = MARKET.position('2026-10-22')+1
 recent=[]
 for i in range(5):
  days=tuple(MARKET.calendar[end-120+i*24:end-96+i*24])
  assert len(days)==24
  recent.append(Episode(f'W{i+1}','recent_diagnostic',MARKET.calendar[MARKET.position(days[0])-1],days))
 assert recent[0].start==pd.Timestamp('2026-04-30')
 assert recent[-1].start==pd.Timestamp('2026-09-16') and recent[-1].end==pd.Timestamp('2026-10-22')
 # DEV barrier: outcomes for later periods are not used.
 dev_control=reference_for(reference,windows['dev'],['K2b'])
 dev=run_phase('dev',windows['dev'],names,pilot=True)
 dev_stats=summary(dev,names,'dev')
 for r in dev_stats:
  r['alpha']=ALPHAS[r['strategy']]
  r['eligible']=r['disqualifications']==0 and r['max_warning_days']<=1 and r['total_warning_days']<=2
 candidates=[r for r in dev_stats if r['eligible']]
 assert candidates, 'No eligible alpha; no fallback is preregistered'
 selected_row=max(candidates,key=lambda r:(Fraction(r['gain_5pp_count']-2*r['loss_5pp_count'],r['count']),r['alpha']))
 selected=selected_row['strategy']
 selection=dict(selected_strategy=selected,selected_alpha=ALPHAS[selected],
  rule='eligible: DQ=0, max warning<=1, total warnings<=2; maximize exact stability score, ties larger alpha',
  eligible=[r['strategy'] for r in candidates],selected_metrics=selected_row,dev_k2b_control=metric(dev_control))
 pd.DataFrame(dev_stats).to_csv(DEST/'dev_grid.csv',index=False)
 dump('selection.json',selection)
 assert LOG.read_bytes()==log_before
 print('DEV_SELECTED',selected,flush=True)
 # VALIDATION: exactly one selected alpha.
 assert (DEST/'selection.json').exists()
 validation=run_phase('validation',windows['validation'],[selected])
 validation_control=reference_for(reference,windows['validation'],['K2b'])
 validation_stats=summary(pd.concat([validation,validation_control]),[selected,'K2b'],'validation')
 s,k=[metric(f) for f in [validation,validation_control]]
 gates=dict(
  V1=dict(passed=s['p_loss_5pp']<=k['p_loss_5pp'],selected=s['p_loss_5pp'],maximum=k['p_loss_5pp']),
  V2=dict(passed=s['p_gain_5pp']>=k['p_gain_5pp']-.03,selected=s['p_gain_5pp'],minimum=k['p_gain_5pp']-.03),
  V3=dict(passed=s['mean_delta_vs_0050']>=k['mean_delta_vs_0050']-.003,selected=s['mean_delta_vs_0050'],minimum=k['mean_delta_vs_0050']-.003),
  V4=dict(passed=s['disqualifications']==0 and s['total_warning_days']==0,disqualifications=s['disqualifications'],warning_days=s['total_warning_days'],maximum_each=0))
 decision=dict(decision='ADOPT' if all(g['passed'] for g in gates.values()) else 'REJECT',
  selected_strategy=selected,selected_alpha=ALPHAS[selected],gates=gates,k2b_control=k,automatic_strategy_change=False)
 pd.DataFrame(validation_stats).to_csv(DEST/'validation.csv',index=False)
 dump('decision.json',decision)
 assert LOG.read_bytes()==log_before
 print('VALIDATION',decision['decision'],flush=True)
 # REPORT: persist decision before logging and accessing report outcomes.
 assert json.loads((DEST/'decision.json').read_text())==decision
 with LOG.open('ab') as out:
  for name in names:
   record=dict(utc=datetime.now(timezone.utc).isoformat(),run_id=f'{name}__holdout_kt_hybrid',
    config=str(DEST/'strategies.py'),config_hash=digest(DEST/'strategies.py'),
    git_commit=git('rev-parse','HEAD').strip(),git_dirty=bool(before_status),argv=sys.argv)
   out.write((json.dumps(record)+'\n').encode())
 report=run_phase('report',windows['holdout']+recent,names)
 control=reference_for(reference,windows['holdout']+recent,['K2b','K3w_05'])
 other=pd.read_csv(DEST.parent/'conc_tournament/results.csv',float_precision='round_trip')
 control_other=reference_for(other,windows['holdout']+recent,['K2','T2'])
 combined=pd.concat([report,control,control_other])
 report_stats=[]
 for period,split in [('holdout','holdout'),('recent','recent_diagnostic')]:
  report_stats+=summary(combined[combined.split==split],names+['K2b','K2','T2','K3w_05'],period)
 pd.DataFrame(report_stats).to_csv(DEST/'report.csv',index=False)
 recent_rows=[]
 for ep in recent:
  row=dict(window_id=ep.episode_id,start=str(ep.start.date()),end=str(ep.end.date()))
  for name in names+['K2b','T2']:
   row[name]=float(combined[(combined.strategy==name)&(combined.window_id==ep.episode_id)].delta_vs_0050.item())
  recent_rows.append(row)
 pd.DataFrame(recent_rows).to_csv(DEST/'recent_windows.csv',index=False)
 # K2b's source CSV is episode-level only; never rerun or substitute K2 for K2b.
 CRASH['K2b']=dict(status='UNAVAILABLE',reason='No daily K2b ledger in supplied control outputs; episode aggregates cannot identify five-session compounded return. No rerun authorized.')
 frame=pd.DataFrame(ROWS)
 assert len(frame)==703 and len(PROBLEMS)==703 and sum(map(len,PROBLEMS.values()))==0
 assert {p:v['processed'] for p,v in PHASES.items()}==dict(dev=498,validation=70,report=135)
 joined=frame.merge(reference[reference.strategy=='K2b'][['window_id','benchmark_0050_return']],on='window_id',suffixes=('','_control'),validate='many_to_one')
 assert len(joined)==703 and np.allclose(joined.benchmark_0050_return,joined.benchmark_0050_return_control,rtol=0,atol=1e-12)
 check_frozen()
 assert all(digest(p)==h for p,h in protected.items())
 assert all(digest(p)==h for p,h in outside.items())
 assert all(digest(ROOT/p)==h for p,h in MARKET.provenance.items())
 log_after=LOG.read_bytes()
 assert log_after.startswith(log_before) and len(log_after.splitlines())==102
 added=[json.loads(x) for x in log_after[len(log_before):].splitlines()]
 assert [r['run_id'] for r in added]==[f'{n}__holdout_kt_hybrid' for n in names]
 assert git('status','--porcelain')==before_status
 for name in ['dev_grid.csv','validation.csv','report.csv','results.csv','recent_windows.csv']:
  finite(pd.read_csv(DEST/name,float_precision='round_trip'))
 checks=dict(episodes_expected=703,episodes_processed=703,episodes_failed=0,pilot_episodes=3,
  verification_calls=703,verification_problem_count=0,window_ids_dates_match=True,
  frozen_hashes_unchanged=True,protected_inputs_unchanged=True,dirty_files_unchanged=True,
  market_provenance_unchanged=True,baseline_git_status_lines=49,git_status_unchanged=True,
  holdout_prior_lines=99,holdout_added_lines=3,holdout_prior_bytes_unchanged=True,
  holdout_prior_bytes_sha256=hashlib.sha256(log_before).hexdigest(),finite_nonmissing_outputs=True,
  benchmark_matches_controls=True,k2b_crash_return_available=False)
 elapsed=time.perf_counter()-STARTED
 dump('metrics.json',dict(status='COMPLETE_WITH_REPORT_GAP',command=COMMAND,workers=WORKERS,elapsed_seconds=elapsed,
  phases=PHASES,checks=checks,verification_problems=PROBLEMS,dev=dev_stats,dev_k2b_control=metric(dev_control),
  validation=validation_stats,report=report_stats,recent=recent_rows,crash_days=CRASH,
  frozen_sha256={str(p.relative_to(ROOT)):h for p,h in FROZEN.items()}))
 for name in ['selection.json','decision.json','metrics.json']:
  json.loads((DEST/name).read_text(),parse_constant=lambda v:(_ for _ in ()).throw(ValueError(v)))
 write_report(selection,decision,dev_stats,validation_stats,report_stats,recent_rows,checks,elapsed)
 check_frozen()
 assert git('status','--porcelain')==before_status and LOG.read_bytes()==log_after
 print('COMPLETE_WITH_REPORT_GAP',703,decision['decision'],f'{elapsed:.2f}s',flush=True)


def write_report(selection,decision,dev,validation,report,recent,checks,elapsed):
 lines=['# CODEX_REPORT','',
  f"- Status: COMPLETE_WITH_REPORT_GAP; validation {decision['decision']}; no production change.",
  '- Changed files: run.py, dev_grid.csv, selection.json, validation.csv, decision.json, report.csv, recent_windows.csv, results.csv, metrics.json, CODEX_REPORT.md; research/holdout_access_log.jsonl appended 3 lines.',
  '- Items expected/processed/failed: 703/703/0 episodes (DEV 498; validation 70; report 135). K2b crash-days statistic: unavailable.',
  '- Asserts: 3-window × 1-alpha pilot reused; all episodes complete, 703 verify calls, 0 problems; window counts and control IDs/dates match; outputs finite/nonmissing; 4 frozen hashes, prior dirty files, market provenance and 49-line Git status unchanged; prior 99 log lines byte-identical.',
  f"- Selection: alpha={selection['selected_alpha']}; exact rational stability score; tied scores choose larger alpha.",
  '- Plan deviations / literal conventions: no strategy, architecture, grid or rule changes. V1–V3 use actual same-window K2b values (parenthetical preregistration values are rounded). Win means Δ50 > 0; tail edge = P(≥+5pp) − P(≤−5pp); turnover/costs are episode means; P90 uses linear quantiles. Crash sessions use daily NAV returns, compounded only over the five specified dates; no terminal dividend credit allocated to individual sessions. K2b crash return is unavailable: supplied results.csv contains no daily path, and no K2b daily ledger exists in prior results. No rerun or K2 substitution.',
  f'- Execution: `{COMMAND}`; repository root; {elapsed:.2f}s; {WORKERS} fork workers with ordered imap. DEV outputs persisted before validation; decision persisted before holdout logging and report.',
  '- Quota: unavailable; one frozen grid batch, no adaptive search.',
  '- Blockers: K2b five-session crash return requires an existing K2b daily ledger or separate authorization to rerun; all other requested computations completed.','',
  '| DEV strategy | α | Eligible | ≥+5pp | ≥+10pp | ≤−5pp | Tail | Stability | Mean Δ50 | Warn total/max | DQ |',
  '|---|---|---|---|---|---|---|---|---|---|---|']
 for r in dev:
  lines.append(f"| {r['strategy']} | {r['alpha']} | {r['eligible']} | {r['p_gain_5pp']:.2%} | {r['p_gain_10pp']:.2%} | {r['p_loss_5pp']:.2%} | {r['tail_edge']:.2%} | {r['stability_score']:.2%} | {r['mean_delta_vs_0050']:.2%} | {r['total_warning_days']}/{r['max_warning_days']} | {r['disqualifications']} |")
 k=selection['dev_k2b_control']
 lines += ['',f"- K2b DEV reference: ≥+5pp {k['p_gain_5pp']:.2%}, ≤−5pp {k['p_loss_5pp']:.2%}, stability {k['stability_score']:.2%}, mean Δ50 {k['mean_delta_vs_0050']:.2%}.",'',
  '| Validation gate | Passed | Numbers (decimal returns/probabilities) |','|---|---|---|']
 for n,g in decision['gates'].items():
  lines.append(f"| {n} | {g['passed']} | {json.dumps({k:v for k,v in g.items() if k!='passed'})} |")
 lines += ['', '| Period | Strategy | N | Mean Δ50 | Median Δ50 | Win | ≥+5pp | ≥+10pp | ≤−5pp | Tail | Warn total/max | DQ | Turnover | Costs TWD | P90 MDD |',
  '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
 for r in validation+report:
  vals=[r[k] for k in ['mean_delta_vs_0050','median_delta_vs_0050','win_rate','p_gain_5pp','p_gain_10pp','p_loss_5pp','tail_edge']]
  lines.append(f"| {r['period']} | {r['strategy']} | {r['count']} | "+' | '.join(f'{v:.2%}' for v in vals)+f" | {r['total_warning_days']}/{r['max_warning_days']} | {r['disqualifications']} | {r['mean_turnover']:.4f} | {r['mean_costs']:.2f} | {r['p90_max_drawdown']:.2%} |")
 ns=list(ALPHAS)+['K2b','T2']
 lines+=['','| Recent Δ50 | Dates | '+' | '.join(ns)+' |','|---|---|'+'---|'*len(ns)]
 for r in recent:
  lines.append(f"| {r['window_id']} | {r['start']}–{r['end']} | "+' | '.join(f'{r[n]:.2%}' for n in ns)+' |')
 lines+=['','| W5 crash dates | Strategy | Compounded return |','|---|---|---|']
 for n,c in CRASH.items():
  value=f"{c['compounded_return']:.2%}" if 'compounded_return' in c else 'UNAVAILABLE (missing daily control data; rerun prohibited)'
  lines.append(f"| {', '.join(CRASH_DATES)} | {n} | {value} |")
 lines+=['','- Full precision metrics: CSV/JSON outputs. Per-episode warning dates/reasons: results.csv. Crash daily returns and verification details: metrics.json. Controls are stored values, never rerun.']
 (DEST/'CODEX_REPORT.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
 try:
  main()
 except Exception as exc:
  (DEST/'CODEX_REPORT.md').write_text('# CODEX_REPORT\n\n- Status: STOPPED\n'
   '- Changed files: run.py, CODEX_REPORT.md and partial allowed outputs only.\n'
   f'- Items expected/processed/failed: 703/{len(ROWS)}/incomplete.\n'
   f'- Asserts / Blockers: {exc!r}\n- Plan deviations: stopped at failed check; no strategy edits.\n'
   f'- Execution: `{COMMAND}`; workers={WORKERS}; {time.perf_counter()-STARTED:.2f}s.\n'
   '- Quota: unavailable.\n\n```\n'+traceback.format_exc()+'```\n')
  raise
