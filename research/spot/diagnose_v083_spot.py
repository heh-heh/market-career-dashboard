"""Read-only EC2 evidence report. S3 request costs require an explicit opt-in.

Never prints User Data, raw console output, credentials or raw data. No launch,
stop, IAM mutation or object writes. Use on the operator's authenticated host.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
from research.spot import v083_spot_lifecycle as lifecycle


def diagnose(cfg,instance_id,*,ec2=None,s3=None,read_s3=False):
    if not re.fullmatch(r'i-[a-f0-9]{8,17}',instance_id):raise ValueError('SPOT_INSTANCE_ID_INVALID')
    ec2=lifecycle._api(ec2)
    response=ec2.describe_instances(InstanceIds=[instance_id])
    found=[i for r in response.get('Reservations',[]) for i in r.get('Instances',[])]
    if len(found)!=1 or not lifecycle.managed_spot(found[0],cfg['job_id']):raise ValueError('SPOT_INSTANCE_IDENTITY_UNVERIFIED')
    instance=found[0]
    raw=b'';console='';unavailable={}
    try:
        raw=base64.b64decode(ec2.describe_instance_attribute(InstanceId=instance_id,Attribute='userData').get('UserData',{}).get('Value',''),validate=True)
    except Exception as error:unavailable['userData']=type(error).__name__
    try:
        console=ec2.get_console_output(InstanceId=instance_id).get('Output','')
        console=base64.b64decode(console,validate=True).decode('utf-8',errors='replace')
    except Exception as error:unavailable['console']=type(error).__name__;console=''
    # Explicitly no raw excerpts; even exception messages/commands can contain secrets.
    codes=[code for code,pattern in {
        'PACKAGE_NOT_AVAILABLE':r'Unable to find a match|No match for argument',
        'PYTHON_MODULE_MISSING':r'ModuleNotFoundError',
        'TAR_FILTER_UNSUPPORTED':r"unexpected keyword argument ['\"]filter",
        'ACCESS_DENIED':r'AccessDenied',
        'S3_VERSION_MISSING':r'NoSuchVersion',
        'PIP_NO_MATCHING_DISTRIBUTION':r'No matching distribution found',
        'SHA256_MISMATCH':r'checksum did NOT match|FAILED.*checksum',
    }.items() if re.search(pattern,console)]
    result=dict(researchOnly=True,finalOosGate='CLOSED',jobId=cfg['job_id'],instanceId=instance_id,
        instanceState=instance['State']['Name'],clientTokenMatches=instance.get('ClientToken')==cfg['job_id'],
        stateReasonCode=instance.get('StateReason',{}).get('Code'),
        unavailableEvidence=unavailable,userDataSha256=hashlib.sha256(raw).hexdigest() if raw else None,consoleBytes=len(console.encode()),consoleErrorCodes=codes,
        failureCause='EVIDENCE_INSUFFICIENT' if not codes else 'CONSOLE_PATTERN_REQUIRES_STAGE_CORRELATION',
        rawConsoleExported=False,s3Diagnostics=None)
    if read_s3:
        s3=lifecycle._api(s3,'s3');records={}
        for suffix in ('exit-code.txt','bootstrap/status.json','output/live_status.json'):
            try:
                response=s3.get_object(Bucket=cfg['bucket'],Key=lifecycle.job_prefix(cfg)+'/'+suffix)
                body=response['Body']
                try:value=body.read(1024*1024+1)
                finally:body.close()
                if len(value)>1024*1024:raise ValueError('SPOT_DIAGNOSTIC_TOO_LARGE')
                if suffix=='exit-code.txt':
                    records[suffix]=int(value.strip()) if re.fullmatch(rb'[0-9]{1,3}',value.strip()) else 'INVALID_EXIT_CODE'
                else:
                    record=json.loads(value)
                    if record.get('job_id')!=cfg['job_id'] or record.get('finalOosGate')!='CLOSED':raise ValueError('SPOT_DIAGNOSTIC_IDENTITY_MISMATCH')
                    records[suffix]={k:record[k] for k in ('phase','stage','exitCode','errorCode','errorType','startedAt','observedAt') if k in record}
            except Exception as error:
                records[suffix]={'errorType':type(error).__name__}
        result['s3Diagnostics']=records
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=lifecycle.DEFAULT_CONFIG)
    p.add_argument('--instance-id',required=True)
    p.add_argument('--read-s3-diagnostics',action='store_true',help='Explicit consent to possibly billable S3 GET requests')
    p.add_argument('--out',type=Path)
    a=p.parse_args(argv)
    try:
        if a.config.is_symlink():raise ValueError('SPOT_CONFIG_SYMLINK')
        cfg=json.loads(a.config.read_text())
        if not lifecycle.JOB_RE.fullmatch(str(cfg.get('job_id',''))) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{2,62}',str(cfg.get('bucket',''))):raise ValueError('SPOT_DIAGNOSTIC_CONFIG_INVALID')
        report=diagnose(cfg,a.instance_id,read_s3=a.read_s3_diagnostics)
        data=json.dumps(report,indent=2)
        if a.out:
            with a.out.open('x') as file:file.write(data+'\n')
        print(data)
    except Exception as error:
        print(json.dumps({'phase':'BLOCKED','reason':type(error).__name__,'researchOnly':True}));return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
