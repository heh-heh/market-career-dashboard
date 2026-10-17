"""Read-only bounded classification of zero-generation runs and audited windows."""
from collections import Counter, defaultdict
import json
from pathlib import Path
from research.backtest_v4_strategies import exchange_sessions
from .available_data import BIAS, verify_plan


def diagnose(run_dir):
    root=Path(run_dir); counts=Counter();samples=defaultdict(list)
    for name in ('windows.jsonl','excluded_sessions.jsonl'):
        path=root/name
        if not path.is_file():continue
        with path.open() as f:
            for line in f:
                row=json.loads(line); reason=row.get('reason')
                if not reason:continue
                key=name+':'+reason;counts[key]+=1
                if len(samples[key])<5:samples[key].append(row)
    info=json.loads((root/'run.json').read_text())
    bot_failures=Counter();invalid_examples=[]
    for folder in root.glob('attempt_*'):
        path=folder/'metrics.json'
        if not path.is_file():continue
        data=json.loads(path.read_text())
        for row in data.get('bots',[]):
            if row.get('evaluationValid'):continue
            bot_failures[row.get('failureReason') or 'UNKNOWN_INVALID_ACCOUNT']+=1
            if len(invalid_examples)<10:
                invalid_examples.append(dict(attempt=folder.name,botId=row.get('botId'),reason=row.get('failureReason'),
                    cancellations=row.get('cancellations'),unresolvedSymbol=(row.get('unresolvedPosition') or {}).get('symbol'),
                    diagnosticSamples=data.get('diagnostics',{}).get('invalidBotSamples',[])))
    return dict(researchOnly=True,finalOosGate='CLOSED',completedGenerations=info.get('completedTrainGenerations'),
        causes=dict(counts),samples=dict(samples),invalidBotDays=dict(bot_failures),invalidBotSamples=invalid_examples,classification={
            'BENCHMARK_SESSION_INCOMPLETE':'DATA_REFERENCE_MISSING',
            'COHORT_HELD_SYMBOL_OBSERVATION_GAP':'HELD_OBSERVATION_GAP',
            'COHORT_UNRESOLVED_SESSION_EXIT':'UNLIQUIDATED_OR_INVALID_ACCOUNT',
            'TRAILING_INCOMPLETE_WINDOW':'CONSECUTIVE_WINDOW_INCOMPLETE'},
        warning='File-specific counts, not proof of another EC2 run. Window and excluded-session counts have different denominators.')


def smoke_window(plan, desired=10):
    # Existing audit acceptance only promises a usable prefix. For a bounded
    # pilot choose whole *days*, not hindsight-selected symbols/observations.
    # All prefix-tradable symbols must have complete data; absent/non-prefix
    # symbols remain in the original universe and cannot silently be substituted.
    streak=[];best=[];rejected=Counter();previous=None
    train=[s for s in plan['sessions'] if '2022-01-01'<=s['sessionDate']<='2023-12-31']
    for row in train:
        day=row['sessionDate']
        consecutive=previous is None or len(exchange_sessions(previous,day))==2
        previous=day
        good=row['eligible'] and not row.get('partialPrefixSymbols')
        if not good or not consecutive:
            rejected[row.get('reason') or ('PARTIAL_TRADABLE_SYMBOL_SESSION' if not good else 'NONCONSECUTIVE_CALENDAR')]+=1
            streak=[]
        if good:streak.append(day)
        if len(streak)>len(best):best=list(streak[:desired])
        if len(best)>=desired:break
    if len(best)<5:
        raise ValueError('NO_VERIFIED_FIVE_SESSION_PILOT_WINDOW: '+json.dumps(dict(rejected),sort_keys=True))
    best=best[:desired] if len(best)>=desired else best[:5]
    return dict(fromDate=best[0],toDate=best[-1],dates=best,sessions=len(best),
        selectionReason='AUDITED_CONSECUTIVE_DAYS_WITHOUT_PARTIAL_TRADABLE_PREFIXES',
        excludedReasons=dict(rejected),selectionBiasWarnings=BIAS+['EX_POST_COMPLETE_DAY_PILOT_SELECTION_NOT_AN_UNCONDITIONAL_PERFORMANCE_CLAIM'])


def main():
    import argparse
    from research.backtest_intraday_v4 import atomic_json
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-dir',type=Path);p.add_argument('--selection-plan',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('Refusing artifact overwrite')
    result={}
    if a.run_dir:result['runDiagnosis']=diagnose(a.run_dir)
    if a.selection_plan:
        plan,_=verify_plan(a.selection_plan);result['pilotWindow']=smoke_window(plan)
    if not result:p.error('Specify run-dir and/or selection-plan')
    atomic_json(a.out,result);print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
