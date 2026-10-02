# Probe Watch runbook

Deploy, demo and tear down the card-testing lab inside a 4-hour sandbox session, using
only the AWS console. Read it once before the talk; during the talk, follow the numbered
steps.

Everything here is synthetic. The API takes card fingerprints, never card numbers, and
the simulator can only reach the stack's own API URL.

## Before the session

- Have `probe-watch.yaml` on the laptop you present from. The console upload needs the
  file.
- Use an email address you can open on stage. The alarm email is part of the demo.
- Pick the region in the top-right corner of the console first: **us-east-1
  (N. Virginia)**, or us-west-2. Everything below happens in that one region, and the
  links in the stack outputs carry it.
- Budget: deploy 3 to 4 minutes, phase 1 about 6 minutes including the alarm, the BLOCK
  update 1 to 2 minutes, phase 2 about 4 minutes, teardown 2 minutes.

## 1. Deploy (phase 1, WAF in COUNT mode)

1. CloudFormation > Stacks > **Create stack** > With new resources (standard).
2. Prepare template: *Choose an existing template* > *Upload a template file* >
   `probe-watch.yaml` > Next.
3. Stack name **`probe-watch`**. Parameters: `AlertEmail` = your address, `WafMode` =
   COUNT, `RateLimitPerDevice` = 20. Next.
4. Configure stack options: leave the defaults. At the bottom tick *I acknowledge that
   AWS CloudFormation might create IAM resources*. Next.
5. Review > **Submit**. Watch the Events tab. Expect **CREATE_COMPLETE** in 3 to 4 minutes.
6. Open the **Outputs** tab. Open `SimulatorTestUrl`, `DashboardUrl` and `AlarmUrl` each
   in a new browser tab. Keep the CloudFormation tab for phase 2.
7. **Confirm the email.** SNS sends "AWS Notification - Subscription Confirmation" to
   `AlertEmail`. Click *Confirm subscription*. Check spam. Until then the alarm fires but
   nobody is told.

While the stack is still creating, or right after: in the Lambda tab (`SimulatorTestUrl`
lands on the **Test** tab) create the three test events once. *Create new event*, Event
name `normal`, Event JSON `{"scenario": "normal"}`, **Save**. Repeat for `loud` and
`spread`. They stay in the event dropdown for the rest of the session.

## 2. Phase 1: attack with the WAF only counting

Lambda tab > Test > pick the event > **Test**. Wait for *Executing function: succeeded*
and read the response in **Execution result** (expand *Details* if it is folded). Run them
in this order:

| Event | What the simulator sends | How long | Expect in the histogram | Say |
|---|---|---|---|---|
| `normal` | 60 shoppers, one attempt each, baskets of 12 to 180 dollars, about 1 in 10 saves a card | 15 s | `"200": 60`, roughly 96% approved | This is a quiet afternoon. |
| `loud` | 120 attempts from one device `ct_loud_dev_1`, a fresh card every time, tiny payments or card saves | 30 s | `"200": 120`, about 35% approved, 65% declined | Every request got 200. A third of the stolen cards worked. The status code told us nothing. |
| `spread` | 360 attempts over 80 devices (4 or 5 each), 0.2 s apart, same shape as loud | 2 min | `"200": 360`, about 35% approved | Same attack, spread thin. No device stands out. |

The spread run keeps the spinner up for about two minutes. Do not click Test again; the
result arrives.

Then show the evidence on the **dashboard** tab. Set the time range to **1h** (top right)
and click the refresh arrow after each run. Four widgets:

- **Per device** table: `ct_loud_dev_1` at the top with 120 attempts and 120 cards (or
  split across two rows if the run straddled a 5-minute boundary; at least one row is well
  past 20 attempts and 15 cards). The spread devices show 4 or 5 attempts each and never
  cross the threshold. Point: the per-device rule sees the loud attack and is blind to the
  spread one.
