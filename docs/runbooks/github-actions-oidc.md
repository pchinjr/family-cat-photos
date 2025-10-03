# GitHub Actions OIDC Deployment Guide

This runbook walks through configuring GitHub Actions to assume an AWS IAM role via OpenID Connect (OIDC). Using OIDC avoids long-lived credentials and aligns with the project’s security goals.

## Prerequisites
- AWS account access with permission to manage IAM identity providers and roles.
- GitHub repository admin access (`pchinjr/family-cat-photos`).
- The SAM deployment workflow from `.github/workflows/ci-cd.yml`.

## 1. Configure AWS OIDC Provider (one-time per account)
1. Sign in to the AWS Console and open **IAM → Identity providers**.
2. Choose **Add provider** → **OpenID Connect**.
3. Set:
   - **Provider URL**: `https://token.actions.githubusercontent.com`
   - **Audience**: `sts.amazonaws.com`
4. Save. If the provider already exists, reuse it and note its ARN.

### CLI equivalent
```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```
*The thumbprint matches GitHub’s published certificate fingerprint; update if GitHub rotates certificates.*

## 2. Create IAM Role for Deployments
1. In IAM, choose **Roles → Create role**.
2. Select **Web identity**, choose the newly created provider, and set **Audience** to `sts.amazonaws.com`.
3. For the trust policy, use the JSON below (replace `<ACCOUNT_ID>` with your account ID):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:pchinjr/family-cat-photos:ref:refs/heads/main"
        }
      }
    }
  ]
}
```
   - Restrict `sub` to `ref:refs/heads/main` so only the `main` branch deploys. Add entries (comma-separated array) for other branches or tags if needed.
4. Attach a permissions policy that allows SAM deployments. Avoid wildcard permissions—scope actions and resources to this stack.
   - **Recommended**: create a dedicated artifacts bucket once (pattern: `family-cat-photos-artifacts-<account-id>`). Example CLI (run in your AWS environment; not executed here):
     ```bash
     ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
     SAM_ARTIFACT_BUCKET="family-cat-photos-artifacts-${ACCOUNT_ID}"
     aws s3 mb "s3://${SAM_ARTIFACT_BUCKET}"
     ```
     Alternatively, create the bucket from the S3 console with the same naming scheme. Remember bucket names are globally unique; adjust if the name is taken.
     > **Project note**: For account `837132623653`, the shared bucket is `family-cat-photos-artifacts-837132623653` in `us-east-1`, and `samconfig.toml` already references it.
   - Update GitHub repository variables: set `SAM_ARTIFACT_BUCKET` to the exact bucket name only if you need to override the default.
   - If you prefer SAM to create and manage its own bucket (`--resolve-s3`), broaden the S3 resource ARNs below to match the managed bucket naming pattern (`aws-sam-cli-managed-default-samclisourcebucket-*`).
   - A starter inline policy (replace placeholders) is:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudFormation",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateChangeSet",
        "cloudformation:CreateStack",
        "cloudformation:DeleteChangeSet",
        "cloudformation:DeleteStack",
        "cloudformation:DescribeChangeSet",
        "cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStacks",
        "cloudformation:ExecuteChangeSet",
        "cloudformation:GetTemplateSummary",
        "cloudformation:ListStackResources",
        "cloudformation:TagResource",
        "cloudformation:UntagResource",
        "cloudformation:UpdateStack"
      ],
      "Resource": [
        "arn:aws:cloudformation:us-east-1:<ACCOUNT_ID>:stack/family-cat-photos*",
        "arn:aws:cloudformation:us-east-1:aws:transform/Serverless-2016-10-31"
      ]
    },
    {
      "Sid": "ArtifactsBucket",
      "Effect": "Allow",
      "Action": [
        "s3:AbortMultipartUpload",
        "s3:CreateBucket",
        "s3:DeleteObject",
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::family-cat-photos-artifacts-<ACCOUNT_ID>",
        "arn:aws:s3:::family-cat-photos-artifacts-<ACCOUNT_ID>/*"
      ]
    },
    {
      "Sid": "PhotoBucket",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:PutBucketAcl",
        "s3:PutEncryptionConfiguration",
        "s3:PutBucketOwnershipControls",
        "s3:PutBucketPolicy",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutBucketTagging",
        "s3:PutBucketVersioning"
      ],
      "Resource": "arn:aws:s3:::family-cat-photos-photobucket-*"
    },
    {
      "Sid": "DynamoDb",
      "Effect": "Allow",
      "Action": [
        "dynamodb:CreateTable",
        "dynamodb:DeleteTable",
        "dynamodb:DescribeTable",
        "dynamodb:DescribeContinuousBackups",
        "dynamodb:ListTagsOfResource",
        "dynamodb:TagResource",
        "dynamodb:UntagResource",
        "dynamodb:UpdateContinuousBackups",
        "dynamodb:UpdateTable"
      ],
      "Resource": "arn:aws:dynamodb:us-east-1:<ACCOUNT_ID>:table/family-cat-photos-PhotoMetadataTable*"
    },
    {
      "Sid": "LambdaFunctions",
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction",
        "lambda:AddPermission",
        "lambda:DeleteFunction",
        "lambda:GetFunction",
        "lambda:GetPolicy",
        "lambda:RemovePermission",
        "lambda:TagResource",
        "lambda:UntagResource",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration"
      ],
      "Resource": "arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:family-cat-photos-PhotoApiFunction*"
    },
    {
      "Sid": "HttpApi",
      "Effect": "Allow",
      "Action": [
        "apigateway:DELETE",
        "apigateway:GET",
        "apigateway:PATCH",
        "apigateway:POST",
        "apigateway:PUT",
        "apigateway:TagResource"
      ],
      "Resource": [
        "arn:aws:apigateway:us-east-1::/apis",
        "arn:aws:apigateway:us-east-1::/apis/*",
        "arn:aws:apigateway:us-east-1::/apis/*/routes/*",
        "arn:aws:apigateway:us-east-1::/apis/*/deployments/*",
        "arn:aws:apigateway:us-east-1::/apis/*/stages",
        "arn:aws:apigateway:us-east-1::/apis/*/stages/*",
        "arn:aws:apigateway:us-east-1::/tags/*"
      ]
    },
    {
      "Sid": "PassExecutionRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/family-cat-photos-*"
    },
    {
      "Sid": "ManageLambdaExecutionRole",
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:DeleteRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PutRolePolicy",
        "iam:TagRole",
        "iam:UntagRole",
        "iam:UpdateRole"
      ],
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/family-cat-photos-PhotoApiFunctionRole-*"
    },
    {
      "Sid": "DescribeIAMRoles",
      "Effect": "Allow",
      "Action": "iam:GetRole",
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/family-cat-photos-*"
    }
  ]
}
```
   - Replace `<ACCOUNT_ID>` with your environment value. If you deploy in a region other than `us-east-1`, adjust the ARNs accordingly. The extra CloudFormation ARN (`aws:transform/Serverless-2016-10-31`) is required because SAM expands templates using that transform; without it you'll see `Template format error` or `not authorized to perform cloudformation:CreateChangeSet` failures. The DynamoDB ARN covers the generated table name (`family-cat-photos-PhotoMetadataTable-…`) so `DescribeTable` and related calls succeed during deploys. The photo bucket statement grants CloudFormation authority to create/update the stack-managed S3 bucket (`family-cat-photos-PhotoBucket-*`).
   - Add statements for additional resources (e.g., S3 object access policies, DynamoDB stream consumers, Parameter Store reads) as the stack grows; prefer narrow ARNs over `*`.
   - If the bucket is provisioned manually, you can remove `s3:CreateBucket` from the policy once the bucket exists.
   - If you iterate quickly, you can temporarily attach a broader policy, but plan to tighten it before production.
