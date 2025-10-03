# Family Cat Photos

Serverless backend for a private family photo sharing site focused on cat pictures. The MVP provides authenticated family members with presigned upload URLs, records photo metadata, and lists uploaded photos. It is deployed with AWS SAM using managed services to keep the footprint small.

## Features
- REST-style API fronted by API Gateway HTTP API.
- Python AWS Lambda function that issues presigned S3 upload URLs, stores metadata in DynamoDB, and lists photos for a family.
- S3 bucket with strict public access blocking for object storage.
- DynamoDB table for photo metadata (family + photo composite key).
- Configurable allow-list of family identifiers enforced per request.
- Minimal runtime dependencies (standard library + AWS SDK).

## Getting Started
1. **Bootstrap infrastructure**
   ```bash
   sam build
   sam deploy --guided
   ```
   Provide values for `StageName` and comma-delimited `AllowedFamilyIds`. Leaving `AllowedFamilyIds` empty allows any `x-family-id` header while you iterate locally.

2. **Invoke locally**
   ```bash
   sam local start-api
   ```
   Send requests with an `x-family-id` header matching your allow list.

3. **Run tests**
   ```bash
   pytest
   ```
   (If your platform lacks the necessary packages, create a virtual environment and install from `requirements-dev.txt`.)

## API Overview
| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Lightweight readiness check. |
| `POST` | `/photos/upload-url` | Returns a presigned S3 URL and metadata stub for uploading a new cat photo. |
| `POST` | `/photos` | Records photo metadata after a successful upload. Idempotent per `photoId`. |
| `GET` | `/photos` | Lists photos for the requesting family identifier. |

Requests must include `x-family-id`. If `ALLOWED_FAMILY_IDS` is set, the header must match one of the configured values.

## Project Layout
```
.
├── AGENTS.md
├── Makefile
├── README.md
├── requirements-dev.txt
├── samconfig.toml
├── src
│   └── handlers
│       ├── __init__.py
│       ├── photos.py
│       └── requirements.txt
├── template.yaml
└── tests
    ├── __init__.py
    └── test_photos_handler.py
```

## Development Workflow
- Aim for small, focused commits with clear messages.
- Every change should have corresponding automated tests; add or update tests before committing.
- Update relevant ADRs or create new ones with architectural-impacting decisions.
- Document API or deployment changes in the README to keep family members supported.

## CI/CD Pipeline
- GitHub Actions workflow (`.github/workflows/ci-cd.yml`) runs on pull requests and pushes to `main`.
- Jobs execute `pytest`, `sam validate`, and `sam build` to keep the template healthy.
- Deployments from `main` call `sam deploy` with parameters from repository/environment variables.
- Configure repository **Secrets** for one of:
  - `AWS_DEPLOY_ROLE_ARN` (preferred OIDC role assumption), or
  - `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` for an IAM user with SAM permissions.
- SAM deploys use dedicated artifacts bucket `family-cat-photos-artifacts-837132623653` (pre-provisioned in `us-east-1` via `samconfig.toml`). Set repository variable `SAM_ARTIFACT_BUCKET` only if you need to override this default.
- Optional Secrets/Variables:
  - `ALLOWED_FAMILY_IDS` (comma-separated allow list passed to `AllowedFamilyIds`).
  - Repository variable `SAM_STAGE_NAME` to override the default `dev` stage name.
  - Repository variable `AWS_REGION` if deploying outside `us-east-1`.
- Trigger deploys manually with the **Run workflow** button (`workflow_dispatch`) when needed.
- See `docs/runbooks/github-actions-oidc.md` for setting up GitHub OIDC role assumption on AWS.

## Observability & Future Work
- Add CloudWatch dashboards and alarms for upload rate anomalies and DynamoDB throttles.
- Integrate Amazon Cognito for authenticated family login flows.
- Attach EventBridge notifications for new photo uploads (e.g., email digests).
- Extend metadata to include tagging, comments, and soft-delete policies.
