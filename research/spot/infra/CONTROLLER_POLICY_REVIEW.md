# Controller policy example — NOT DEPLOYED

`controller-policy.example.json` is a review-only, per-job IAM identity policy.
Every `REPLACE_*` value must be replaced with an independently reviewed existing
resource. Do not attach this policy to the operating server role without separate
administrator approval: even an IAM update without a direct fee expands security
permissions. No IAM changes were applied by this implementation.

The controller reads exact reviewed infrastructure and a version-pinned bundle;
creates only a tagged instance and encrypted tagged volumes; passes only the
specified worker role; and reads only the explicitly approved job's live status.
It has no S3 writes, bucket creation, role/policy creation, secret-manager access,
broker credentials or existing-host lifecycle permissions. `RunInstances` itself
is a paid capability and must not be exercised until separate explicit consent.
The existing EC2 Spot service-linked role must already be available; this policy
deliberately cannot create it. Any missing EC2 dependent-action requirements are
fail-closed and require operator IAM review, not broad permission additions.

**Important limitations:** IAM tags do not independently prove that an instance
is a Spot instance. The lifecycle controller additionally verifies
`InstanceLifecycle == spot` plus Project/job/OOS/AutoReplace tags before any
termination. No unverified `ec2:InstanceMarketType` condition was invented.
The controller always requests one-time Spot and never requests On-Demand; a
security reviewer must not mistake this identity policy for a complete SCP or an
AWS billing cap. New ENIs are scoped to the account/region because their IDs do
not exist before launch; the same RunInstances authorization also requires the
exact reviewed subnet and SG. No public-IP or internet-routing changes are made.

The independent reaper needs only EC2 describe plus the tagged termination
statement. It must not receive `RunInstances`/PassRole privileges. Run the reaper
on the single reviewed controller host; local flock is not a distributed lock.

Resume requires a NEW job ID, NEW explicit cost approval, updated job-scoped
worker IAM and an exact prior-checkpoint version/hash. It does not automatically
start a replacement. If remote checkpoint discovery is added, grant only the
specific old commit object and version after review; do not grant broad List/Get.

Official references consulted (2026-10-18 UTC):
- https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ExamplePolicies_EC2.html
  (RunInstances resource scoping, InstanceType, MetadataHttpTokens, Encrypted,
  resource/request tags and CreateAction examples).
- https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_passrole.html
- https://docs.aws.amazon.com/AmazonS3/latest/userguide/amazon-s3-policy-keys.html
  (`s3:VersionId` for GetObjectVersion).

This is syntactically validated JSON with offline static assertions, **not** an
AWS IAM simulator/Access Analyzer validation or an actual deployment. Review
against the target account's SCPs, permission boundaries, role trust, AMI and
network requirements before applying. The example intentionally fails before
all placeholders are replaced.
