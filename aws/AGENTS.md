# Probe Watch: instructions for `aws/`

This is the synthetic AWS demo for the beginner talk "Build it. Attack it. Catch it.
A live card-testing lab on AWS" (date TBC). The talk is 20 minutes plus a demo.
The root `AGENTS.md` also applies here. Paths below are relative to `aws/`.

Use [README.md](README.md) for the architecture, scope and sandbox constraints, and
[probe-watch-runbook.md](probe-watch-runbook.md) for the console walkthrough and
first-run notes. Keep those explanations there rather than copying them into this file.

## Hard rules

- Synthetic data only. No real card numbers, customer records or payment-provider calls.
  The checkout accepts card fingerprints and must reject anything resembling a card number.
- The simulator may only call this stack's own API. CloudFormation supplies `API_URL`;
  never take a target URL from invocation input or point the simulator at another checkout.
  Preserve its refusal to run against non-API-Gateway hosts.
- No employer references in code, docs or slides. Generic AWS only.
- The demo runs entirely in the AWS console: CloudFormation upload, Lambda Test tab,
  CloudWatch dashboard. Do not add shell scripts or CLI steps to the demo. Local
  maintenance checks below are separate from the demo.
- Target a 4-hour training sandbox in us-east-1 (us-west-2 also allowed), with a
  restricted role and no billing access. Keep one uploadable CloudFormation template.
- Use only CloudFormation, API Gateway, Lambda, WAF, CloudWatch, SNS and IAM resources.
  CloudShell is allowed but unused. Do not add a VPC, VPC endpoints, OpenSearch, Cloud9,
  CodeCommit or Amazon Fraud Detector.
- Use two Lambda functions (sandbox maximum: ten, 2048 MB each). Keep their code inline,
  Python 3.12, standard library only, and within the lab's 4,096-character budget per handler.
- Abuse monitoring is on. Keep traffic bounded; the simulator caps an invocation at 600
  requests. Do not introduce an unbounded traffic loop or raise the cap.
- Keep log groups and other provisioned demo resources owned by the stack for teardown.
  Do not promise deletion of service-retained metrics or resources outside the stack.

## Code and detection contracts

- `probe-watch.yaml` is the source for both handlers. Tests extract and execute that code;
  do not add separate handler copies just for testing.
- `checkout` returns HTTP 200 for approved and declined attempts, with the business
  outcome in its response and one JSON log event per valid attempt. Invalid input must
  not become a business event.
- Default scenarios are `normal` (60 shoppers), `loud` (120 attempts from one device),
  and `spread` (360 attempts over 80 devices). Preserve deterministic card outcomes and
  repeatable seeded traffic. Device headers and event fingerprints must agree.
- Keep the Python/Splunk reference rules intact: 20 attempts, 15 distinct cards, small
  payments at most 100 USD cents, fixed UTC-aligned 300-second buckets. Any rule change
  must update its template expressions, dashboard, docs and meaningful checks together.
- The current AWS volume alarm and WAF rate limit do not implement the full reference
  detectors. Preserve the README's limitations until the implementation is corrected;
  do not treat a passing unit suite as evidence of AWS/Python/Splunk parity.
- Saved queries and outputs use `Mappings.Queries`; the dashboard embeds JSON-escaped
  copies. Retain a drift check while those copies exist.
- Keep tests focused on observable handler behavior, scenario shape, safety limits and
  dashboard validity. Avoid copied predicates or simulated WAF timing as proof of detection.
- Use plain language. Comments explain why. Never commit secrets, state files or real data.
  Keep diagrams simple and commit messages short.

## Verification

From the repository root:

```bash
python3 -m unittest discover -s aws/tests
cfn-lint aws/probe-watch.yaml
```

Both must pass after template or test changes. Root notebook synchronization and Splunk
parity requirements still apply if their source files change.

The stack has not yet been deployed to a real AWS account. First-run acceptance needs
an actual console deployment: CREATE_COMPLETE (target under five minutes), valid dashboard
widgets and saved queries, normal traffic below the volume alarm threshold, loud and
spread counts with the expected device distribution, an alarm notification after SNS
confirmation, observed WAF blocks in BLOCK mode, and successful stack teardown. Record
actual timings and histograms in the runbook; WAF does not guarantee a block on request 21.

If editing the talk slides, match API Gateway + WAF to Lambda, with CloudWatch Logs
Insights for inspection.
