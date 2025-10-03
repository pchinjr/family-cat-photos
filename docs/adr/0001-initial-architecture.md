# 0001. Initial Serverless Architecture

- Status: accepted
- Date: 2024-05-19

## Context
The family needs a secure, low-maintenance way to privately share cat photos. The solution should minimize operational overhead, rely on AWS managed services, and support small incremental delivery while accommodating future enhancements like richer metadata and notifications.

## Decision
- Implement the backend using AWS SAM with a single Python 3.11 Lambda function fronted by an HTTP API.
- Store uploaded photos in an S3 bucket configured with encryption, versioning, and blocked public access.
- Persist photo metadata in a DynamoDB table keyed by family identifier and photo id.
- Generate presigned upload URLs from Lambda to keep uploads private and time-bound.
- Enforce a configurable allow-list of family identifiers via an `x-family-id` request header until dedicated authentication is added.

## Consequences
- **Positive**: Minimal infrastructure footprint, few dependencies to manage, near-zero server administration, and straightforward CI/CD with SAM. Presigned URLs avoid exposing the bucket while keeping client uploads simple.
- **Negative**: Relying on a custom header is a stopgap; we must replace it with stronger auth (e.g., Cognito) before production. The single Lambda function may need to be split as complexity grows. DynamoDB’s access patterns must be monitored to avoid hot partitions if a family uploads many photos quickly.

## Links
- `template.yaml`
- Future ADR TBD: authentication strategy
