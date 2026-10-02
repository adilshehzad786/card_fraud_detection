# Probe Watch

A small, disposable card-testing detection lab on AWS. Everything in it is synthetic:
the checkout is fake, the payment provider is fake, and the API accepts card
*fingerprints*, never card numbers. The traffic simulator can only call the stack's own
API. Never run anything like this against a checkout you do not own.

Built for the talk "Build it. Attack it. Catch it. A live card-testing lab on AWS" at an
AWS student community day (date to be confirmed; slides link to follow). It is the live
companion to the Python/Splunk lab in the root of this repository, whose verified numbers
the talk quotes: a fraud model caught 84% of classic fraud and 0 of 320 card-testing payments; a
per-device rule caught the loud attack and missed the spread one; a checkout-wide rule
caught 52 of 360 spread events; a decline-gated rule caught nothing because 90% of
attempts were approved.

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

One CloudFormation file, [`probe-watch.yaml`](probe-watch.yaml). No VPC.

| Piece | What it does |
|---|---|
| API Gateway REST API (regional), stage `demo` | `POST /pay` and `POST /save-card` |
| Lambda `<stack>-checkout` | Fake shop and fake payment provider in one. Prints one JSON event per attempt to log group `/probe-watch/<stack>/checkout-events` |
| AWS WAF web ACL on the stage | One rate-based rule on header `x-device-fp`: 20 requests per 5 minutes, action COUNT or BLOCK (parameter `WafMode`) |
| Lambda `<stack>-simulate` | Sends `normal`, `loud` or `spread` traffic to the stack's own API and returns a status-code histogram |
| Metric filter, alarm, SNS topic | More than 20 probe-shaped attempts in a 5-minute window emails `AlertEmail` |

Event schema, one JSON line per attempt:
`ts, flow (pay|save_card), outcome (approved|declined), decline_code, device_fp, card_fp, bin, amount_minor, currency, request_id, source_ip`.

Outcomes are deterministic per card fingerprint, like a real card that is either live
or dead: fingerprints starting `ct_` (the synthetic stolen cards) approve 35% of the time,
all others 96%.

## The demo in three steps

From AWS CloudShell, in a clone of this repository, inside this `aws/` folder. Each script
takes `STACK_NAME` from the environment and defaults to `probe-watch`.

1. **Deploy and confirm the email.** `scripts/deploy.sh you@example.com` creates the
   stack with the WAF rule in COUNT mode, then prints the outputs. Click Confirm in the
   subscription email from SNS.
2. **Attack, then look.** `scripts/demo.sh` runs `normal`, `loud` and `spread` and prints
   each histogram (all HTTP 200). `scripts/results.sh` runs the two Logs Insights
   queries from the terminal, or paste `PerDeviceQuery` and `CheckoutWideQuery` from the
   stack outputs into the Logs Insights console. The alarm email arrives within about
   five minutes of the loud run.
3. **Turn the edge on.** `scripts/deploy.sh you@example.com BLOCK` switches the same
   stack to blocking. `scripts/demo.sh loud spread` now shows 403s for the loud attack
   and still all 200s for the spread one.

Finish with `scripts/teardown.sh`, which deletes the stack and checks that no log group,
function, web ACL, alarm or topic is left behind.

Step-by-step instructions, what to say at each point, expected numbers and the things
that can go wrong on a first run are in [`probe-watch-runbook.md`](probe-watch-runbook.md).

## Sandbox constraints the design follows

The lab is built for a 4-hour training sandbox (us-east-1, a restricted role with no
billing access, at most 10 Lambda functions, abuse monitoring on). So:

- One template, uploaded through the console or deployed with the CLI, up in under
  5 minutes with `CAPABILITY_IAM`.
- Only CloudFormation, API Gateway, Lambda, WAF, CloudWatch, SNS and IAM. No VPC, no
  PrivateLink, no OpenSearch, no Fraud Detector.
- Lambda code is inline in the template (under 4,096 characters each), Python 3.12,
  standard library only. Two functions.
- Traffic stays in the hundreds: a full two-phase demo is about 1,100 requests. The
  simulator caps any single run at 600.
- Every log group is created by the stack, so deleting the stack leaves nothing behind.

## Checks

```bash
cfn-lint probe-watch.yaml                      # template is lint clean
python3 -m unittest discover -s tests -v       # both handlers, HTTP call stubbed, stdlib only
```

The unit tests pull the handler code out of the template, so there is one copy of it.

## Files

All under `aws/`:

```
probe-watch.yaml          the stack: API, WAF, two Lambda functions, metric filter, alarm, SNS
probe-watch-runbook.md    deploy, demo script, teardown, why the design fits the sandbox
scripts/deploy.sh         create or update the stack (COUNT or BLOCK)
scripts/demo.sh           run the scenarios and print the histograms and alarm state
scripts/results.sh        run both Logs Insights queries from the terminal
scripts/teardown.sh       delete the stack and check nothing is left
tests/test_handlers.py    unit tests for the inline handlers
```
