"""Read-only, bounded V07 artifact analysis. No market reader, policy or orders.

CLI output must be a new path outside the source run. Missing evidence is UNKNOWN,
never a guessed trading result. Invalid attempts are excluded from selection stats.
"""
from collections import Counter
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics

MAX_JSON = 16 * 1024**2


def read(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON:
        raise ValueError('UNSAFE_OR_OVERSIZED_ARTIFACT: ' + path.name)
    with path.open() as f:
        return json.load(f)


def rows(path):
    path = Path(path)
    if not path.exists():
        return
    if path.is_symlink():
        raise ValueError('SYMLINK_ARTIFACT')
    with path.open() as f:
        for line in f:
            if len(line) > MAX_JSON:
                raise ValueError('OVERSIZED_ARTIFACT_ROW')
            if line.strip():
                yield json.loads(line)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def distribution(values):
    vals = [x for x in values if isinstance(x, (int, float))]
    return dict(mean=statistics.mean(vals), median=statistics.median(vals), best=max(vals), worst=min(vals)) if vals else None


def analyze(run, audit=None):
    run = Path(run)
    if run.is_symlink():
        raise ValueError('SYMLINK_RUN')
    info = read(run/'run.json', {})
    state = read(run/'status.json', {})
    if not info or info.get('finalOosGate') != 'CLOSED' or info.get('toDate', '') >= '2026':
        raise ValueError('RESEARCH_ONLY_PRE2026_RUN_REQUIRED')
    used = read(run/'used_sessions.json')
    evaluated = read(run/'evaluation_sessions.json')
    windows = list(rows(run/'windows.jsonl'))
    exclusions = list(rows(run/'excluded_sessions.jsonl'))
    selected = [w for w in windows if w['status'] == 'SELECTED']
    committed = [d for w in selected for d in w['dates']]
    invalid_dates = {x['date'] for x in exclusions}
    generations = []
    parent_counts = Counter()
    for w in selected:
        data = read(run/f"window_{w['generation']:04d}.json", {})
        bots = data.get('bots', [])
        selection = data.get('selection', {})
        parent_counts.update(selection.get('parentIds', []))
        generations.append(dict(generation=w['generation']+1, dates=w['dates'],
            returns=distribution([b.get('windowReturn') for b in bots]),
            fitness=distribution([b.get('fitness') for b in bots]),
            mdd=distribution([b.get('windowMaxDrawdown', b.get('maxDrawdown')) for b in bots]),
            costs=sum(b.get('transactionCost', 0) for b in bots),
            survival=dict(Counter(b.get('survival', 'UNKNOWN') for b in bots)), selection=selection))
    held_gaps = Counter(); cancellations = Counter(); invalid_bot_days = 0
    validation_daily = []; regime = {}; months = {}
    # Only immediate known attempt directories: never recurse into arbitrary logs/data.
    for child in sorted(run.glob('attempt_*')):
        if child.is_symlink() or not child.is_dir():
            raise ValueError('UNSAFE_ATTEMPT_PATH')
        metrics = read(child/'metrics.json', {})
        for b in metrics.get('bots', []):
            cancellations.update(b.get('cancellations', {}))
            n = b.get('cancellations', {}).get('HELD_SYMBOL_OBSERVATION_GAP', 0)
            if n:
                held_gaps[b['date']] += n
            invalid_bot_days += not b.get('evaluationValid', False)
            if info.get('sourceRunId') and used is not None and b.get('date') in used:
                validation_daily.append(dict(date=b['date'], botId=b['botId'], dailyReturn=b.get('dailyReturn'),
                    maxDrawdown=b.get('maxDrawdown'), trades=b.get('trades')))
        if info.get('sourceRunId') and used is not None and child.name[-10:] in used:
            trade_file = child/'trades.csv'
            if trade_file.is_file() and not trade_file.is_symlink():
                with trade_file.open() as f:
                    for t in csv.DictReader(f):
                        key = t.get('entryRegime', 'UNKNOWN')
                        x = regime.setdefault(key, dict(trades=0, pnlUsd=0.))
                        x['trades'] += 1; x['pnlUsd'] += float(t.get('pnlUsd') or 0)
                        month = t.get('exitTimestamp', '')[:7]
                        x = months.setdefault(month, dict(trades=0, pnlUsd=0.))
                        x['trades'] += 1; x['pnlUsd'] += float(t.get('pnlUsd') or 0)
    per_symbol = {}; benchmark = Counter()
    if audit:
        for row in rows(Path(audit)/'symbol_sessions.jsonl'):
            if not info['fromDate'] <= row['sessionDate'] <= info['toDate']:
                continue
            s = per_symbol.setdefault(row['symbol'], dict(sessions=0, missingSessions=0, missingMinutes=0, expectedMinutes=0))
            s['sessions'] += 1; s['missingSessions'] += not row['complete']
            s['missingMinutes'] += row['missingMinutes']; s['expectedMinutes'] += row['expectedMinutes']
            if row['symbol'] in ('SPY', 'QQQ', 'SOXX') and not row['complete']:
                benchmark[row['symbol']] += 1
        for x in per_symbol.values():
            x['missingPct'] = 100*x['missingMinutes']/x['expectedMinutes'] if x['expectedMinutes'] else None
    validation_bots = {}
    for b in validation_daily:
        x = validation_bots.setdefault(b['botId'], dict(equity=1., peak=1., dailyCloseMaxDrawdown=0., maxDailyDrawdown=0., sessions=0))
        if b['dailyReturn'] is not None:
            x['equity'] += b['dailyReturn']; x['peak'] = max(x['peak'], x['equity'])
            x['dailyCloseMaxDrawdown'] = max(x['dailyCloseMaxDrawdown'], (x['peak']-x['equity'])/x['peak'])
            x['maxDailyDrawdown'] = max(x['maxDailyDrawdown'], b.get('maxDrawdown') or 0.)
            x['sessions'] += 1
    frozen = bool(info.get('sourceRunId'))
    checks = dict(usedSessionsMatchCommittedWindows=(used == committed if used is not None and not frozen else None),
        excludedNeverSelected=(not invalid_dates.intersection(used) if used is not None else None),
        windowsExactlyFive=all(len(w['dates']) == 5 for w in selected),
        committedDatesUnique=len(committed)==len(set(committed)),
        committedDatesChronological=committed==sorted(committed),
        generationCountMatches=(len(selected) == info.get('completedTrainGenerations') if not frozen else None),
        windowDatesMatchArtifacts=all(read(run/f"window_{w['generation']:04d}.json", {}).get('dates') == w['dates'] for w in selected),
        sourceHashesVerified=info.get('inputHashesVerified'))
    # No success claim when the run was interrupted before final ledgers were written.
    integrity = 'FAIL' if any(v is False for v in checks.values()) else 'PASS' if info.get('phase') == 'completed' and used is not None else 'INCOMPLETE'
    return dict(researchOnly=True, finalOosGate='CLOSED', runId=info.get('runId'), phase=state.get('phase'),
        reportVersion='V07_MANAGED_REPORT_1', engineGitCommit=info.get('gitCommit'), codeHashes=info.get('codeHashes'),
        dataMode=info.get('dataMode'), evaluationStatus=info.get('evaluationStatus'), config=info.get('config'),
        fromDate=info.get('fromDate'), toDate=info.get('toDate'), requestedSessions=info.get('requestedSessions'),
        completedGenerations=info.get('completedTrainGenerations'), usedSessions=used, evaluationSessions=evaluated,
        excludedSessions=exclusions, exclusionReasons=dict(Counter(x['reason'] for x in exclusions)),
        skippedGenerationAttempts=info.get('skippedGenerationAttempts'), interruptedPartialWindows=info.get('interruptedPartialWindows'),
        invalidWindows=[w for w in windows if w['status'] != 'SELECTED'], generations=generations,
        heldGapObservationCount=sum(held_gaps.values()), heldGapObservationsByDate=dict(held_gaps),
        heldGapCountingUnit='bot x missing observation; not distinct outages', invalidBotDays=invalid_bot_days,
        heldGapSymbolAttribution='UNKNOWN_IN_LEGACY_METRICS: aggregate cancellations lack symbol/time; no inference',
        cancellations=dict(cancellations), missingBySymbol=per_symbol, benchmarkIncompleteSessions=dict(benchmark),
        parentSelectionCounts=dict(parent_counts), accountingChecks=checks, accountingIntegrity=integrity,
        validation=read(run/'validation_summary.json'), validationDaily=validation_daily,
        validationMonthly=months, validationRegimes=regime, validationBotRisk=validation_bots,
        validationRiskDefinition='Daily-reset additive PnL equity; daily-close drawdown excludes intraday path; maxDailyDrawdown reported separately.',
        artifactHashes={p.name:digest(p) for p in (run/'run.json', run/'status.json', run/'windows.jsonl', run/'used_sessions.json', run/'excluded_sessions.jsonl') if p.is_file()},
        selectionBiasWarnings=info.get('selectionBiasWarnings', []),
        limitations=['Missing legacy symbol/timestamp cancellation evidence is not fabricated.',
                    'Accounting checks reconcile committed/excluded ledgers; not an independent policy replay.'])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True); p.add_argument('--audit', type=Path)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(argv)
    if a.out.resolve() == a.run.resolve() or a.run.resolve() in a.out.resolve().parents:
        p.error('Output must be outside original run')
    report = analyze(a.run, a.audit)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open('x') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(dict(report=str(a.out), accountingIntegrity=report['accountingIntegrity'],
                          completedGenerations=report['completedGenerations'], reasons=report['exclusionReasons'])))


if __name__ == '__main__':
    main()
