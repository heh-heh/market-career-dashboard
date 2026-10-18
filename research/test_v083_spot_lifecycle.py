"""Offline tests: no AWS clients, credentials, instances or charges."""
import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from research.spot import v083_spot_lifecycle as spot
from research.spot import v083_spot_worker as worker


def sample():
    return {
        "enabled": True,
        "region": "ap-northeast-1",
        "instance_type": "c6i.2xlarge",
        "ami_id": "ami-0123456789abcdef0",
        "subnet_id": "subnet-0123456789abcdef0",
        "security_group_id": "sg-0123456789abcdef0",
        "instance_profile_arn": "arn:aws:iam::123456789012:instance-profile/V083SpotTrainOnly",
        "bucket": "v083-research-bucket",
        "bundle_key": "v083/bundles/verified-research.tar.gz",
        "bundle_sha256": "a"*64,
        "bundle_version_id": "reviewedversion01",
        "source_commit": "b"*40,
        "job_id": "v083-0123456789abcdef",
        "campaign_job": "job-20261018T120000Z-0123456789ab",
        "population": 10,
        "max_hours": 8,
        "max_spot_price_usd_hour": 0.20,
        "max_compute_usd": 1.6,
        "final_oos_gate": "CLOSED",
        "max_total_usd":2, "cost_estimate":dict(ebs_usd=.12,s3_usd=.08,network_usd=.1,ipv4_usd=.04,other_usd=.06),
    }


def managed(identifier, job, state="running", deadline="2026-10-20T00:00:00+00:00"):
    return {"InstanceId":identifier,"InstanceLifecycle":"spot","State":{"Name":state},
        "Tags":[{"Key":"Project","Value":spot.PROJECT},{"Key":"V083ResearchJob","Value":job},
                {"Key":"TTL_UTC","Value":deadline},{"Key":"OOS","Value":"CLOSED"},
                {"Key":"AutoReplace","Value":"DISABLED"}]}


class SpotLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name)/"config.json"
        self.config=sample()

    def load(self,config=None):
        self.file.write_text(json.dumps(config if config is not None else self.config))
        return spot.load_config(self.file)

    def test_no_billing_or_client_from_offline_plan(self):
        cfg=self.load()
        with patch.object(spot,"_api",side_effect=AssertionError("AWS never called")):
            plan=spot.display_plan(cfg)
        self.assertEqual(plan["spot_behavior"],"one-time / terminate")
        self.assertFalse(plan["automatic_replacement"])
        self.assertEqual(plan["maximum_compute_spend_at_bid"],1.6)
        self.assertEqual(plan["hourly_total_planning_target_usd"],0.25)

    def test_fail_closed_when_config_missing_or_disabled(self):
        with self.assertRaisesRegex(ValueError,"SPOT_CONFIG_REQUIRED"):
            spot.load_config(self.file)
        self.config["enabled"]=False
        with self.assertRaisesRegex(ValueError,"SPOT_DISABLED"):
            self.load()

    def test_invalid_limits_2026_or_unapproved_policies(self):
        bad=[("region","us-west-2"),("instance_type","p5.48xlarge"),
             ("max_hours",24),("max_spot_price_usd_hour",7),
             ("max_compute_usd",0),("population",51),
             ("final_oos_gate","OPEN"),("bundle_key","v083/../bad"),
             ("bundle_version_id",""),("source_commit","not_sha"),
             ("ami_id","fake"),("max_hours",False)]
        for field,value in bad:
            with self.subTest(field=field,value=value):
                temp={**sample(),field:value}
                with self.assertRaises(ValueError):
                    self.load(temp)

    def test_no_launch_without_both_approval_and_explicit_enable(self):
        cfg=self.load()
        with patch.object(spot,"_api",side_effect=AssertionError("never invoked")):
            with self.assertRaisesRegex(ValueError,"SPOT_EXPLICIT"):
                spot.launch(cfg,approval=False)
            with self.assertRaisesRegex(ValueError,"SPOT_OPERATOR_ENABLE"):
                spot.launch(cfg,approval=True)
        with patch.dict("os.environ",{"V083_SPOT_LAUNCH_ENABLED":"YES"}),patch.object(spot,"_api",side_effect=AssertionError("not authorized")):
            with self.assertRaisesRegex(ValueError,"SPOT_INDEPENDENT_TTL_REAPER_REQUIRED"):
                spot.launch(cfg,approval=True)

    def test_launch_provisions_exactly_one_terminate_only(self):
        cfg=self.load()
        ec2=MagicMock()
        s3=MagicMock()
        ec2.describe_instances.return_value={"Reservations":[]}
        ec2.run_instances.return_value={"Instances":[{"InstanceId":"i-0123456789abcdef0"}]}
        s3.head_object.return_value={"Metadata":{"sha256":"a"*64,"source-commit":"b"*40},
                                     "ContentLength":12000}
        with patch.dict("os.environ",{"V083_SPOT_LAUNCH_ENABLED":"YES","V083_SPOT_REAPER_READY":"YES"}), \
             patch.object(spot.subprocess,"run",return_value=type("R",(),{"returncode":0})()), patch.object(spot,"preflight_readonly",side_effect=lambda ec2,s3,iam,cfg: (spot.verify_bundle(s3,cfg) or {"root_device":"/dev/xvda"})):
            result=spot.launch(cfg,ec2=ec2,s3=s3,iam=MagicMock(),approval=True,approval_record=spot.issue_approval(cfg),control_dir=Path(self.temp.name)/"control")
        self.assertEqual(result["status"],"REQUESTED_ONE_TIME_SPOT")
        params=ec2.run_instances.call_args.kwargs
        self.assertEqual(params["MinCount"],params["MaxCount"])
        self.assertEqual(params["MinCount"],1)
        self.assertEqual(params["InstanceMarketOptions"]["MarketType"],"spot")
        self.assertEqual(params["InstanceMarketOptions"]["SpotOptions"]["SpotInstanceType"],"one-time")
        self.assertEqual(params["InstanceMarketOptions"]["SpotOptions"]["InstanceInterruptionBehavior"],"terminate")
        self.assertEqual(params["InstanceInitiatedShutdownBehavior"],"terminate")
        self.assertTrue(params["MetadataOptions"]["HttpTokens"]=="required")
        self.assertTrue(params["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"])
        self.assertTrue(params["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
        self.assertIn("timeout --signal=TERM",params["UserData"])
        self.assertIn("shutdown -h now",params["UserData"])
        self.assertNotIn("live_trader",params["UserData"])
        self.assertEqual(params["ClientToken"],cfg["job_id"])
        self.assertFalse(result["automatic_relaunch"])

    def test_duplicate_and_bad_archive_fail_before_launch(self):
        cfg=self.load()
        ec2=MagicMock()
        s3=MagicMock()
        ec2.describe_instances.return_value={"Reservations":[{"Instances":[{"InstanceId":"i-x",
                                         "State":{"Name":"running"}}]}]}
        with patch.dict("os.environ",{"V083_SPOT_LAUNCH_ENABLED":"YES","V083_SPOT_REAPER_READY":"YES"}), \
             patch.object(spot.subprocess,"run",return_value=type("R",(),{"returncode":0})()), patch.object(spot,"preflight_readonly",side_effect=lambda ec2,s3,iam,cfg: (spot.verify_bundle(s3,cfg) or {"root_device":"/dev/xvda"})):
            with self.assertRaisesRegex(ValueError,"DUPLICATE"):
                spot.launch(cfg,ec2=ec2,s3=s3,iam=MagicMock(),approval=True,approval_record=spot.issue_approval(cfg),control_dir=Path(self.temp.name)/"control")
            ec2.describe_instances.return_value={"Reservations":[]}
            s3.head_object.return_value={"Metadata":{"sha256":"wrong"},"ContentLength":3000}
            with self.assertRaisesRegex(ValueError,"HASH_OR_SIZE"):
                spot.launch(cfg,ec2=ec2,s3=s3,iam=MagicMock(),approval=True,approval_record=spot.issue_approval(cfg),control_dir=Path(self.temp.name)/"control")
        ec2.run_instances.assert_not_called()

    def test_stop_only_matching_job_and_explicit_user_approval(self):
        cfg=self.load()
        ec2=MagicMock()
        ec2.describe_instances.return_value={"Reservations":[{"Instances":[
            managed("i-one",cfg["job_id"]),
            managed("i-two",cfg["job_id"],state="stopping")]}]}
        with self.assertRaisesRegex(ValueError,"APPROVAL_REQUIRED"):
            spot.terminate(cfg,ec2=ec2)
        result=spot.terminate(cfg,ec2=ec2,approval=True)
        self.assertEqual(result["terminated"],["i-one","i-two"])
        ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-one","i-two"])
        keys=ec2.describe_instances.call_args.kwargs["Filters"]
        self.assertTrue(any(f["Name"]=="tag:V083ResearchJob" and f["Values"]==[cfg["job_id"]] for f in keys))

    def test_independent_ttl_reaper_terminates_only_expired_matching_tags(self):
        cfg=self.load()
        ec2=MagicMock()
        ec2.describe_instances.return_value={"Reservations":[{"Instances":[
            managed("i-old",cfg["job_id"],deadline="2026-10-18T00:00:00+00:00"),
            managed("i-new",cfg["job_id"],deadline="2026-10-20T00:00:00+00:00"),
            managed("i-other","v083-ffffffffffffffff",deadline="2026-10-18T00:00:00+00:00")
        ]}]}
        with self.assertRaisesRegex(ValueError,"SPOT_REAPER_SWITCH_REQUIRED"):
            spot.reap(cfg,ec2=ec2)
        from datetime import datetime,timezone
        with patch.dict("os.environ",{"V083_SPOT_REAPER_ENABLED":"YES"}):
            d=spot.reap(cfg,ec2=ec2,current_time=datetime(2026,10,19,tzinfo=timezone.utc))
        self.assertEqual(d["expired_terminated"],["i-old","i-other"])
        ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-old","i-other"])

    def test_s3_bundle_source_is_pinned_and_has_safe_extraction(self):
        cfg=self.load()
        user_data=spot.make_user_data(cfg)
        self.assertIn("--version-id=reviewedversion01",user_data)
        self.assertIn("sha256sum -c -",user_data)
        self.assertIn("SPOT_UNSAFE_ARCHIVE",user_data)
        self.assertEqual(spot.ALLOWED_TYPES["c6i.2xlarge"],8)
        self.assertNotIn("run_instances",user_data)


class WorkerTests(unittest.TestCase):
    def test_worker_fails_without_verified_bundle_before_launching_any_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(worker,"execute",side_effect=AssertionError("no research run")):
                with self.assertRaisesRegex(ValueError,"PINNED_BUNDLE_LAYOUT"):
                    worker.run(workspace=tmp,job_id="v083-0123456789abcdef",
                               population=10,source_commit="b"*40)

    def test_worker_cannot_read_final_oos_or_invalid_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,"JOB_OR_POPULATION_INVALID"):
                worker.run(workspace=tmp,job_id="bad-id",population=1,source_commit="b"*40)


if __name__=="__main__":
    unittest.main()
