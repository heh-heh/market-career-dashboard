#!/usr/bin/env python3
"""Read-only V081/V082 paired artifact comparison; never trains or touches OOS."""
import argparse
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research.evolution.data_access import check_dates
from research.backtest_v4_strategies import file_sha256
from research.backtest_intraday_v4 import atomic_json


def summarize(folder):
    folder=Path(folder);info=json.loads((folder/'run.json').read_text());check_dates(info['fromDate'],info['toDate'])
    if info.get('phase')!='completed':raise ValueError('COMPLETED_RESEARCH_RUN_REQUIRED')
    windows=[]
    for p in sorted(folder.glob('window_*.json')):
        row=json.loads(p.read_text());bots=row.get('bots',[])
        if len(row.get('dates',[]))!=5 or not bots or any(not b.get('evaluationValid') for b in bots):continue
        windows.append(dict(dates=row['dates'],meanReturn=statistics.mean(b['windowReturn'] for b in bots),
            meanMdd=statistics.mean(b['windowMaxDrawdown'] for b in bots),meanFitness=statistics.mean(b['fitness'] for b in bots),
            netPnl=sum(b['windowPnl'] for b in bots),trades=sum(b['trades'] for b in bots),cost=sum(b['transactionCost'] for b in bots)))
    if not windows:raise ValueError('NO_VALID_FIVE_SESSION_WINDOWS')
    return dict(path=str(folder),version=info['version'],population=info['population'],seed=info['seed'],
        config=info['config'],sourcePlanSha256=info['sourcePlanSha256'],fromDate=info['fromDate'],toDate=info['toDate'],
        inputManifest=info['inputManifest'],executionModel=info['executionModel'],dataOrigin=info['dataOrigin'],
        runSha256=file_sha256(folder/'run.json'),windows=windows,resourceReport=info.get('resourceReport'),
        meanWindowReturn=statistics.mean(w['meanReturn'] for w in windows),meanWindowMdd=statistics.mean(w['meanMdd'] for w in windows))


def compare(a,b):
    keys=('seed','sourcePlanSha256','fromDate','toDate','inputManifest','executionModel','dataOrigin')
    differences=[k for k in keys if a[k]!=b[k]]
    if [x['dates'] for x in a['windows']]!=[x['dates'] for x in b['windows']]:differences.append('selectedWindows')
    config_changes={k:dict(before=a['config'].get(k),after=b['config'].get(k)) for k in set(a['config'])|set(b['config']) if a['config'].get(k)!=b['config'].get(k)}
    return dict(pairedMarketConditions=not differences,differences=differences,configChanges=config_changes,
        populationEffectIsolated=not differences and set(config_changes)== {'population_size'},
        indicatorScoreEffectIsolated=not differences and set(config_changes)=={'indicator_policy_enabled'},
        caveat='V081 vs V082 changes portfolio structure as well as indicators; cannot attribute a bundled difference to indicators alone. No significance/profitability claim.',
        before=a,after=b,finalOosGate='CLOSED',researchOnly=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args(argv)
    if a.out.exists():p.error('Refusing artifact overwrite')
    result=compare(summarize(a.baseline),summarize(a.candidate));atomic_json(a.out,result);print(json.dumps({k:v for k,v in result.items() if k not in ('before','after')}))

if __name__=='__main__':main()
