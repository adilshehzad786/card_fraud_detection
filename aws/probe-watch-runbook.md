# Probe Watch runbook

Deploy, demo and tear down the card-testing lab inside a 4-hour sandbox session. Read it
once before the talk; during the talk, follow the numbered steps.

Everything here is synthetic. The API takes card fingerprints, never card numbers, and
the simulator can only reach the stack's own API URL.

## Before the session

- Have this repository ready to clone or upload into CloudShell (a zip of the `aws/`
  folder is enough: `Actions > Upload file`, then `unzip`). Run the scripts from inside
  `aws/`.
- Use an email address you can open on stage. The alarm email is part of the demo.
- Know the two Logs Insights queries; they are also in the stack outputs
  (`PerDeviceQuery`, `CheckoutWideQuery`).
- Budget: deploy 3 to 4 minutes, phase 1 about 6 minutes including the alarm, the
  BLOCK update 1 to 2 minutes, phase 2 about 4 minutes, teardown 2 minutes.

## 1. Deploy (phase 1, WAF in COUNT mode)

Region: us-east-1 (us-west-2 also works). Pick the region in the console first; CloudShell
inherits it.

Console: CloudFormation > Create stack > Upload a template file > `probe-watch.yaml`.
Stack name `probe-watch` (the scripts assume it). Set `AlertEmail`. Leave `WafMode` at
COUNT and `RateLimitPerDevice` at 20. Tick the IAM acknowledgement. Create. Expect
CREATE_COMPLETE in 3 to 4 minutes.

CloudShell:

```bash
scripts/deploy.sh you@example.com
```

Either way, note the outputs: `ApiUrl`, `EventsLogGroup`, `SimulatorFunction`,
`AlarmName`, `WafMode` and the two queries.

**Confirm the email.** SNS sends "AWS Notification - Subscription Confirmation" to
`AlertEmail`. Click Confirm. Until then the alarm fires but nobody is told. Check spam.

## 2. Phase 1: attack with the WAF only counting

```bash
scripts/demo.sh            # normal, then loud, then spread
```

What each run prints, and what to say:

| Scenario | What the simulator sends | Expect in the histogram | Say |
|---|---|---|---|
| `normal` | 60 shoppers, one attempt each, baskets of 12 to 180 dollars, about 1 in 10 saves a card | `200: 60`, roughly 96% approved | This is a quiet afternoon. |
| `loud` | 120 attempts from one device `ct_loud_dev_1`, a fresh card every time, tiny payments or card saves | `200: 120`, about 35% approved, 65% declined | Every request got 200. A third of the stolen cards worked. The status code told us nothing. |
| `spread` | 360 attempts over 80 devices (4 or 5 each), 0.2 s apart, same shape as loud | `200: 360`, about 35% approved | Same attack, spread thin. No device stands out. |

Then show the evidence. In the console: CloudWatch > Logs Insights, pick the log group
from `EventsLogGroup`, time range **1h**. Or from the terminal:

```bash
scripts/results.sh         # both queries, last 60 minutes
```

**Per-device query** (one device, one 5-minute window):

```
filter ispresent(flow)
| stats count(*) as attempts, count_distinct(card_fp) as cards by device_fp, bin(5m)
| sort attempts desc | limit 20
```

Expect `ct_loud_dev_1` at the top with 120 attempts and 120 cards (or split across two
windows if the run straddled a 5-minute boundary; at least one window is well past 20
attempts and 15 cards). The spread devices show 4 or 5 attempts each and never cross the
threshold. Point: the per-device rule sees the loud attack and is blind to the spread one.

**Checkout-wide query** (all devices, probe-shaped attempts only):

```
filter ispresent(flow) and (flow = "save_card" or amount_minor <= 100)
| stats count(*) as candidates, count_distinct(card_fp) as cards by bin(5m)
| sort candidates desc | limit 20
```

Expect the windows that held the loud and the spread runs with far more than 20
candidates, and the normal run's windows with a handful (its card saves). Point: drop the
device from the count and both attacks show up.

**The alarm.** CloudWatch > Alarms > `probe-watch-checkout-wide-probes`. It evaluates
whole 5-minute windows, so it moves to ALARM up to five minutes after the loud run, and the
email arrives a few seconds later. Show the email. Point: this is the same checkout-wide
count as a metric filter; it costs nothing and needs no SIEM.

Closing point for phase 1: every request returned HTTP 200. The outcome fields in the
log showed the attack, not the status code.

## 3. Phase 2: turn the edge on

Update the same stack to `WafMode=BLOCK`. Console: Update stack > Use current template >
change `WafMode`. CloudShell:

```bash
scripts/deploy.sh you@example.com BLOCK
```

Only the WAF rule changes; the API and the functions stay as they are. Then:

```bash
scripts/demo.sh loud spread
```

