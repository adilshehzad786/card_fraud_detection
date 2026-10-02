# Probe Watch

A small, disposable card-testing detection lab on AWS, run entirely from the AWS console.
Everything in it is synthetic: the checkout and payment provider are fake. Use only
synthetic card and device fingerprints; never submit card numbers. The traffic simulator
uses the stack's own API. Never run anything like this against a checkout you do not own.

The template has local lint and handler checks, but has **not been deployed to an AWS
account**. Rehearse deployment, alarm delivery, WAF behavior and teardown before the talk.

Built for the talk "Build it. Attack it. Catch it. A live card-testing lab on AWS" at an
AWS student community day (date to be confirmed; slides link to follow). It is the live
companion to the Python/Splunk lab in the root of this repository, whose verified numbers
the talk quotes: a fraud model caught 84% of classic fraud and 0 of 320 card-testing
payments; a per-device rule caught the loud attack and missed the spread one; a
checkout-wide rule caught 52 of 360 spread events; a decline-gated rule caught nothing
because 90% of attempts were approved.

## What the lab shows

Card testing is when bots use a real checkout to find out which stolen cards still work,
with tiny payments or "save this card" attempts. The lab makes three things visible:

1. The fake checkout returns HTTP 200 for both approved and declined attempts. Read the
   business outcome in the log; the HTTP status alone does not identify card testing.
2. Counting per device catches an attacker who uses one device (the **loud** attack:
   120 attempts, 1 device) and misses one who spreads 360 attempts over 80 devices
   (the **spread** attack: 4 or 5 attempts per device).
3. A metric filter and alarm highlight the volume of probe-shaped attempts across the
   whole checkout (card saves, or payments of 100 minor units or less).

The AWS alarm is a simplified volume indicator, not the Python/Splunk D2 detector: it
uses **more than 20** attempts without a distinct-card or USD condition. Its default
five-minute evaluation window slides every minute. The saved queries show counts for
inspection rather than filtering to D1/D2 matches, and `bin(5m)` rounds timestamps to
the nearest five minutes. The reference D2 rule still requires at least 20 candidates
and 15 distinct cards in fixed UTC-aligned windows, using floor rather than rounding.
See AWS's [alarm evaluation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarm-evaluation.html)
and [query datetime functions](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax-operations-functions.html).

An AWS WAF rate rule keyed on the device fingerprint demonstrates an additional layer.
Its rate estimates use a rolling window, with a delay before blocking; it is not an exact
20-request cutoff. The spread scenario remains below that per-device limit.

Other limits to resolve before calling this deployment verified:

- The custom metric `ProbeWatch/ProbeShapedAttempts` is shared by all stacks in the
  account/region. Concurrent demos combine counts, and recent history survives recreation.
- The input guard rejects long digit-only card values but does not reject every formatted
  card number. Use the supplied synthetic simulator; do not rely on the guard to sanitize
  arbitrary input.
- Some HTTP transport failures, including socket timeouts, can abort the simulator
  without returning its histogram. The local tests do not verify real AWS networking.

## What gets deployed

One CloudFormation file, [`probe-watch.yaml`](probe-watch.yaml), uploaded in the console.
No VPC.

| Piece | What it does |
|---|---|
| API Gateway REST API (regional), stage `demo` | `POST /pay` and `POST /save-card` |
| Lambda `<stack>-checkout` | Fake shop and fake payment provider in one. Prints one JSON event per attempt to log group `/probe-watch/<stack>/checkout-events` |
| AWS WAF web ACL on the stage | One rate-based rule on header `x-device-fp`: 20 requests per 5 minutes, action COUNT or BLOCK (parameter `WafMode`) |
| Lambda `<stack>-simulate` | Run it from the Lambda console Test tab with `{"scenario": "normal"}`, `"loud"` or `"spread"`. Sends traffic to the stack's own API and returns a status-code histogram |
| Metric filter, alarm, SNS topic | More than 20 probe-shaped attempts in a sliding 5-minute window changes the alarm to ALARM and emails the confirmed `AlertEmail` subscription |
| CloudWatch dashboard and two saved Logs Insights queries | One page with the per-device table, the checkout-wide table, the alarm, and a graph of probe-shaped attempts next to what the WAF rule counted or blocked |

The stack outputs include console links: `SimulatorTestUrl`, `DashboardUrl`, `AlarmUrl`,
`WebAclUrl`.

Event schema, one JSON line per attempt:
`ts, flow (pay|save_card), outcome (approved|declined), decline_code, device_fp, card_fp, bin, amount_minor, currency, request_id, source_ip`.

Outcomes are deterministic per card fingerprint, like a real card that is either live
or dead: fingerprints starting `ct_` (the synthetic stolen cards) approve 35% of the time,
all others 96%.

## The demo in three steps, all in the console

1. **Deploy and confirm the email.** CloudFormation > Create stack > upload
   `probe-watch.yaml`, stack name `probe-watch`, set `AlertEmail`, leave `WafMode` at
   COUNT, tick the IAM acknowledgement, Submit. Wait for CREATE_COMPLETE.
   Click Confirm in the subscription email from SNS. Open the links in the Outputs tab.
2. **Attack, then look.** In the Lambda console Test tab run `{"scenario": "normal"}`,
   then `"loud"`, then `"spread"`. Each Execution result is a histogram, and every request
   should be HTTP 200. On the dashboard (time range 1h), compare the loud device's count
   with the small counts for individual spread devices. The checkout-wide table shows
   both attacks' volume. Allow for log ingestion and alarm evaluation before checking
   ALARM and the email.
3. **Turn the edge on.** CloudFormation > Update stack > use the existing template, set
   `WafMode` to BLOCK, Submit. Run `loud` again and inspect 403s after WAF detects the
   rate. Run `spread` again: expect all 200. See the runbook for timing limits.

Finish with Delete stack and check that its resources are gone. Published CloudWatch
metrics expire separately, and the console's uploaded template is stored outside the
stack; see the runbook's teardown notes.

Step-by-step console instructions, what to say at each point, expected numbers and the
things that can go wrong on a first run are in
[`probe-watch-runbook.md`](probe-watch-runbook.md).

## Sandbox constraints the design follows

The lab is built for a 4-hour training sandbox (us-east-1, a restricted role with no
billing access, at most 10 Lambda functions, abuse monitoring on). So:

- One template, uploaded through the console, with a deployment target under 5 minutes
  and the IAM acknowledgement. No CloudShell, CLI or build step.
- Only CloudFormation, API Gateway, Lambda, WAF, CloudWatch, SNS and IAM. No VPC, no
  PrivateLink, no OpenSearch, no Fraud Detector. The console upload also requires S3
  access to store the template; verify the sandbox permits it.
- Lambda code is inline in the template (under this lab's 4,096-character limit per
  handler), Python 3.12, standard library only. Two functions, 256 MB each.
- Each scenario sends hundreds of requests at most; a full two-phase demo sends 1,020
  before retries. The simulator caps any single run at 600.
- Both log groups are stack resources and are deleted with it.

## Checks for anyone changing the template

Not part of the demo. Run locally before committing a change:

```bash
cfn-lint aws/probe-watch.yaml                      # template is lint clean
python3 -m unittest discover -s aws/tests -v       # handlers with the HTTP call stubbed, dashboard body, query copies
```

The unit tests pull the handler code out of the template, so there is one copy of it.
Read [`AGENTS.md`](AGENTS.md) before changing the AWS lab.