- **Checkout-wide** table: the windows that held the loud and the spread runs with far
  more than 20 candidates; the normal run's windows with a handful (its card saves). Point:
  drop the device from the count and both attacks show up.
- **Alarm** widget: it evaluates whole 5-minute windows, so it turns to ALARM up to five
  minutes after the loud run, and the email arrives a few seconds later. Show the email.
  Point: this is the same checkout-wide count as a metric filter; it costs nothing and
  needs no SIEM. The `AlarmUrl` tab shows the same alarm with its history.
- **Graph**: probe-shaped attempts per 5 minutes against the threshold line, and "WAF
  counted", which is the requests the rate rule would have blocked. Nothing is blocked yet.

If you want to show the query text: CloudWatch > Logs > **Logs Insights** > *Saved and
sample queries* > folder `probe-watch` > `per-device` or `checkout-wide`, time range 1h,
*Run query*. The same queries are in the stack outputs.

Closing point for phase 1: every request returned HTTP 200. The outcome fields in the log
showed the attack, not the status code.

## 3. Phase 2: turn the edge on

1. CloudFormation tab > stack `probe-watch` > **Update** > *Make a direct update*.
2. *Use existing template* > Next.
3. `WafMode` = **BLOCK** > Next > Next (tick the IAM acknowledgement again) > **Submit**.
4. Expect **UPDATE_COMPLETE** in 1 to 2 minutes. Only the WAF rule changes; the API and the
   functions stay as they are.

Then, in the Lambda tab:

- `loud` > Test: expect a mix such as `"200": 20-40, "403": 80-100`. WAF counts requests
  over a 5-minute window and updates its counts with a short delay (AWS does not promise
  an exact figure), so the first requests past 20 can still get through. If the run shows
  no 403s at all, click Test again straight away: the device is already over the limit and
  the second run is blocked almost from the start. Blocked requests never reach the
  checkout, so they do not appear in the log tables; on the dashboard the "WAF blocked"
  line rises, and the `WebAclUrl` tab > *Sampled requests* lists them with action BLOCK.
- `spread` > Test: expect `"200": 360`. No device sends more than 5 requests, so the
  rate rule never acts.

Point: the edge rate limit is worth having and does nothing against the spread attack.
Layers: WAF at the edge, per-device counting, checkout-wide counting. None of them alone.

## 4. Teardown

CloudFormation > stack `probe-watch` > **Delete** > Delete. Expect **DELETE_COMPLETE** in
about 2 minutes. To prove nothing is left: Lambda > Functions has nothing starting
`probe-watch-`; CloudWatch > Log groups filtered on `/probe-watch/` is empty; CloudWatch >
Dashboards and Alarms show nothing with the stack's name; WAF & Shield > Web ACLs (in the
region) is empty; SNS > Topics is empty. The sandbox wipes everything at the end of the
session anyway; tearing down yourself proves the claim.

## Why the design fits the sandbox

- **One file, console upload, under 5 minutes.** No build step, no S3 bucket for code:
  both functions are inline (`ZipFile`, under 4,096 characters each), Python 3.12,
  standard library only.
- **Console only.** The attacker is a Lambda function you run from the Test tab. The
  evidence is a dashboard and two saved queries the stack creates. No CloudShell, no CLI.
- **No VPC.** The sandbox has no PrivateLink, so a Lambda in a VPC could not reach API
  Gateway or CloudWatch without a NAT. Both functions run outside any VPC.
- **Only allowed services.** CloudFormation, API Gateway, Lambda, WAF, CloudWatch (Logs,
  Logs Insights, metric filter, alarm, dashboard), SNS, IAM. Nothing else.
- **Two of the ten Lambda functions,** 256 MB each.
- **Traffic in the hundreds.** One full two-phase run is about 1,100 requests over ten
  minutes. The simulator refuses more than 600 requests per invocation and ignores any URL
  in its input; the only URL it knows is the one CloudFormation gave it.
