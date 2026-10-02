# Probe Watch runbook

Deploy, demo and tear down the card-testing lab inside a 4-hour sandbox session, using
only the AWS console. Read it once before the talk; during the talk, follow the numbered
steps.

Everything here is synthetic. Use only synthetic card and device fingerprints, never
card numbers. The simulator uses the stack's own API URL.

The stack has been linted and the handlers unit-tested, but it has **not been deployed
to an AWS account**. Timings below are estimates to check during rehearsal.

## Before the session

- Have `probe-watch.yaml` on the laptop you present from. The console upload needs the
  file.
- Use an email address you can open on stage. The alarm email is part of the demo.
- Pick the region in the top-right corner of the console first: **us-east-1
  (N. Virginia)**, or us-west-2. Everything below happens in that one region, and the
  links in the stack outputs carry it.
- Budget: deploy 3 to 4 minutes, phase 1 about 6 minutes including the alarm, the BLOCK
  update 1 to 2 minutes, phase 2 about 4 minutes, teardown 2 minutes.
- Check the [sandbox requirements and detector limitations](README.md). Console template
  upload needs S3 permissions as well as access to the services in the stack. Run only
  one copy of this lab in the account/region: its custom metric is shared between stacks.

## 1. Deploy (phase 1, WAF in COUNT mode)

1. CloudFormation > Stacks > **Create stack** > With new resources (standard).
2. Prepare template: *Choose an existing template* > *Upload a template file* >
   `probe-watch.yaml` > Next.
3. Stack name **`probe-watch`**. Parameters: `AlertEmail` = your address, `WafMode` =
   COUNT, `RateLimitPerDevice` = 20. Next.
4. Configure stack options: leave the defaults. At the bottom tick *I acknowledge that
   AWS CloudFormation might create IAM resources*. Next.
5. Review > **Submit**. Watch the Events tab and wait for **CREATE_COMPLETE**.
6. Open the **Outputs** tab. Open `SimulatorTestUrl`, `DashboardUrl` and `AlarmUrl` each
   in a new browser tab. Keep the CloudFormation tab for phase 2.
7. **Confirm the email.** SNS sends "AWS Notification - Subscription Confirmation" to
   `AlertEmail`. Click *Confirm subscription*. Check spam. Until then the alarm fires but
   nobody is told.

After the stack completes, in the Lambda tab (`SimulatorTestUrl` lands on the **Test**
tab) create the three test events once. *Create new event*, Event name `normal`, Event
JSON `{"scenario": "normal"}`, **Save**. Repeat for `loud` and
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

The spread run can keep the spinner up for about two minutes. Wait for its result before
starting another run. Repeated runs add to the same device counts and alarm history.

Then show the evidence on the **dashboard** tab. Set the time range to **1h** (top right)
and click the refresh arrow after each run. Four widgets:

- **Per device** table: `ct_loud_dev_1` at the top with 120 attempts and 120 cards (or
  split across two query bins if the run crossed a bin boundary; at least one row is well
  past 20 attempts and 15 cards). The spread devices show 4 or 5 attempts each and never
  cross the threshold in a single run. These are count tables, not filtered detector
  matches: inspect both the attempt and distinct-card columns.
- **Checkout-wide** table: the windows that held the loud and the spread runs with far
  more than 20 candidates; the normal run's windows with a handful (its card saves). Point:
  drop the device from the count and both attacks show up.
- **Alarm** widget: the volume alarm uses a sliding five-minute window and evaluates
  every minute. Allow for log ingestion and evaluation, then check ALARM and the email.
  It counts more than 20 probe-shaped attempts without requiring distinct cards; this is
  a simpler indicator than the reference lab's D2 rule. The `AlarmUrl` tab shows its
  history. A fresh normal run should stay below the threshold; prior attack traffic can
  keep the alarm in ALARM.
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
4. Wait for **UPDATE_COMPLETE**. Only the WAF rule action changes.

Then, in the Lambda tab:

- `loud` > Test: inspect the histogram for 403s. AWS WAF estimates rates over a rolling
  five-minute window; it does not block exactly on request 21. Detection can lag several
  minutes, although AWS says it is usually below 30 seconds. Earlier COUNT traffic uses
  the same device, so blocking can also begin immediately. If the first run finishes
  without 403s, allow propagation time and try one more run. Record the result in rehearsal;
  a second run is not a guarantee. Blocked requests never reach the checkout's logs. Look
  for the "WAF blocked" graph and `WebAclUrl` > *Sampled requests* > action BLOCK.
- `spread` > Test: expect `"200": 360`. Each device sends at most 5 requests in this
  run. Repeated runs accumulate within WAF's window, so avoid unnecessary retries.

Point: the edge rate limit is worth having and does nothing against the spread attack.
Layers: WAF at the edge, per-device counting, checkout-wide counting. None of them alone.

## 4. Teardown

CloudFormation > stack `probe-watch` > **Delete** > Delete. Wait for **DELETE_COMPLETE**.
Check the stack's Resources list and the service consoles for its functions, both log
groups, API, roles, web ACL, dashboard, saved queries, alarm and SNS topic. Filter by the
stack's name; other sandbox resources need not be empty.

CloudWatch metric history is not deleted with the stack; it expires automatically.
Console upload also stores the template in a CloudFormation S3 bucket outside this
stack. If cleanup is required and permitted, remove this lab's uploaded template object
in the S3 console, leaving other objects and shared buckets intact. The sandbox's final
reset handles any remaining account artifacts.

## First-run notes and things that can go wrong

- **Stack creation fails.** In CloudFormation > Events, find the first failed resource
  and record its status reason. Resolve that reported error and rerun local lint and
  tests before another deploy; do not remove dependent resources as a generic workaround.
- **API returns `{"message":"Missing Authentication Token"}`.** The path is wrong (the
  stage is part of the URL: `.../demo/pay`) or the deployment did not pick up a method.
  If you change a method, rename `ApiDeployment` in the template to force a new one.
- **Test tab looks stuck.** The spread run normally takes about two minutes, and the
  function timeout is 10 minutes. Slow or failed HTTP calls can extend it. To shorten a
  run, use an event such as `{"scenario": "spread", "n": 200}`.
- **Execution result says failed.** Read the error in the result: `scenario must be
  normal, loud or spread` means a typo in the event JSON; `refusing to run` means the
  `API_URL` environment variable is not this stack's API Gateway URL, which only happens
  if someone edited the function.
- **No alarm email.** Check subscription confirmation, spam and the alarm's state
  history. Notifications follow state changes; another attack while it remains in ALARM
  does not send another ALARM email.
- **Dashboard tables empty.** Time range is not 1h, or the dashboard has not been
  refreshed since the run. Log widgets only re-run when you refresh.
- **A console link from the outputs opens the wrong region.** The links carry the stack's
  region; if the console switched region, switch back.

Optional fields in the test event: `"n"` (attempts, capped at 600), `"seed"` (fixed
fingerprints for a repeatable run), `"pace"` (seconds between requests).

AWS behavior references: [alarm evaluation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarm-evaluation.html),
[alarm notification actions](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarm-actions.html),
[WAF rate-limit caveats](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based-caveats.html),
[console template storage](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/cfn-console-create-stack.html),
and [metric retention](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html).
