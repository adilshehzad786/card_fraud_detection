# Card-Testing Detection Lab

Card testing is when attackers run stolen card details through a checkout, as small payments or $0 card saves, to find out which cards still work. Each attempt looks like an ordinary low-value purchase, so a fraud model that scores one transaction at a time can miss the whole campaign.

This lab reproduces that gap with synthetic checkout events. It then uses Splunk to show which kind of aggregation catches each attack, what it misses and which genuine customers it flags by mistake.

<!-- markdownlint-disable-next-line MD033 -->
<p align="center"><img src="docs/images/results-chart.png" width="440" alt="Results summary: the transaction model flags none of either attack; the per-device rule catches only the loud attack; the checkout-wide rule catches the loud attack and 14% of the spread attack"></p>

## Results

The baseline run (seed 61027) has 1,972 events between 10:00 and 14:00 UTC on 26 September 2026: 1,492 genuine events and two card-testing attacks with 480 events between them.

| Detector | Signal | Loud attack (1 device, 20 min) | Spread attack (80 devices, 3 h) | Genuine events flagged (of 1,492) |
| --- | --- | --- | --- | --- |
| Transaction model | Amount, hour, distance and card history of each payment | 0 of 80 payments | 0 of 240 payments | 0 |
| D1: per-device rule | At least 20 attempts and 15 cards on one device in 5 min | **120 of 120** | 0 of 360 | 24 |
| D2: checkout-wide rule | At least 20 small payments or card saves, with 15 cards, across the checkout in 5 min | **120 of 120** | **52 of 360** | 67 |
| D0: decline-gated rule | D1, plus at least 80% of attempts declined | 0 of 120 | 0 of 360 | 0 |

- **A model can score well and still miss this.** On held-out classic fraud (large, late-night, far-from-home purchases) the model reaches 84.2% recall and 95.3% precision. The card-testing payments all scored below 0.001, against its 0.48 threshold. It scores payments only, so the attacks' 160 card saves were never scored.
- **Declines weren't the signal.** 90% of attack attempts were approved, so the decline-gated rule saw nothing.
- **Spreading out defeats per-device limits.** The spread attack never put more than 2 attempts on one device in a 5-minute window.
- **The checkout-wide rule needed help.** On its own, the spread attack never produced more than 14 candidate events in a window, below the threshold of 20. All 52 detections came from windows where genuine customers pushed the total over 20.

The [lab guide](docs/LAB_GUIDE.md#how-splunk-detected-each-attack) walks through each Splunk result window by window.

## Quick start

You need Docker Desktop with 3 CPUs, 5 GB of memory and about 6 GB of free disk space for the Splunk container. From the repository root:

```bash
python3 splunk/start_lab.py     # start Splunk and load data/card_testing_events.csv
python3 splunk/verify_lab.py    # check every Splunk result against the Python flags
```

Then open <http://127.0.0.1:8000>, set the time range to **All time**, and follow the [lab guide](docs/LAB_GUIDE.md). The first start downloads the Splunk image (4.5 GB on disk). On Apple Silicon, where the x86-64 image runs under emulation, Splunk takes 3–5 minutes to start. The Splunk scripts need only Python 3.8 or later with the standard library.

### Regenerate the events

- **Google Colab:** upload `card_testing_lab.ipynb`, then choose Runtime → Run all. The last cell packages the outputs as a ZIP.
- **Locally**, with Python 3.12 or later:

  ```bash
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  python card_testing_lab.py --output-dir lab_output
  ```

The default settings reproduce `data/card_testing_events.csv` byte for byte, with SHA-256 `b992512c715bf49c9b300cb00d1eb745bf3b191e0f436047480f63cd75556599`. To load a file you generated, run `python3 splunk/start_lab.py --csv lab_output/card_testing_events.csv`.

## Repository layout

```text
card_testing_lab.py         Generates events, trains the model, applies the rules, writes outputs
card_testing_lab.ipynb      The same experiment for Google Colab (embeds card_testing_lab.py)
requirements.txt            Pinned Python dependencies
data/
  card_testing_events.csv   Baseline events (seed 61027) with the Python detector flags
splunk/
  start_lab.py              Starts Splunk in Docker and indexes one CSV, exactly once
  verify_lab.py             Runs the lab searches and checks them against the indexed CSV
  card_testing_rules.spl    Detection, parity and coverage searches
  compose.yaml              Splunk 10.4.3, Free license, bound to 127.0.0.1
  app/                      Splunk app: lab index, CSV parsing (time from epoch), file input
docs/
  LAB_GUIDE.md              Step-by-step walkthrough with screenshots
  images/
```

## Scope

- **Synthetic by design.** The identifiers are invented. The lab contains no card numbers or customer records and makes no calls to a payment service. The generator creates the patterns under test, so the results show what each detector can see; they are not real-world detection rates.
- **Retrospective windows.** Rules use fixed 5-minute UTC windows evaluated after the fact. Coverage is the share of events inside flagged windows, not a real-time blocking rate.
- **Not a product benchmark.** This is not an evaluation of Stripe Radar, Splunk or fraud machine learning in general. Models trained on card-testing behaviour can detect it.
- **Local and isolated.** Splunk Free has no login, so the container listens on 127.0.0.1 only and uses its own index and Docker volumes.

## References

- Stripe: [Protect yourself from card testing](https://docs.stripe.com/disputes/prevention/card-testing)
- Stripe: [How Stripe Radar responded to a new wave of card testing](https://stripe.com/blog/how-stripe-radar-responded-to-a-new-wave-of-card-testing) (15 January 2025), on card testing with rising approval rates
- OWASP: [OAT-001 Carding](https://owasp.github.io/www-project-automated-threats-to-web-applications/assets/oats/EN/OAT-001_Carding.html)
- Splunk: [docker-splunk](https://github.com/splunk/docker-splunk) and [About Splunk Free](https://help.splunk.com/en/splunk-enterprise/administer/admin-manual/9.4/configure-splunk-licenses/about-splunk-free)
