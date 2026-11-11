# helio-demo-infra

Infrastructure for `demo-api` on ECS Fargate, requested from the Helio catalog.

- `helio/service.json` is the request. `main.tf` is what Helio rendered from it under the account's policy.
- `.github/workflows/infra.yml` runs on every push to `main`: **plan**, **apply** (waits for approval of the `production` environment), **verify**.
- Helio runs Terraform. The workflow asks Helio and waits; it holds no cloud credentials.
- The plan job publishes `helio-plan-summary.json`: resource addresses, types and actions, the plan digest and the policy result. Names only, no values.
- Apply refuses any plan other than the one that was reviewed.