- **Nothing left behind.** Log groups, roles, the API, the web ACL, the topic, the alarm,
  the dashboard and the saved queries are all stack resources. API Gateway access logging
  and WAF logging are deliberately off because they would need account-level or extra
  resources.

## Numbers to expect

| Check | Expected |
|---|---|
| `normal` | 60 requests, all 200, about 6 card saves, alarm stays OK |
| `loud` | 120 requests, all 200 in COUNT mode; `ct_loud_dev_1` with 120 attempts and 120 cards; alarm goes to ALARM within about 5 minutes; one email |
| `spread` | 360 requests, all 200; 80 devices with 4 or 5 attempts each; checkout-wide windows far above 20 |
| `BLOCK` + `loud` | 403s after roughly the 20th request, see the note above |
| `BLOCK` + `spread` | still all 200 |
| teardown | nothing with the stack's name left in Lambda, CloudWatch, WAF or SNS |

## First-run notes and things that can go wrong

The stack has been linted and the handlers unit-tested, but it has not yet been created
in a real account. Likely spots and what to do:

- **Stack fails on `WebAclAssociation`.** The resource ARN format for a REST API stage is
  `arn:aws:apigateway:<region>::/restapis/<api-id>/stages/<stage>`. The template builds
  exactly that. If WAF reports the entity unavailable, it is eventual consistency after
  creating the web ACL; CloudFormation retries, and a second create usually succeeds.
- **Stack fails on `Dashboard` ("Invalid dashboard body").** Delete the `Dashboard`
  resource and the `DashboardUrl` output from the template and create the stack again. The
  saved queries, the alarm and the Logs Insights console carry the whole demo without it.
- **Stack fails on a `SavedQuery` resource.** Delete the two `...SavedQuery` resources;
  paste `PerDeviceQuery` and `CheckoutWideQuery` from the outputs into Logs Insights.
- **Stack fails on `LoggingConfig`.** The functions write to log groups the stack
  creates, and each role only has `CreateLogStream` and `PutLogEvents` on its own group.
  If a deploy in some region rejects `LoggingConfig`, remove the two `LoggingConfig`
  blocks and the two `LogGroup` resources; Lambda then uses `/aws/lambda/<function>`,
  the metric filter, saved queries and dashboard must point at the checkout's default
  group, and teardown has to delete those two groups by hand.
- **API returns `{"message":"Missing Authentication Token"}`.** The path is wrong (the
  stage is part of the URL: `.../demo/pay`) or the deployment did not pick up a method.
  If you change a method, rename `ApiDeployment` in the template to force a new one.
- **Test tab looks stuck.** The spread run takes about two minutes. Wait. The function
  timeout is 10 minutes, far above any scenario. To shorten a run, use an event such as
  `{"scenario": "spread", "n": 200}`.
- **Execution result says failed.** Read the error in the result: `scenario must be
  normal, loud or spread` means a typo in the event JSON; `refusing to run` means the
  `API_URL` environment variable is not this stack's API Gateway URL, which only happens
  if someone edited the function.
- **No alarm email.** The subscription was not confirmed, or the mail is in spam. The
  alarm widget and the alarm page still change state, so the demo is not lost.
- **Alarm still OK right after `loud`.** It evaluates whole 5-minute windows aligned to the
  clock, the same fixed buckets the queries use. Wait for the window to close.
- **Dashboard tables empty.** Time range is not 1h, or the dashboard has not been
  refreshed since the run. Log widgets only re-run when you refresh.
- **No 403s in BLOCK mode.** Click Test on `loud` a second time; see phase 2.
- **Spread devices with more than 5 attempts in a window.** Only if two spread runs land in
  the same 5-minute window. Still under 20.
- **A console link from the outputs opens the wrong region.** The links carry the stack's
  region; if the console switched region, switch back.

Optional fields in the test event: `"n"` (attempts, capped at 600), `"seed"` (fixed
fingerprints for a repeatable run), `"pace"` (seconds between requests).
