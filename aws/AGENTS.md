# Probe Watch, a card-testing detection lab on AWS

Context for Claude Code. Read this before touching anything under `aws/`. All paths
below are relative to `aws/`.

## What this is

A live demo for a beginner talk, "Build it. Attack it. Catch it. A live card-testing lab
on AWS", at an AWS student community day (date TBC). 20-minute talk plus demo.

Card testing: bots use a real checkout to find out which stolen cards still work, with
tiny payments or "save this card" attempts. The lab shows that a fraud model scoring one
payment at a time misses it, that watching declines alone misses it (most attempts get
approved), and that counting per device and across the whole checkout catches it.

Companion to the Python/Splunk lab in the root of this repository (synthetic, 1,972
events). Its verified numbers, which the talk quotes: model caught 84% of
classic fraud and 0 of 320 card-testing payments; per-device rule caught the loud attack
(120 attempts from one device in 20 min) and missed the spread one (360 attempts across 80
devices, never more than 2 per device in 5 min); checkout-wide rule caught 52 of 360; a
decline-gated rule caught nothing because 90% of attempts were approved.

## Hard rules

- Synthetic data only. No real card numbers anywhere. The API accepts card *fingerprints*
  and rejects anything that looks like a card number.
- The simulator may only call the stack's own API URL. It takes the URL from its
  environment, never from its input, and refuses non-API-Gateway hosts. Never point it
  anywhere else. Never test against a real checkout.
- No employer references in code, docs or slides. Generic AWS only.
- Runs in a 4-hour training sandbox. Design for it:
  - us-east-1 (us-west-2 also allowed). Restricted role, no billing access.
  - Everything is wiped after the session. One CloudFormation file, console upload,
    under 5 min.
  - Lambda: max 10 functions, 2048 MB. We use 2.
  - Allowed and used: CloudFormation, API Gateway, Lambda, WAF, CloudWatch, SNS, IAM.
    CloudShell is allowed but not used; the demo runs from the console.
  - Not supported: PrivateLink (so no VPC endpoints, so no VPC at all), OpenSearch
    Serverless, Cloud9, CodeCommit, Amazon Fraud Detector.
  - Abuse monitoring is on. Keep traffic to hundreds of requests, not thousands. The
    simulator caps a run at 600.
- Inline Lambda code must stay under 4,096 characters (CloudFormation ZipFile limit),
  python3.12, stdlib only. `tests/test_handlers.py` asserts the limit.

## Architecture (as built in `probe-watch.yaml`)

- API Gateway REST (REGIONAL), stage `demo`, `POST /pay` and `POST /save-card` to Lambda
  `<stack>-checkout`.
- `checkout`: fake payment provider. Outcome = hash of `card_fp`; cards starting `ct_`
  approve 35%, others 96%. Prints one JSON business event per attempt to log group
  `/probe-watch/<stack>/checkout-events` (Lambda `LoggingConfig.LogGroup`, text format so
  each line is raw JSON). Returns HTTP 200 for approved *and* declined, on purpose.
- Event schema: `ts, flow (pay|save_card), outcome (approved|declined), decline_code,
  device_fp, card_fp, bin, amount_minor, currency, request_id, source_ip`.
- WAFv2 REGIONAL web ACL on the stage. One rate-based rule keyed on header `x-device-fp`
  (custom keys), limit `RateLimitPerDevice` (default 20) per 300 s. Action COUNT or BLOCK
  from parameter `WafMode`.
- Lambda `<stack>-simulate` (env `API_URL`), event
  `{"scenario": "normal"|"loud"|"spread", "n": int, "seed": int, "pace": float}`:
  normal = 60 shoppers, one attempt each; loud = 120 attempts, 1 device, fresh card each
  time; spread = 360 attempts over 80 devices (round robin, 4 or 5 each) with 0.2 s
  sleep. Returns a status-code histogram plus an outcome histogram.
- Metric filter `{ ($.flow = "save_card") || ($.amount_minor <= 100) }` to
  `ProbeWatch/ProbeShapedAttempts`. Alarm: Sum > 20 over 300 s, missing data not
  breaching, to an SNS topic with an email subscription (`AlertEmail`, must be confirmed).
- Both log groups are stack resources, so deleting the stack leaves nothing behind.
- Console pieces, all CloudWatch: the two queries saved as `AWS::Logs::QueryDefinition`
  (`<stack>/per-device`, `<stack>/checkout-wide`) and one `AWS::CloudWatch::Dashboard`
  named after the stack: both query tables, the alarm widget, and a graph of
  probe-shaped attempts plus the WAF rule's counted and blocked requests.
