"""Static review-only policy tests; never calls AWS or changes IAM."""
import json
from pathlib import Path
import unittest

class ControllerPolicyTests(unittest.TestCase):
    def test_review_policy_has_no_storage_or_role_mutation(self):
        root=Path(__file__).resolve().parents[1]
        policy=json.loads((root/'research/spot/infra/controller-policy.example.json').read_text())
        statements={s['Sid']:s for s in policy['Statement']}
        actions=[]
        for statement in policy['Statement']:
            value=statement['Action'];actions.extend(value if isinstance(value,list) else [value])
        self.assertFalse(any(a in actions for a in ('iam:CreateRole','iam:PutRolePolicy','s3:PutObject','ec2:StartInstances','ec2:StopInstances','ec2:RequestSpotInstances')))
        self.assertEqual(statements['CreateOnlyTaggedApprovedInstance']['Condition']['StringEquals']['ec2:InstanceType'],'c6i.2xlarge')
        self.assertEqual(statements['CreateOnlyEncryptedTaggedRootVolume']['Condition']['Bool']['ec2:Encrypted'],'true')
        self.assertEqual(statements['TagOnlyDuringApprovedRunInstances']['Condition']['StringEquals']['ec2:CreateAction'],'RunInstances')
        self.assertNotIn('ec2:InstanceMarketType',json.dumps(policy))
        stop=statements['TerminateResearchOnlyNeverExistingHost']['Condition']['StringEquals']
        self.assertEqual(stop['aws:ResourceTag/Project'],'V083-Research-Only')
        self.assertEqual(statements['ReadOnlyExplicitlyApprovedJobStatus']['Action'],'s3:GetObject')
        self.assertTrue(statements['ReadOnlyExplicitlyApprovedJobStatus']['Resource'].endswith('/output/live_status.json'))
        self.assertIn('s3:VersionId',statements['ReadOnlyPinnedResearchBundleVersion']['Condition']['StringEquals'])