5. Finish creation and note the role ARN (e.g., `arn:aws:iam::123456789012:role/family-cat-photos-deploy`).

### CLI example
```bash
TRUST_POLICY_FILE=trust-policy.json
PERMISSIONS_FILE=deploy-policy.json

aws iam create-role \
  --role-name family-cat-photos-deploy \
  --assume-role-policy-document file://$TRUST_POLICY_FILE

aws iam put-role-policy \
  --role-name family-cat-photos-deploy \
  --policy-name sam-deploy \
  --policy-document file://$PERMISSIONS_FILE
```

 ## 3. Configure GitHub Secrets and Variables
 1. In the GitHub repository, open **Settings → Secrets and variables → Actions**.
 2. Under **Secrets**, add `AWS_DEPLOY_ROLE_ARN` with the role ARN from AWS.
 3. Under **Variables**, add any optional overrides:
    - `AWS_REGION` (defaults to `us-east-1`).
    - `SAM_STAGE_NAME` (defaults to `dev`).
 4. Optionally add `ALLOWED_FAMILY_IDS` as a secret to pass the family allow-list at deploy time.
5. **Composite action limitation**: GitHub composite actions cannot read `secrets.*` directly. The workflow exports secrets into environment variables (`AWS_DEPLOY_ROLE_ARN`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) before invoking any composite steps. If you introduce additional composites, pass required secrets via `env` or `with` inputs from the calling workflow.

## 4. Verify Workflow Usage
The deploy job in `.github/workflows/ci-cd.yml` automatically picks up `AWS_DEPLOY_ROLE_ARN` and configures credentials via the OIDC provider. On the first push to `main`:
1. Visit **Actions → CI/CD** workflow run.
2. Confirm the `Configure AWS credentials (assume role)` step succeeds.
3. Review the `Deploy stack` output for the `sam deploy` command and resulting stack changes.

## 5. Troubleshooting
- **AccessDenied**: Check the role’s trust policy `sub` condition matches the branch/ref triggering the workflow.
- **No matching credentials**: Ensure `AWS_DEPLOY_ROLE_ARN` secret is set and not masked by environment protections.
- **Missing permissions**: Expand the role’s permissions policy to cover any new AWS resources SAM needs to create or update.
- **Thumbprint errors**: Update the OIDC provider thumbprint to match GitHub’s current certificate.

Document any refinements in a future ADR if the deployment strategy changes (e.g., per-environment roles or manual approvals).
