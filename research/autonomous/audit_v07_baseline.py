#!/usr/bin/env python3
"""Inspect existing run metadata only. Never repair/rewrite or train a baseline."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research.autonomous.diagnostics import BaselineCandidateError, identify_baseline
from research.autonomous.experiment_registry import read_json
from research.evolution.completeness import completeness_facts
from research_export import sanitize


def audit(root):
    runs=[]; stages=[]
    for path in sorted(Path(root).rglob('run.json')):
        if path.is_symlink():
            raise ValueError('Symlink metadata refused')
        info=read_json(path)
        # Dates are metadata only: never open raw data or evaluate Final OOS.
        if str(info.get('toDate',''))[:4]>='2026':
            continue
        if info.get('phase')=='completed':
            runs.append((path.parent,info))
        if info.get('evaluationMode')=='FROZEN_VALIDATION' and str(info.get('fromDate','')).startswith('2024'):
            summary_path=path.parent/'validation_summary.json'
            summary=read_json(summary_path) if summary_path.exists() else {}
            stages.append(dict(runId=info.get('runId'),stage='internal_holdout',**completeness_facts(info,summary)))
    candidates=sorted(((p,i) for p,i in runs if i.get('selectionMode')=='V07_INTERNAL_HOLDOUT_SHORTLIST'
                       and i.get('evaluationMode')=='FROZEN_VALIDATION'
                       and str(i.get('fromDate','')).startswith('2025')),
                      key=lambda x:(x[1].get('finishedAt',''),x[1]['runId']),reverse=True)
    rejected=[]; selected=None
    def reference(run_id, declared_path=None):
        matches=[(p,i) for p,i in runs if i.get('runId')==run_id]
        if len(matches)>1 and declared_path:
            matches=[(p,i) for p,i in matches if str(p.absolute())==declared_path]
        if len(matches)!=1:
            raise BaselineCandidateError('BASELINE_LINEAGE_INVALID',stage='lineage',run_id=run_id,
                                         detail='missing or ambiguous cataloged reference')
        return matches[0]
    for development,info in candidates:
        try:
            shortlist,short=reference(info.get('sourceRunId'),info.get('sourceRunDir'))
            holdout,hold=reference(short.get('holdoutRunId'))
            training,train=reference(hold.get('sourceRunId'),hold.get('sourceRunDir'))
            baseline=identify_baseline([(development,info),(shortlist,short),(holdout,hold),(training,train)])
            selected=baseline['info']['runId']
            break
        except BaselineCandidateError as exc:
            rejected.append(exc.as_dict())
        except (KeyError,ValueError,OSError,TypeError):
            rejected.append(dict(runId=info.get('runId'),stage='metadata',code='BASELINE_ARTIFACT_INVALID'))
    selection=dict(policy='NEWEST_STRICTLY_VALID_V07_LINEAGE',candidateCount=len(candidates),
                   selectedRunId=selected,rejected=rejected,criteriaRelaxed=False,
                   code='BASELINE_VALID' if selected else 'NO_VALID_V07_BASELINE')
    return dict(researchOnly=True,criteriaRelaxed=False,finalOosGate='CLOSED',
                selection=selection,internalHoldouts=stages,
                note='Metadata only; REAL missing dates require read-only raw minute audit before targeted backfill. Existing artifacts unchanged.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results-dir',type=Path,default=Path('/home/ubuntu/evolution-results'))
    args=p.parse_args()
    print(json.dumps(sanitize(audit(args.results_dir)),ensure_ascii=False,allow_nan=False,indent=2))


if __name__=='__main__':
    main()
