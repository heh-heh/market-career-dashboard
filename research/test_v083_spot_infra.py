"""Offline validation of Spot-only CloudFormation drafts; never calls AWS."""
from pathlib import Path
import json
import unittest


INFRA=Path(__file__).resolve().parent/"spot"/"infra"


class V083SpotInfrastructureSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage=json.loads((INFRA/"storage.json").read_text(encoding="utf-8"))
        cls.worker=json.loads((INFRA/"worker.json").read_text(encoding="utf-8"))

    def test_storage_is_review_only_and_creates_no_compute_or_paid_network(self):
        self.assertIn("REVIEW ONLY",self.storage["Description"])
        resources=self.storage["Resources"]
        self.assertEqual({node["Type"] for node in resources.values()},
                         {"AWS::S3::Bucket","AWS::S3::BucketPolicy"})
        self.assertNotIn("AWS::EC2::Instance",str(resources))
        self.assertNotIn("AWS::EC2::NatGateway",str(resources))

    def test_bucket_is_private_encrypted_versioned_and_retained(self):
        bucket=self.storage["Resources"]["ResearchBucket"]
        props=bucket["Properties"]
        self.assertEqual(bucket["DeletionPolicy"],"Retain")
        self.assertEqual(bucket["UpdateReplacePolicy"],"Retain")
        self.assertEqual(props["VersioningConfiguration"],{"Status":"Enabled"})
        self.assertEqual(props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
                         ["ServerSideEncryptionByDefault"]["SSEAlgorithm"],"AES256")
        self.assertEqual(props["PublicAccessBlockConfiguration"],
                         {k:True for k in ("BlockPublicAcls","IgnorePublicAcls",
                                           "BlockPublicPolicy","RestrictPublicBuckets")})
        self.assertEqual(props["OwnershipControls"]["Rules"],[{"ObjectOwnership":"BucketOwnerEnforced"}])
        self.assertNotIn("LifecycleConfiguration",props,"retention must not silently delete recovery evidence")
        self.assertNotIn("ReplicationConfiguration",props,"no unapproved cross-region copy")
        self.assertNotIn("LoggingConfiguration",props,"no surprise archive/log copy")

    def test_no_plain_http_for_any_object_or_bucket(self):
        policy=self.storage["Resources"]["TLSOnlyBucketPolicy"]["Properties"]["PolicyDocument"]
        statements=policy["Statement"]
        self.assertEqual(len(statements),1)
        deny=statements[0]
        self.assertEqual(deny["Effect"],"Deny")
        self.assertEqual(deny["Principal"],"*")
        self.assertEqual(deny["Action"],"s3:*")
        self.assertEqual(deny["Condition"],{"Bool":{"aws:SecureTransport":"false"}})
        resources=deny["Resource"]
        self.assertEqual(len(resources),2)
        self.assertTrue(any("${ResearchBucketName}/*" in item["Fn::Sub"] for item in resources))
        self.assertTrue(any(item["Fn::Sub"].endswith("${ResearchBucketName}") for item in resources))

    def test_worker_template_reuses_external_bucket_without_recreation(self):
        self.assertIn("Bucket",self.worker["Parameters"])
        resource_types={value["Type"] for value in self.worker["Resources"].values()}
        self.assertNotIn("AWS::S3::Bucket",resource_types)
        self.assertNotIn("AWS::EC2::Instance",resource_types)
        self.assertNotIn("AWS::EC2::SpotFleet",resource_types)
        self.assertIn("AWS::IAM::Role",resource_types)
        self.assertIn("AWS::EC2::SecurityGroup",resource_types)
        sg=self.worker["Resources"]["WorkerSecurityGroup"]["Properties"]
        self.assertEqual(sg["SecurityGroupIngress"],[])
        self.assertEqual(sg["SecurityGroupEgress"],[{
            "IpProtocol":"tcp","FromPort":443,"ToPort":443,"CidrIp":"0.0.0.0/0"}])

    def test_bucket_name_remains_required_unselected_and_not_deployed(self):
        param=self.storage["Parameters"]["ResearchBucketName"]
        self.assertNotIn("Default",param)
        self.assertEqual(self.storage["Resources"]["ResearchBucket"]["Properties"]["BucketName"],
                         {"Ref":"ResearchBucketName"})
        self.assertEqual(self.storage["Outputs"]["ResearchBucketName"]["Value"],
                         {"Ref":"ResearchBucket"})


if __name__=="__main__":
    unittest.main()
