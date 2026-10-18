"""Authenticated HTTP control state for paid Spot jobs; no provisioning on import.

Offline plan/prepare are local-only. Separate explicit cost consent and instance
creation requests are required; no request may supply arbitrary paths or commands.
"""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from research.spot import v083_spot_lifecycle as lifecycle

PROTOCOL='V083_SPOT_CONTROL_2'
REQUEST_RE=re.compile(r'[a-f0-9]{32}\Z')
ACTIVE={'STARTING','REQUESTED_ONE_TIME_SPOT','CREATE_UNKNOWN','RUNNING','STOP_REQUESTED'}


def _root():return lifecycle.CONTROL_DIR/'web'


def _read(path):
    if path.is_symlink():raise ValueError('SPOT_CONTROL_SYMLINK')
    if not path.is_file():return {}
    if path.stat().st_size>1024*1024:raise ValueError('SPOT_CONTROL_FILE_TOO_LARGE')
    return json.loads(path.read_text())


def _write(path,record):
    from research.backtest_intraday_v4 import atomic_json
    if path.is_symlink():raise ValueError('SPOT_CONTROL_SYMLINK')
    atomic_json(path,record);os.chmod(path,0o600)


@contextmanager
def _lock():
    root=_root()
    if root.is_symlink() or root.parent.is_symlink():raise ValueError('SPOT_CONTROL_SYMLINK')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (root/'web.lock').is_symlink():raise ValueError('SPOT_CONTROL_SYMLINK')
    with (root/'web.lock').open('a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX);yield root


def _load_request(root,ident):
    if not isinstance(ident,str) or not REQUEST_RE.fullmatch(ident):raise ValueError('SPOT_REQUEST_ID_INVALID')
    record=_read(root/(ident+'.json'))
    if not record:raise ValueError('SPOT_REQUEST_NOT_FOUND')
    return record


def _campaign_guard(cfg):
    import v083_web_jobs as jobs
    jobs._storage();jobs._other_active();jobs._verify_source()
    job=jobs._job()
    if job is None or cfg['campaign_job']!=job.name or cfg['population']!=jobs._population(job):
        raise ValueError('V083_SPOT_CAMPAIGN_OR_POPULATION_MISMATCH')
    if any(jobs._state(job,k)['phase'] in ('starting','running') for k in jobs.STAGES):raise ValueError('V083_SPOT_LOCAL_WORKER_BUSY')
    proof=jobs._read(jobs.ROOT/'source_revision.json');evidence=jobs._read(job/'source_plan.json')
    if cfg['source_commit']!=proof.get('commit') or evidence.get('sourceCommit')!=cfg['source_commit']:
        raise ValueError('V083_SPOT_SOURCE_REVISION_MISMATCH')
    jobs._verified_run(job,'smoke')


def _local_blockers(cfg):
    reasons=[]
    if os.getenv('V083_SPOT_LAUNCH_ENABLED')!='YES':reasons.append('SPOT_OPERATOR_ENABLE_SWITCH_REQUIRED')
    if os.getenv('V083_SPOT_REAPER_READY')!='YES':reasons.append('SPOT_INDEPENDENT_TTL_REAPER_REQUIRED')
    if os.getenv('V083_SPOT_REAPER_ENABLED')!='YES':reasons.append('SPOT_REAPER_SWITCH_REQUIRED')
    try:
        if subprocess.run(['/bin/systemctl','is-active','--quiet','v083-spot-reaper.timer'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3).returncode:
            reasons.append('SPOT_REAPER_TIMER_NOT_ACTIVE')
    except (OSError,subprocess.TimeoutExpired):reasons.append('SPOT_REAPER_TIMER_UNVERIFIED')
    review=cfg.get('security_review',{})
    if not review.get('least_privilege_confirmed') or not review.get('no_broker_secrets'):reasons.append('SPOT_SECURITY_REVIEW_REQUIRED')
    return reasons


def plan():
    """No AWS client or billable request; report only private reviewed config."""
    try:
        cfg=lifecycle.load_config(lifecycle.DEFAULT_CONFIG)
        costs=lifecycle.cost_breakdown(cfg);blocked=_local_blockers(cfg)
        return dict(protocol=PROTOCOL,configured=True,runnable=not blocked,blockedReasons=blocked,
            **lifecycle.display_plan(cfg),costBreakdown=costs,configHash=lifecycle.config_digest(cfg),
            finalOosGate='CLOSED',awsPreflight='NOT_EXECUTED_BY_LOCAL_PLAN',allowedTypes=list(lifecycle.ALLOWED_TYPES))
    except (OSError,ValueError) as e:
        return dict(protocol=PROTOCOL,configured=False,runnable=False,blockedReasons=[str(e)],finalOosGate='CLOSED',
                    status='BLOCKED',costBreakdown=None,allowedTypes=list(lifecycle.ALLOWED_TYPES))


def prepare(body):
    allowed={'population','instanceType','maxHours','maxTotalUsd'}
    if not isinstance(body,dict) or set(body)-allowed:raise ValueError('SPOT_UNKNOWN_PLAN_FIELD')
    cfg=lifecycle.load_config(lifecycle.DEFAULT_CONFIG);base_hash=lifecycle.config_digest(cfg)
    if 'population' in body:
        if type(body['population']) is not int or not 2<=body['population']<=50:raise ValueError('SPOT_POPULATION_MUST_BE_2_TO_50')
        # Campaign population is immutable: configure a new campaign before changing it.
        if body['population']!=cfg['population']:raise ValueError('V083_SPOT_NEW_CAMPAIGN_REQUIRED_FOR_POPULATION')
    if 'instanceType' in body:
        if body['instanceType'] not in lifecycle.ALLOWED_TYPES:raise ValueError('SPOT_INSTANCE_NOT_ALLOWLISTED')
        cfg['instance_type']=body['instanceType']
    if 'maxHours' in body:
        value=body['maxHours']
        if type(value) not in (int,float) or not 0<value<=cfg['max_hours']:raise ValueError('SPOT_MAX_HOURS_EXCEEDS_REVIEWED_LIMIT')
        cfg['max_hours']=value
    if 'maxTotalUsd' in body:
        value=body['maxTotalUsd']
        if type(value) not in (int,float) or not 0<value<=cfg['max_total_usd']:raise ValueError('SPOT_BUDGET_EXCEEDS_REVIEWED_LIMIT')
        cfg['max_total_usd']=value
    costs=lifecycle.cost_breakdown(cfg);blockers=_local_blockers(cfg)
    if blockers:raise ValueError(blockers[0])
    _campaign_guard(cfg)
    with _lock() as root:
        latest=_read(root/'latest.json').get('requestId')
        if latest and _load_request(root,latest).get('phase') in ACTIVE:raise ValueError('SPOT_ACTIVE_OR_AMBIGUOUS_JOB')
        ident=uuid.uuid4().hex;record=dict(requestId=ident,phase='PREPARED',createdAt=time.time(),expiresAt=time.time()+600,
            baseConfigHash=base_hash,configHash=lifecycle.config_digest(cfg),config=cfg,costBreakdown=costs)
        _write(root/(ident+'.json'),record);_write(root/'latest.json',{'requestId':ident})
    return _public(record)


def _check_binding(record):
    cfg=lifecycle.load_config(lifecycle.DEFAULT_CONFIG)
    if lifecycle.config_digest(cfg)!=record['baseConfigHash']:raise ValueError('SPOT_REVIEWED_CONFIG_CHANGED')
    if time.time()>record['expiresAt']:raise ValueError('SPOT_COST_CONSENT_EXPIRED')
    if lifecycle.config_digest(record['config'])!=record['configHash']:raise ValueError('SPOT_PREPARED_CONFIG_CHANGED')


def approve(body):
    if not isinstance(body,dict) or set(body)!={'requestId','configHash','acknowledgeCost','confirmation'} or body['acknowledgeCost'] is not True or body['confirmation']!='APPROVE_PAID_SPOT':
        raise ValueError('SPOT_EXPLICIT_COST_CONSENT_REQUIRED')
    with _lock() as root:
        r=_load_request(root,body['requestId']);_check_binding(r)
        if r['phase']!='PREPARED' or body['configHash']!=r['configHash']:raise ValueError('SPOT_APPROVAL_PLAN_MISMATCH')
        r.update(phase='APPROVED',approval=lifecycle.issue_approval(r['config'],expires_at=datetime.now(timezone.utc)+timedelta(minutes=5)))
        _write(root/(r['requestId']+'.json'),r)
    return _public(r)


def start(body):
    if not isinstance(body,dict) or set(body)!={'requestId','confirmation'} or body['confirmation']!='CREATE_ONE_PAID_SPOT':raise ValueError('SPOT_SEPARATE_INSTANCE_CREATION_CONSENT_REQUIRED')
    with _lock() as root:
        r=_load_request(root,body['requestId']);_check_binding(r)
        if r['phase']!='APPROVED':raise ValueError('SPOT_JOB_NOT_APPROVED_OR_ALREADY_USED')
        _campaign_guard(r['config']);r['phase']='STARTING';_write(root/(r['requestId']+'.json'),r)
        try:
            result=lifecycle.launch(r['config'],approval=True,approval_record=r['approval'])
            r.update(phase=result['status'],launch=result)
        except Exception as e:
            # Conservatively assume allocation uncertainty; never auto-retry.
            r.update(phase='CREATE_UNKNOWN',lastError=safe_error(e));_write(root/(r['requestId']+'.json'),r);raise
        _write(root/(r['requestId']+'.json'),r)
    return _public(r)


def _public(r):
    return {k:r[k] for k in ('requestId','phase','createdAt','expiresAt','configHash','costBreakdown','launch','lastError') if k in r}


def _remote_status(cfg,*,s3=None):
    """Small paid S3 reads only after stored cost approval + explicit config opt-in."""
    if cfg.get('monitor_reads_approved') is not True:return dict(available=False,reason='SPOT_S3_MONITOR_REQUEST_COST_NOT_APPROVED')
    s3=lifecycle._api(s3,'s3')
    try:
        response=s3.get_object(Bucket=cfg['bucket'],Key=lifecycle.job_prefix(cfg)+'/output/live_status.json')
        body=response['Body']
        try:raw=body.read(1024*1024+1)
        finally:body.close()
        if len(raw)>1024*1024:raise ValueError('SPOT_REMOTE_STATUS_TOO_LARGE')
        d=json.loads(raw)
        if d.get('job_id')!=cfg['job_id'] or d.get('finalOosGate')!='CLOSED':raise ValueError('SPOT_REMOTE_STATUS_IDENTITY_MISMATCH')
        return dict(available=True,data=d)
    except Exception as e:return dict(available=False,reason=type(e).__name__)


def status():
    info=plan();root=_root();latest=_read(root/'latest.json').get('requestId') if root.is_dir() else None
    if not latest:return dict(**info,request=None,instances=[],remote=None)
    r=_load_request(root,latest);instances=[];remote=dict(available=True,data=r['remoteFinal'],cached=True) if r.get('remoteFinal') else None;error=None
    if r.get('phase') in ACTIVE:
        try:
            observation=lifecycle.status(r['config']);instances=observation.get('instances',[])
            remote=_remote_status(r['config'])
            data=(remote or {}).get('data',{})
            # A durable final commit is required. Neither a missing instance nor
            # a stale live heartbeat alone establishes success or safe retry.
            if data.get('phase') in ('completed','failed') and data.get('resultCommit') and not instances and r['phase']!='CREATE_UNKNOWN':
                with _lock() as locked:
                    current=_load_request(locked,r['requestId'])
                    if current['phase']==r['phase']:
                        current['phase']=data['phase'].upper();current['finishedAt']=time.time();current['remoteFinal']=data
                        _write(locked/(r['requestId']+'.json'),current);r=current
            elif (observation.get('lastInstance') or {}).get('state')=='terminated' and not instances and r['phase'] in ACTIVE and r['phase']!='CREATE_UNKNOWN':
                with _lock() as locked:
                    current=_load_request(locked,r['requestId'])
                    if current['phase']==r['phase']:
                        current.update(phase='INTERRUPTED',lastError='SPOT_TERMINATED_WITHOUT_DURABLE_FINAL_RESULT')
                        _write(locked/(r['requestId']+'.json'),current);r=current
        except Exception as e:error=type(e).__name__
    return dict(**info,request=_public(r),instances=instances,remote=remote,statusError=error)


def stop(body):
    if not isinstance(body,dict) or set(body)!={'requestId','confirmation'} or body['confirmation']!='STOP_OWN_SPOT':raise ValueError('SPOT_STOP_CONFIRMATION_REQUIRED')
    with _lock() as root:
        r=_load_request(root,body['requestId'])
        if r['phase'] not in ACTIVE:raise ValueError('SPOT_NO_ACTIVE_OWNED_JOB')
        result=lifecycle.terminate(r['config'],approval=True);r.update(phase='STOP_REQUESTED',stop=result)
        _write(root/(r['requestId']+'.json'),r)
    return dict(**_public(r),terminated=result.get('terminated',[]))


def safe_error(error):
    value=str(error)
    return value if re.fullmatch(r'[A-Z][A-Z0-9_]{0,159}',value) else 'SPOT_CONTROL_'+type(error).__name__.upper()


def dispatch(action,body=None):
    if action=='plan':return plan()
    if action=='status':return status()
    if action in ('prepare','approve','start','stop'):return globals()[action](body)
    raise ValueError('SPOT_ACTION_NOT_ALLOWLISTED')
