#!/usr/bin/env python3
"""Paired sequential research: identical audited data/seed/population; selection only.

Never selects a winner or tunes using holdout/development validation. Runs only
explicit bounded 2022-2023 training; frozen evaluation uses the existing driver.
"""
import argparse
from dataclasses import replace
import json
import statistics
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.evolution.run_v07_available import parser as base_parser, run
from research.backtest_intraday_v4 import atomic_json
from research.evolution.available_data import new_output
from research.evolution.config import Config


def compare(control, candidate):
    a, b = [json.loads((p/'run.json').read_text()) for p in (control, candidate)]
    ca, cb = dict(a['config']), dict(b['config'])
    ca.pop('selection_policy', None); cb.pop('selection_policy', None)
    fields = ('seed', 'population', 'initialPopulationHash', 'sourcePlanSha256', 'symbols', 'fromDate', 'toDate', 'timestampKind', 'executionModel', 'codeHashes')
    mismatches = [k for k in fields if a.get(k) != b.get(k)]
    if ca != cb: mismatches.append('config_except_selection_policy')
    if a.get('selectionPolicy', 'legacy') != 'legacy' or b.get('selectionPolicy') != 'v08_profit_only':
        mismatches.append('selection_policy_pair')
    for name in ('used_sessions.json', 'evaluation_sessions.json'):
        if json.loads((control/name).read_text()) != json.loads((candidate/name).read_text()):
            mismatches.append(name)
    result = dict(researchOnly=True, finalOosGate='CLOSED', comparable=not mismatches,
                  mismatches=mismatches, warning='Selection diverges populations; no causal performance claim from one seed. Frozen 2024/2025 evaluation still required.')
    for label, root in (('v07', control), ('v08', candidate)):
        windows = [json.loads(p.read_text()) for p in sorted(root.glob('window_*.json'))]
        result[label] = dict(run=str(root), generations=len(windows),
            randomRecoveryCount=sum(w['selection']['randomRecovery'] for w in windows),
            windows=[dict(dates=w['dates'], selection=w['selection'],
                meanReturn=statistics.mean(e['windowReturn'] for e in w['bots']),
                bestReturn=max(e['windowReturn'] for e in w['bots']),
                worstReturn=min(e['windowReturn'] for e in w['bots']),
                meanMdd=statistics.mean(e['windowMaxDrawdown'] for e in w['bots']),
                meanFitness=statistics.mean(e['fitness'] for e in w['bots']),
                profitableBotRatio=sum(e['windowPnl'] > a['config']['epsilon'] for e in w['bots'])/len(w['bots']),
                uniqueGenomes=len({e.get('genomeHash') for e in w['bots']}) if all('genomeHash' in e for e in w['bots']) else None,
                bots=[{k:e.get(k) for k in ('botId','windowReturn','windowMaxDrawdown','fitness')} for e in w['bots']]) for w in windows])
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selection-plan', type=Path, required=True)
    p.add_argument('--from-date', required=True); p.add_argument('--to-date', required=True)
    p.add_argument('--results-dir', type=Path, required=True)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--require-ebs', action='store_true')
    a=p.parse_args(argv)
    from research.evolution.data_access import check_dates
    check_dates(a.from_date,a.to_date)
    if not '2022-01-01' <= a.from_date <= a.to_date <= '2023-12-31':
        p.error('Paired evolution restricted to 2022-2023')
    from uuid import uuid4
    # Source root is enforced again by run()/verify_plan. Never overwrite artifacts.
    plan=json.loads(a.selection_plan.read_text())
    output=new_output(a.results_dir/('pair-'+uuid4().hex),plan['dataRoot'])
    cfg=Config(**json.loads(Path(__file__).with_name('v08_profit_only_config.json').read_text()))
    directories=[]
    for policy in ('legacy','v08_profit_only'):
        config=replace(cfg,selection_policy=policy)
        path=output/(policy+'.json');atomic_json(path,config.snapshot())
        args=base_parser().parse_args(['--selection-plan',str(a.selection_plan),'--config',str(path),
            '--population','100','--seed',str(a.seed),'--from-date',a.from_date,'--to-date',a.to_date,
            '--results-dir',str(output/policy)]+(['--require-ebs'] if a.require_ebs else []))
        directories.append(run(args))
    atomic_json(output/'comparison.json',compare(*directories))
    print(output/'comparison.json')


if __name__ == '__main__': main()
