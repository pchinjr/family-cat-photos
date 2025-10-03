# 0002. GitHub Actions CI/CD Pipeline

- Status: accepted
- Date: 2025-10-03

## Context
The project requires automated verification before merge and reliable deployments to AWS. We want a minimal-dependency approach that aligns with the serverless stack, exercises SAM templates, and prepares for guarded production releases without introducing external tooling to manage.

## Decision
- Use GitHub Actions for CI/CD because the repository is hosted on GitHub and Actions provides managed runners with native OIDC integration for AWS.
- Run unit tests, `sam validate`, and `sam build` on every pull request and push to `main` to keep quality gates fast and deterministic.
- Deploy changes from `main` using `sam deploy` with environment configuration supplied via GitHub secrets/variables. The workflow supports either OIDC role assumption or static IAM user credentials.
- Store optional allow-list configuration (`AllowedFamilyIds`) in GitHub Secrets so we keep sensitive family identifiers out of version control.

## Consequences
- **Positive**: Minimal setup, no new self-hosted infrastructure, and test coverage enforced on each change. AWS credentials remain in GitHub Secrets or use OIDC, reducing long-lived credentials.
- **Negative**: Deployments depend on correctly configured GitHub secrets/variables. We must monitor workflow logs to troubleshoot failed deployments and ensure IAM roles allow SAM actions. Future environments may require environment-specific workflows or additional guardrails (e.g., manual approvals).

## Links
- `.github/workflows/ci-cd.yml`
- `README.md` CI/CD section
