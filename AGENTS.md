# AGENTS.md

## Project overview

**Card testing detection lab** — a defensive security experiment that shows a typical fraud
model missing card-testing attacks (stolen cards probed with tiny payments or $0 card saves),
while simple counting rules in Splunk catch them only partially. All data is synthetic; there
are no real card numbers, no customer records, and no payment-provider integration.

The lab does three things:

1. **Generates** 1,972 synthetic checkout events over 4 hours (26 Sep 2026, 10:00–14:00 UTC):
   legitimate shoppers plus a "loud" attack (120 attempts, 1 device, 20 min) and a "spread"
   attack (360 attempts, 80 devices, 3 h).
2. **Trains** a deliberately narrow Random Forest fraud model on classic-fraud data
   (amount, UTC hour, distance from home, prior card attempts). It scores payments only.
3. **Applies three counting rules** in fixed UTC-aligned 5-minute windows
   (`bucket_epoch = floor(epoch/300)*300`), then cross-checks the Python results against
   Splunk SPL searches run inside a local Docker container.

The `aws/` folder holds **Probe Watch**, the live AWS version of the same lab for the talk
(API Gateway + WAF in front of a fake checkout on Lambda, detection in CloudWatch). It has
its own `aws/AGENTS.md`; read it before touching anything there.

Detectors (thresholds are hard-coded and must stay consistent across Python and SPL):

- **D1 `device_rule`**: one device with ≥20 events and ≥15 distinct cards in a window.
- **D2 `checkout_rule`**: checkout-wide, over "candidate" events only (card saves, or USD
  payments ≤100 cents); ≥20 candidates and ≥15 distinct cards in a window.
- **D0 `decline_rule`**: D1 plus ≥80% of the device's events in the window declined.
- **`toy_model`**: model score ≥ validation-F1-optimal threshold (0.48); payments only.

## Repository layout

```
card_testing_lab.py       single-file generator/trainer/rules (stdlib CLI + pandas/sklearn/matplotlib)
card_testing_lab.ipynb    the same logic as a Google Colab notebook (the notebook embeds the .py)
requirements.txt          pinned packages, tested with Python 3.12 (3.12 or 3.13 supported)
data/card_testing_events.csv   committed baseline dataset (seed 61027), checked by SHA-256
splunk/
  start_lab.py            starts Splunk in Docker and loads one CSV (idempotent, stdlib only)
  verify_lab.py           runs the SPL searches and asserts results match the CSV flags (14 checks)
  card_testing_rules.spl  the four SPL searches (D1, D2, parity, scenario coverage)
  compose.yaml            Splunk 10.4.3, Free license, bound to 127.0.0.1 only
  app/                    Splunk app: index=card_testing_lab, CSV sourcetype, file monitor
docs/
  LAB_GUIDE.md            step-by-step walkthrough with expected results
  images/                 screenshots and the results chart
tools/
  sync_notebook.py        re-embeds card_testing_lab.py, requirements.txt and the SPL into
                          the notebook after edits (stdlib only; --check fails on drift)
aws/
  AGENTS.md               context and rules for the AWS lab; read first
  probe-watch.yaml        one CloudFormation stack: API Gateway, WAF, two inline Lambdas,
                          metric filter, alarm, SNS, saved queries, dashboard
                          (cfn-lint clean, not yet deployed)
  probe-watch-runbook.md  console-only deploy, demo script, teardown, first-run notes
  README.md               what the AWS lab shows and the three-step demo
  tests/                  stdlib unittest for the inline handlers and the dashboard body
.lab-state/               gitignored; holds the container's bootstrap password (splunk.env)
```

## Build, run, and test commands

