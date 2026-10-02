# Probe Watch

A small, disposable card-testing detection lab on AWS, run entirely from the AWS console.
Everything in it is synthetic: the checkout is fake, the payment provider is fake, and the
API accepts card *fingerprints*, never card numbers. The traffic simulator can only call
the stack's own API. Never run anything like this against a checkout you do not own.

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

1. Every attempt gets HTTP 200, approved or declined. The status code shows nothing.
   The business outcome in the log line shows everything.
2. Counting per device catches an attacker who uses one device (the **loud** attack:
   120 attempts, 1 device) and misses one who spreads 360 attempts over 80 devices
   (the **spread** attack: 4 or 5 attempts per device).
3. Counting probe-shaped attempts across the whole checkout (card saves, or payments of
   100 cents or less) catches both, and it is one metric filter plus one alarm.

An AWS WAF rate rule keyed on the same device fingerprint shows the edge layer: it stops
the loud attack and does nothing to the spread one. Defend in layers.

## What gets deployed

One CloudFormation file, [`probe-watch.yaml`](probe-watch.yaml), uploaded in the console.
No VPC.

| Piece | What it does |
|---|---|
| API Gateway REST API (regional), stage `demo` | `POST /pay` and `POST /save-card` |
| Lambda `<stack>-checkout` | Fake shop and fake payment provider in one. Prints one JSON event per attempt to log group `/probe-watch/<stack>/checkout-events` |
| AWS WAF web ACL on the stage | One rate-based rule on header `x-device-fp`: 20 requests per 5 minutes, action COUNT or BLOCK (parameter `WafMode`) |
| Lambda `<stack>-simulate` | Run it from the Lambda console Test tab with `{"scenario": "normal"}`, `"loud"` or `"spread"`. Sends traffic to the stack's own API and returns a status-code histogram |
| Metric filter, alarm, SNS topic | More than 20 probe-shaped attempts in a 5-minute window emails `AlertEmail` |
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
   COUNT, tick the IAM acknowledgement, Submit. CREATE_COMPLETE takes 3 to 4 minutes.
   Click Confirm in the subscription email from SNS. Open the links in the Outputs tab.
2. **Attack, then look.** In the Lambda console Test tab run `{"scenario": "normal"}`,
   then `"loud"`, then `"spread"`. Each Execution result is a histogram, and every request
   is HTTP 200. On the dashboard (time range 1h) the per-device table shows one loud device
   and nothing for the spread attack, the checkout-wide table shows both, and the alarm
   widget turns to ALARM within about five minutes, with an email.
3. **Turn the edge on.** CloudFormation > Update stack > use the existing template, set
   `WafMode` to BLOCK, Submit. Run `loud` again: 403s. Run `spread` again: still all 200.

Finish with Delete stack. Every log group, the dashboard, the saved queries, the alarm
and the topic belong to the stack, so nothing is left behind.

Step-by-step console instructions, what to say at each point, expected numbers and the
things that can go wrong on a first run are in
[`probe-watch-runbook.md`](probe-watch-runbook.md).

## Sandbox constraints the design follows

The lab is built for a 4-hour training sandbox (us-east-1, a restricted role with no
billing access, at most 10 Lambda functions, abuse monitoring on). So:

- One template, uploaded through the console, up in under 5 minutes with the IAM
  acknowledgement. No CloudShell, no CLI, no build step, no S3 bucket for code.
- Only CloudFormation, API Gateway, Lambda, WAF, CloudWatch, SNS and IAM. No VPC, no
  PrivateLink, no OpenSearch, no Fraud Detector.
- Lambda code is inline in the template (under 4,096 characters each), Python 3.12,
  standard library only. Two functions.
- Traffic stays in the hundreds: a full two-phase demo is about 1,100 requests. The
  simulator caps any single run at 600.
- Every log group is created by the stack, so deleting the stack leaves nothing behind.

## Checks for anyone changing the template

Not part of the demo. Run locally before committing a change:

```bash
cfn-lint aws/probe-watch.yaml                      # template is lint clean
python3 -m unittest discover -s aws/tests -v       # handlers with the HTTP call stubbed, dashboard body, query copies
```

The unit tests pull the handler code out of the template, so there is one copy of it.

## Files

All under `aws/`:

```
probe-watch.yaml          the stack: API, WAF, two Lambda functions, metric filter, alarm, SNS,
                          saved queries, dashboard
probe-watch-runbook.md    console-only deploy, demo script, teardown, first-run notes
AGENTS.md                 context and rules for this folder
tests/test_handlers.py    unit tests for the inline handlers and the dashboard body
```
