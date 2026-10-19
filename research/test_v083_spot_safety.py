"""Strictly offline lifecycle fault injection: no AWS requests or resources."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from research.spot import v083_spot_lifecycle as spot
from research.test_v083_spot_lifecycle import sample, managed
REAL_PREFLIGHT = spot.preflight_readonly


class SpotSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg = sample(); self.ec2 = MagicMock(); self.s3 = MagicMock(); self.iam = MagicMock()
        self.ec2.describe_instances.return_value = {"Reservations": []}
        self.ec2.run_instances.return_value = {"Instances": [{"InstanceId": "i-0123456789abcdef0"}]}
        self.addCleanup(patch.stopall)
        patch.dict("os.environ", {"V083_SPOT_LAUNCH_ENABLED":"YES", "V083_SPOT_REAPER_READY":"YES"}).start()
        patch.object(spot.subprocess, "run", return_value=type("R", (), {"returncode":0})()).start()
        self.preflight = patch.object(spot, "preflight_readonly", return_value={"root_device":"/dev/sda1"}).start()

    def launch(self, cfg=None, **kwargs):
        cfg = cfg or self.cfg
        return spot.launch(cfg, ec2=self.ec2, s3=self.s3, iam=self.iam, approval=True,
                           approval_record=spot.issue_approval(cfg), control_dir=self.tmp.name, **kwargs)

    def test_approval_pins_all_config_and_expires(self):
        approval = spot.issue_approval(self.cfg)
        for key, value in (("population",50), ("bundle_sha256","f"*64), ("max_total_usd",30), ("instance_type","m7i.2xlarge")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,"PER_JOB_COST"):
                spot.verify_approval({**self.cfg,key:value},approval)
        approval["expires_at"] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError,"EXPIRED"): spot.verify_approval(self.cfg,approval)
        self.ec2.run_instances.assert_not_called()

    def test_cost_breakdown_includes_noncompute_and_is_not_invoice_cap(self):
        self.assertFalse(spot.cost_breakdown(self.cfg)["hard_invoice_cap"])
        with self.assertRaisesRegex(ValueError,"OVER_BUDGET"):
            spot.issue_approval({**self.cfg,"max_total_usd":1.9})
        with self.assertRaisesRegex(ValueError,"ADDITIONAL_COST"):
            spot.issue_approval({**self.cfg,"cost_estimate":{}})

    def test_global_paginated_other_job_blocks(self):
        self.ec2.describe_instances.side_effect = [
            {"Reservations":[],"NextToken":"page2"},
            {"Reservations":[{"Instances":[managed("i-other","v083-ffffffffffffffff")]}]}]
        with self.assertRaisesRegex(ValueError,"DUPLICATE"): self.launch()
        self.ec2.run_instances.assert_not_called()
        self.assertNotIn("tag:V083ResearchJob", [f["Name"] for f in self.ec2.describe_instances.call_args.kwargs["Filters"]])

    def test_concurrent_different_jobs_are_serialized_and_global_limit_one(self):
        def created(**kwargs):
            self.ec2.describe_instances.return_value={"Reservations":[{"Instances":[managed("i-created",self.cfg["job_id"])]}]}
            return {"Instances":[{"InstanceId":"i-created"}]}
        self.ec2.run_instances.side_effect=created
        results=[]
        def run(cfg):
            try: results.append(self.launch(cfg)["status"])
            except ValueError as error: results.append(str(error))
        other={**self.cfg,"job_id":"v083-ffffffffffffffff"}
        threads=[threading.Thread(target=run,args=(c,)) for c in (self.cfg,other)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(self.ec2.run_instances.call_count,1)
        self.assertEqual(results.count("REQUESTED_ONE_TIME_SPOT"),1)

    def test_ambiguous_create_blocks_retry_and_different_job(self):
        self.ec2.run_instances.side_effect=TimeoutError("unknown result")
        with self.assertRaises(TimeoutError): self.launch()
        for cfg in (self.cfg,{**self.cfg,"job_id":"v083-ffffffffffffffff"}):
            with self.assertRaisesRegex(ValueError,"UNRESOLVED_CREATE"): self.launch(cfg)
        self.assertEqual(self.ec2.run_instances.call_count,1)
        self.assertEqual(json.loads((Path(self.tmp.name)/"launch-registry.json").read_text())["phase"],"CREATE_UNKNOWN")

    def test_stop_and_reaper_never_terminate_untagged_or_ondemand_host(self):
        valid=managed("i-managed",self.cfg["job_id"],deadline="2020-01-01T00:00:00+00:00")
        ondemand={**valid,"InstanceId":"i-host","InstanceLifecycle":"on-demand"}
        untagged={**valid,"InstanceId":"i-other","Tags":[]}
        self.ec2.describe_instances.return_value={"Reservations":[{"Instances":[valid,ondemand,untagged]}]}
        self.assertEqual(spot.terminate(self.cfg,ec2=self.ec2,approval=True)["terminated"],["i-managed"])
        with patch.dict("os.environ",{"V083_SPOT_REAPER_ENABLED":"YES"}):
            self.assertEqual(spot.reap(self.cfg,ec2=self.ec2)["expired_terminated"],["i-managed"])

    def test_reaper_cli_survives_missing_or_disabled_launch_config(self):
        # Cleanup must not depend on the latest mutable job's launch switch.
        with patch.object(spot,'load_config',side_effect=AssertionError('reaper must be independent')),patch.object(spot,'reap',return_value={'expired_terminated':[]}) as cleanup:
            spot.main(['reap','--config','/nonexistent/disabled-launch.json'])
        cleanup.assert_called_once_with(None)

    def test_userdata_deadline_exists_before_bundle_fetch_and_worker_paths_match(self):
        data=spot.make_user_data(self.cfg)
        self.assertLess(data.index("shutdown -h +480"),data.index("aws s3api get-object"))
        self.assertLess(data.index("trap 'finish $?' EXIT"),data.index("shutdown -h +480"))
        self.assertNotIn("aws s3 sync",data)
        self.assertIn("bundle/source/research/spot/v083_spot_worker.py",data)
        self.assertIn("--max-seconds 28500",data)
        self.assertIn("--bucket v083-research-bucket",data)
        self.assertNotIn("personal:order",data)

    def test_security_preflight_failure_cannot_launch(self):
        self.preflight.side_effect=ValueError("SPOT_IAM_POLICY_CHANGED")
        with self.assertRaisesRegex(ValueError,"IAM_POLICY"): self.launch()
        self.ec2.run_instances.assert_not_called()
        self.assertFalse((Path(self.tmp.name)/"launch-registry.json").exists())

    def test_real_preflight_rejects_missing_review_no_client_reads(self):
        with self.assertRaisesRegex(ValueError,"SECURITY_REVIEW"):
            REAL_PREFLIGHT(self.ec2,self.s3,self.iam,self.cfg)
        self.ec2.describe_images.assert_not_called()


    def configured_preflight(self):
        cfg=dict(self.cfg)
        policy={"Version":"2012-10-17","Statement":[
            {"Effect":"Allow","Action":["s3:GetObjectVersion"],"Resource":[f"arn:aws:s3:::{cfg['bucket']}/{cfg['bundle_key']}"]},
            {"Effect":"Allow","Action":["s3:PutObject","s3:GetObjectVersion"],"Resource":[f"arn:aws:s3:::{cfg['bucket']}/{spot.job_prefix(cfg)}/*"]}]}
        cfg["security_review"]={"reviewed_profile_arn":cfg["instance_profile_arn"],"no_broker_secrets":True,
            "least_privilege_confirmed":True,"iam_policy_sha256":hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(",",":")).encode()).hexdigest()}
        self.ec2.describe_security_groups.return_value={"SecurityGroups":[{"VpcId":"vpc1","IpPermissions":[]}]}
        self.ec2.describe_images.return_value={"Images":[{"ImageId":cfg["ami_id"],"State":"available","Architecture":"x86_64","RootDeviceType":"ebs","RootDeviceName":"/dev/sda1"}]}
        self.ec2.describe_subnets.return_value={"Subnets":[{"VpcId":"vpc1"}]}
        self.iam.get_instance_profile.return_value={"InstanceProfile":{"Arn":cfg["instance_profile_arn"],"Roles":[{"RoleName":"worker"}]}}
        self.iam.list_attached_role_policies.return_value={"AttachedPolicies":[]}
        self.iam.list_role_policies.return_value={"PolicyNames":["restricted"]}
        self.iam.get_role_policy.return_value={"PolicyDocument":policy}
        self.s3.get_bucket_versioning.return_value={"Status":"Enabled"}
        self.s3.get_public_access_block.return_value={"PublicAccessBlockConfiguration":dict.fromkeys(("BlockPublicAcls","IgnorePublicAcls","BlockPublicPolicy","RestrictPublicBuckets"),True)}
        self.s3.head_object.return_value={"ContentLength":100,"Metadata":{"sha256":cfg["bundle_sha256"],"source-commit":cfg["source_commit"]}}
        return cfg,policy

    def test_real_preflight_valid_restricted_infrastructure(self):
        cfg,policy=self.configured_preflight()
        self.assertEqual(REAL_PREFLIGHT(self.ec2,self.s3,self.iam,cfg)["root_device"],"/dev/sda1")
        self.ec2.run_instances.assert_not_called()
        original=policy['Statement'][1]['Action']
        policy['Statement'][1]['Action']=['s3:PutObject']
        cfg['security_review']['iam_policy_sha256']=hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with self.assertRaisesRegex(ValueError,'REQUIRED_PERMISSION_MISSING'):
            REAL_PREFLIGHT(self.ec2,self.s3,self.iam,cfg)
        policy['Statement'][1]['Action']=original
        cfg['security_review']['iam_policy_sha256']=hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.s3.head_object.return_value['ContentLength']=24*1024**3+1
        with self.assertRaisesRegex(ValueError,'BUNDLE_HASH_OR_SIZE'):
            REAL_PREFLIGHT(self.ec2,self.s3,self.iam,cfg)

    def test_real_preflight_rejects_inbound_and_wildcard_policy_even_if_hash_reviewed(self):
        cfg,policy=self.configured_preflight()
        self.ec2.describe_security_groups.return_value["SecurityGroups"][0]["IpPermissions"]=[{"IpProtocol":"-1"}]
        with self.assertRaisesRegex(ValueError,"NO_INGRESS"): REAL_PREFLIGHT(self.ec2,self.s3,self.iam,cfg)
        self.ec2.describe_security_groups.return_value["SecurityGroups"][0]["IpPermissions"]=[]
        policy["Statement"][0]["Action"]=["*"]
        cfg["security_review"]["iam_policy_sha256"]=hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        with self.assertRaisesRegex(ValueError,"ACTION_SCOPE_UNSAFE"): REAL_PREFLIGHT(self.ec2,self.s3,self.iam,cfg)

    def test_direct_launch_revalidates_mutable_config_before_clients(self):
        cfg={**self.cfg,"bundle_key":"v083/bundles/a.tar.gz;curl"}
        with self.assertRaisesRegex(ValueError,"BUNDLE_KEY_INVALID"):
            self.launch(cfg)
        self.ec2.run_instances.assert_not_called()

    def resume_config(self):
        old="v083/runs/v083-ffffffffffffffff"
        return {**self.cfg,"resume_prefix":old,"resume_reference":{
            "key":old+"/commits/generation-0001.json","versionId":"immutable01","sha256":"d"*64}}

    def test_resume_userdata_pins_reference_and_never_uses_same_job(self):
        cfg=self.resume_config();spot.validate_config(cfg)
        userdata=spot.make_user_data(cfg)
        cfg['bundle_version_id']='3/L4kqtJlcpXroDTDmJ+rmSpXd3dIbrHY='
        cfg['resume_reference']['versionId']=cfg['bundle_version_id']
        spot.validate_config(cfg);userdata=spot.make_user_data(cfg)
        self.assertIn('--version-id='+cfg['bundle_version_id'],userdata)
        self.assertIn('--resume-reference "$JOB/resume-reference.json"',userdata)
        self.assertIn('--resume-prefix '+cfg['resume_prefix'],userdata)
        self.assertIn(json.dumps(cfg['resume_reference'],sort_keys=True),userdata)
        record=spot.issue_approval(cfg)
        with self.assertRaisesRegex(ValueError,'PER_JOB_COST'):
            spot.verify_approval({**cfg,'resume_reference':{**cfg['resume_reference'],'versionId':'changed'}},record)
        for bad in ({'resume_prefix':spot.job_prefix(cfg)},
                    {'resume_reference':{**cfg['resume_reference'],'key':'v083/runs/other/commits/a.json'}},
                    {'resume_reference':{**cfg['resume_reference'],'versionId':"';curl"}}):
            with self.assertRaisesRegex(ValueError,'RESUME'):
                spot.validate_config({**cfg,**bad})

    def test_resume_iam_only_exact_old_prefix_version_read_permitted(self):
        cfg,policy=self.configured_preflight();cfg.update({k:v for k,v in self.resume_config().items() if k.startswith('resume_')})
        statement={'Effect':'Allow','Action':['s3:GetObjectVersion'],
            'Resource':[f"arn:aws:s3:::{cfg['bucket']}/{cfg['resume_prefix']}/*"]}
        policy['Statement'].append(statement)
        def digest():
            cfg['security_review']['iam_policy_sha256']=hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        digest();self.assertEqual(REAL_PREFLIGHT(self.ec2,self.s3,self.iam,cfg)['security'],'VERIFIED_READ_ONLY')
        for bad in (['s3:PutObject'],['s3:GetObject'],['s3:GetObjectVersion','s3:PutObject']):
            statement['Action']=bad;digest()
            with self.assertRaisesRegex(ValueError,'RESUME_READ_VERSION_ONLY'):
                REAL_PREFLIGHT(self.ec2,self.s3,self.iam,cfg)
        statement['Action']=['s3:GetObjectVersion'];statement['Resource']=[f"arn:aws:s3:::{cfg['bucket']}/v083/runs/v083-eeeeeeeeeeeeeeee/*"];digest()
        with self.assertRaisesRegex(ValueError,'RESOURCE_SCOPE_UNSAFE'):
            REAL_PREFLIGHT(self.ec2,self.s3,self.iam,cfg)

    def test_status_never_resets_unknown_creation_registry(self):
        self.ec2.run_instances.side_effect=TimeoutError('unknown')
        with self.assertRaises(TimeoutError):self.launch()
        result=spot.status(self.cfg,ec2=self.ec2,control_dir=self.tmp.name)
        self.assertTrue(result['reconciliationRequired'])
        self.assertEqual(result['launchRegistry']['phase'],'CREATE_UNKNOWN')
        self.assertEqual(self.ec2.run_instances.call_count,1)
