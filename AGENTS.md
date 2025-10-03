# Family Cat Photos Project Plan

## Mission
Build a secure, serverless cat photo sharing site exclusively for private family members. Deliver a minimal-dependency Python backend deployed with AWS SAM.

## Guiding Principles
- Favor AWS managed services (API Gateway, Lambda, DynamoDB, S3) to minimize ops overhead.
- Keep Python backend lightweight; rely on standard library whenever possible.
- Maintain small, frequent commits with clear messages.
- All features must be backed by verified automated tests before merge.
- Document architectural decisions via ADRs; ensure high-level design stays current.

## High-Level Roadmap
1. **Foundations**: Scaffold AWS SAM application, configure Python Lambda runtime, set up CI with unit test enforcement.
2. **Auth & Access Control**: Implement private family member authentication (e.g., Cognito with invite-only signup) and fine-grained access rules.
3. **Photo Storage & Sharing**: Design S3 bucket structure, signed URL flows, and metadata storage in DynamoDB.
4. **User Experience**: Expose RESTful endpoints for uploading, browsing, and sharing cat photos; enable audit logging.
5. **Observability & Ops**: Add logging, metrics, and alarms; document runbooks and recovery checklists.

## Collaboration Norms
- Prioritize automated tests and run them locally before every push.
- Keep dependencies minimal; justify any addition in an ADR.
- Update documentation and ADRs in the same commit as impactful changes.
- Review pull requests with focus on security, privacy, and reliability.

## Open Questions
- Which authentication mechanism best balances security and ease for family members?
- Do we need offline access or mobile-optimized flows initially?
- How should photo retention policies and deletion windows be handled?