- `loud`: expect a mix such as `200: 20-40, 403: 80-100`. WAF counts requests over a
  5-minute window and updates its counts with a short delay (AWS does not promise an exact figure), so the first requests
  past 20 can still get through. If the run shows no 403s at all, run `scripts/demo.sh
  loud` again straight away: the device is already over the limit and the second run is
  blocked almost from the start. Blocked requests never reach the checkout, so they do not
  appear in the log group; they appear in WAF's sampled requests and metrics.
- `spread`: expect `200: 360`. No device sends more than 5 requests, so the rate rule
  never acts.

Point: the edge rate limit is worth having and does nothing against the spread attack.
Layers: WAF at the edge, per-device counting, checkout-wide counting. None of them alone.

## 4. Teardown

```bash
scripts/teardown.sh
```

It deletes the stack, waits, then checks for leftover log groups, functions, web ACLs,
alarms and topics with the stack's name. Expect `nothing left behind`. Both log groups
belong to the stack, which is why nothing survives. The sandbox wipes everything at the
end of the session anyway; tearing down yourself proves the claim.

## Why the design fits the sandbox

- **One file, console upload, under 5 minutes.** No build step, no S3 bucket for code:
  both functions are inline (`ZipFile`, under 4,096 characters each), Python 3.12,
  standard library only.
- **No VPC.** The sandbox has no PrivateLink, so a Lambda in a VPC could not reach API
  Gateway or CloudWatch without a NAT. Both functions run outside any VPC.
- **Only allowed services.** CloudFormation, API Gateway, Lambda, WAF, CloudWatch (Logs,
  Logs Insights, metric filter, alarm), SNS, IAM. Nothing else.
- **Two of the ten Lambda functions,** 256 MB each.
- **Traffic in the hundreds.** One full two-phase run is about 1,100 requests over ten
  minutes. The simulator refuses more than 600 requests per invocation and ignores any URL
  in its input; the only URL it knows is the one CloudFormation gave it.
- **Nothing left behind.** Log groups, roles, the API, the web ACL, the topic and the alarm
  are all stack resources. API Gateway access logging and WAF logging are deliberately off
  because they would need account-level or extra resources.

## Numbers to expect

| Check | Expected |
|---|---|
| `normal` | 60 requests, all 200, about 6 card saves, alarm stays OK |
| `loud` | 120 requests, all 200 in COUNT mode; `ct_loud_dev_1` with 120 attempts and 120 cards; alarm goes to ALARM within about 5 minutes; one email |
| `spread` | 360 requests, all 200; 80 devices with 4 or 5 attempts each; checkout-wide windows far above 20 |
| `BLOCK` + `loud` | 403s after roughly the 20th request, see the note above |
| `BLOCK` + `spread` | still all 200 |
| teardown | `nothing left behind` |

## First-run notes and things that can go wrong

The stack has been linted and the handlers unit-tested, but it has not yet been created
in a real account. Likely spots and what to do:

- **Stack fails on `WebAclAssociation`.** The resource ARN format for a REST API stage is
  `arn:aws:apigateway:<region>::/restapis/<api-id>/stages/<stage>`. The template builds
  exactly that. If WAF reports the entity unavailable, it is eventual consistency after
  creating the web ACL; CloudFormation retries, and a second create usually succeeds.
- **Stack fails on `LoggingConfig`.** The functions write to log groups the stack
  creates, and each role only has `CreateLogStream` and `PutLogEvents` on its own group.
  If a deploy in some region rejects `LoggingConfig`, remove the two `LoggingConfig`
  blocks and the two `LogGroup` resources; Lambda then uses `/aws/lambda/<function>`,
  the metric filter must point at the checkout's default group, and teardown has to
  delete those two groups by hand.
- **API returns `{"message":"Missing Authentication Token"}`.** The path is wrong (the
  stage is part of the URL: `.../demo/pay`) or the deployment did not pick up a method.
  If you change a method, rename `ApiDeployment` in the template to force a new one.
- **`aws lambda invoke` times out.** The spread run takes about two minutes, longer than
  the CLI's default 60-second read timeout. The scripts pass `--cli-read-timeout 0`.
  If you invoke by hand, add it, along with `--cli-binary-format raw-in-base64-out`.
- **No alarm email.** The subscription was not confirmed, or the mail is in spam. The
  alarm state in the console still changes, so the demo is not lost.
- **Alarm still OK right after `loud`.** It evaluates whole 5-minute windows aligned to the
  clock, the same fixed buckets the queries use. Wait for the window to close.
- **No 403s in BLOCK mode.** Run `loud` a second time; see phase 2.
- **Spread devices with more than 5 attempts in a window.** Only if two spread runs land in
  the same 5-minute window. Still under 20.
- **CloudShell region.** CloudShell uses the console's region. The stack and the commands
  must be in the same one.

By hand, the invoke the scripts wrap is:

```bash
aws lambda invoke --function-name probe-watch-simulate \
  --cli-binary-format raw-in-base64-out --cli-read-timeout 0 \
  --payload '{"scenario":"loud"}' /tmp/out.json && cat /tmp/out.json
```

Optional input fields: `"n"` (attempts, capped at 600), `"seed"` (fixed fingerprints for a
repeatable run), `"pace"` (seconds between requests).