**Regenerate the data** (Python 3.12/3.13):

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python card_testing_lab.py --output-dir lab_output
```

Outputs: `card_testing_events.csv`, `card_testing_metrics.csv`, `classic_split_manifest.csv`,
`card_testing_metrics.json`, and `card_testing_chart.png` (chart only for the default
80-device baseline). With default settings the CSV is byte-identical to the committed
baseline: SHA-256 `b992512c715bf49c9b300cb00d1eb745bf3b191e0f436047480f63cd75556599`
(`shasum -a 256 lab_output/card_testing_events.csv`).

**Run the Splunk lab** (needs Docker Desktop with ≥3 CPUs, 5 GB memory, ~6 GB disk free;
scripts need only Python 3.8+, no extra packages):

```bash
python3 splunk/start_lab.py      # starts Splunk, loads data/card_testing_events.csv, once
python3 splunk/verify_lab.py     # test suite: expect "14 of 14 checks passed"; exits 1 on failure
```

Splunk UI is at http://127.0.0.1:8000 (Splunk Free, no login); set the time range to
**All time** because events are dated 26 September 2026. First run pulls a ~4.5 GB image;
on Apple Silicon the x86-64 image runs under emulation and takes 3–5 minutes to start.

Reset: `docker compose --env-file .lab-state/splunk.env -f splunk/compose.yaml down -v`.

## Testing and verification strategy

There is no conventional unit-test framework (no pytest). Correctness is enforced by
self-checks and cross-implementation agreement:

- `aws/`: `python3 -m unittest discover -s aws/tests` runs the handler tests (they read
  the code out of `aws/probe-watch.yaml`, so there is one copy of it) and
  `cfn-lint aws/probe-watch.yaml` must be clean. Rule thresholds there (20 attempts,
  15 cards, 100 cents, 300 s) must match this lab's `card_testing_lab.py`.

- `card_testing_lab.py` asserts generator reproducibility (regenerating from the same seed
  yields an identical DataFrame), unique event/card IDs, UTC timestamp round-trips, disjoint
  stratified train/validation/test splits, and no label/ID leakage into features.
- `reference_rule_flags()` in `card_testing_lab.py` re-implements the three rules in plain
  Python (no pandas groupby) and asserts the flag sets match.
- `splunk/verify_lab.py` recomputes ingestion, both rule windows, per-event parity, and
  scenario coverage **in Splunk SPL** and compares against the flags Python wrote into the
  CSV that Splunk actually indexed. All rule/SPL changes must end with
  "14 of 14 checks passed". Expected values are read from the indexed CSV, so variant
  datasets are checked against their own flags.
- Because of this parity design, **any change to rule logic or thresholds must be made in
  three places together**: `apply_detectors()` in `card_testing_lab.py` (and the notebook),
  `reference_rule_flags()`, and the SPL in `splunk/card_testing_rules.spl` (plus the
  searches quoted in `docs/LAB_GUIDE.md`). Otherwise parity checks fail by design.
- The notebook and `card_testing_lab.py` must produce identical output; keep them in sync.
  After editing `card_testing_lab.py`, `requirements.txt`, or `splunk/card_testing_rules.spl`,
  run `python3 tools/sync_notebook.py` (its `--check` mode fails if the notebook is stale).

## Code style guidelines

- Plain, dependency-light Python: type-hinted function signatures, `pathlib`, f-strings in
  the Splunk scripts, %-free formatting. Scripts are single-purpose CLI tools with a
  `main()` and an `argparse` docstring.
- The Splunk scripts use **only the standard library** (Python 3.8+) so they run anywhere;
  do not add dependencies to them.
- Heavy dependencies (numpy/pandas/sklearn/matplotlib) are confined to `card_testing_lab.py`
  and are pinned in `requirements.txt`. Matplotlib uses the `Agg` backend and a local
  `MPLCONFIGDIR` so it works headless and in Colab.
- Comments explain *why* (caveats, lab-design decisions), not what the code does. Assertions
  double as documentation of invariants. Documentation (README, LAB_GUIDE) is written in
  plain, direct English — keep the same tone when editing docs.
- Currency is always integer minor units (USD cents); "small payment" means ≤100 cents.
- Windows are fixed, UTC-aligned 5-minute buckets everywhere; never introduce sliding or
  timezone-dependent windows.

## Security considerations

- **Synthetic data only.** No real PANs, customer records, or payment-provider calls. Card
  and device IDs are `fake_card_*` / `fake_device_*` strings. Keep it that way.
- The Splunk container is bound to **127.0.0.1 only** and uses the Splunk Free license (no
  authentication); do not expose its ports or reuse the configs for production.
  `app/local/server.conf` lowers `minFreeSpace` to 1000 MB for this isolated lab only.
- `.lab-state/splunk.env` holds the randomly generated bootstrap password (mode 0600) and is
  gitignored. Never commit it or include it in shared output.
- `start_lab.py` accepts the Splunk license and Splunk General Terms on the user's behalf —
  this is called out in the README and compose file; keep those notices intact.
- `verify_lab.py` runs searches from *inside* the container over loopback because Splunk
  Free rejects external management requests; do not relax that restriction to make it
  easier to call.
- The launcher is deliberately idempotent and refuses to mix two different CSVs into one
  index (duplicate events inflate every count). Preserve that guard.