- Outputs: `ApiUrl`, `EventsLogGroup`, `SimulatorFunction`, `WafMode`, `AlarmName`,
  `PerDeviceQuery`, `CheckoutWideQuery`, and console links `DashboardUrl`,
  `SimulatorTestUrl`, `AlarmUrl`, `WebAclUrl`.

Logs Insights queries used in the demo (kept once in `Mappings`, used by the saved
queries and the outputs; the dashboard repeats them with JSON escaping and a test checks
the copies match):

```
filter ispresent(flow)
| stats count(*) as attempts, count_distinct(card_fp) as cards by device_fp, bin(5m)
| sort attempts desc | limit 20
```
```
filter ispresent(flow) and (flow = "save_card" or amount_minor <= 100)
| stats count(*) as candidates, count_distinct(card_fp) as cards by bin(5m)
| sort candidates desc | limit 20
```

## Files

- `probe-watch.yaml`: the stack. cfn-lint clean. Both handlers unit-tested with the HTTP
  call stubbed (`python3 -m unittest discover -s tests`). **Not yet deployed to a real
  AWS account.** Expect first-run fixes; the runbook lists the likely spots.
- `probe-watch-runbook.md`: console-only deploy steps, demo script, teardown, why the
  design fits the sandbox, first-run notes.
- `tests/test_handlers.py`: stdlib unit tests; they extract the inline code from the
  template so there is one copy of it, and check the dashboard body is valid JSON that
  repeats the saved queries.
- Slides live in a Claude artifact (14 slides). Slides 6 and 11 match this lab (API
  Gateway + WAF to Lambda; Logs Insights).

## Demo script (AWS console only, no shell)

Phase 1, `WafMode=COUNT`: upload the template in the CloudFormation console, confirm the
SNS email, open the Outputs links. In the Lambda console Test tab run `{"scenario":
"normal"}`, then `"loud"`, then `"spread"` and read the histogram in Execution result.
On the dashboard show the per-device table (loud caught, spread not), the checkout-wide
table (both), the alarm widget, and the alarm email. Point: every request got HTTP 200.
The outcome fields showed the attack, not the status code.

Phase 2, Update stack in the console with `WafMode=BLOCK`: rerun `loud`, expect 403s after
about 20 requests (run it twice if WAF has not caught up). Rerun `spread`, still all 200.
Point: the edge rate limit is worth having and does not cover the spread-out attack. Layers.

The user runs the demo from the AWS console: CloudFormation upload, Lambda Test tab,
CloudWatch dashboard. Do not add shell scripts or CLI steps to the docs.

## Backlog, in order

1. First real deploy in a sandbox. Fix any runtime errors (likely spots: WAF association
   ARN, Lambda `LoggingConfig`, API Gateway deployment ordering, dashboard body). Record
   the outputs and the real histograms in the runbook.
2. Optional: Terraform port under `terraform/`, same resources, same constraints.

Do not add resources outside the allowed list above. Do not add a VPC.

## Acceptance checks

- `cfn-lint probe-watch.yaml` is clean.
- `python3 -m unittest discover -s tests` passes.
- Stack reaches CREATE_COMPLETE in under 5 minutes in us-east-1 with `CAPABILITY_IAM`.
- `normal`: alarm stays OK.
- `loud`: per-device query shows `ct_loud_dev_1` with 20+ attempts and 15+ cards in one
  window; alarm goes to ALARM within about 5 minutes; email arrives.
- `spread`: no device crosses the per-device threshold; checkout-wide query shows windows
  over 20.
- The dashboard renders all four widgets and the saved queries appear in Logs Insights.
- `WafMode=BLOCK`: `loud` returns 403 after about 20 requests; `spread` stays all 200.
- Deleting the stack leaves nothing behind: no log group, function, web ACL, dashboard,
  alarm or topic with the stack's name.

## Conventions

- Plain language in docs and comments. No filler, no marketing tone.
- Keep diagrams simple and low on text.
- Short commit messages. Never commit secrets, state files or real data.
- Any change to the rule thresholds (20 attempts, 15 cards, 100 cents, 300 s) must be made
  in the template (metric filter, alarm, WAF limit, output queries), the runbook and the
  README together.
