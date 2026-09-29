#!/usr/bin/env python3
"""Check that Splunk reproduces the Python lab's results, event for event.

    python3 splunk/verify_lab.py                  # print PASS/FAIL per check
    python3 splunk/verify_lab.py --json report.json

Expected values come from the CSV that Splunk actually indexed (read from the
container), so variant CSVs are checked against their own Python flags.
Exits with status 1 if any check fails.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode

RULES_FILE = Path(__file__).resolve().parent / 'card_testing_rules.spl'
RULE_NAMES = ('device_windows', 'checkout_windows', 'parity', 'scenario_coverage')
CONTAINER = 'card-testing-splunk'
INDEXED_CSV = '/opt/splunk/etc/apps/card_testing_lab/data/card_testing_events.csv'
BASELINE_SHA256 = 'b992512c715bf49c9b300cb00d1eb745bf3b191e0f436047480f63cd75556599'
INGESTION_SPL = '''index=card_testing_lab event_source="synthetic_lab"
| eval time_error=abs(_time-tonumber(epoch))
| stats count AS events dc(event_id) AS unique_events max(time_error) AS max_timestamp_error'''
SCENARIO_FIELDS = {'events': None, 'device_flagged': 'device_rule', 'checkout_flagged': 'checkout_rule',
                   'decline_flagged': 'decline_rule', 'model_flagged': 'toy_model', 'model_scored': 'toy_model_scored'}


def search(spl: str) -> list[dict]:
    """Run SPL over All time through the management API on the container's loopback.

    Splunk Free rejects management requests that cross the Docker network, so the
    request is made from inside the container instead of relaxing that restriction.
    """
    form = urlencode({'search': 'search ' + spl, 'output_mode': 'json',
                      'earliest_time': '0', 'latest_time': 'now', 'count': '0'})
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'curl', '-sk', '--max-time', '300',
                             '--data-binary', '@-', 'https://127.0.0.1:8089/services/search/jobs/export'],
                            input=form, capture_output=True, text=True, timeout=330)
    if result.returncode:
        raise SystemExit(f'Search request failed ({result.stderr.strip() or result.returncode}). '
                         'Start the lab with: python3 splunk/start_lab.py')
    rows = []
    for line in filter(str.strip, result.stdout.splitlines()):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            raise SystemExit(f'Unexpected response from Splunk: {result.stdout[:300]}')
        errors = [m['text'] for m in item.get('messages', []) if m.get('type') in ('ERROR', 'FATAL')]
        if errors:
            raise SystemExit('Splunk: ' + '; '.join(errors))
        if 'result' in item and not item.get('preview'):
            rows.append(item['result'])
    return rows


def load_rules() -> dict[str, str]:
    """The four searches in card_testing_rules.spl, without their /* comments */."""
    text = re.sub(r'/\*.*?\*/', '', RULES_FILE.read_text(), flags=re.S)
    queries = [query.strip() for query in text.split('\n\n') if query.strip()]
    if len(queries) != len(RULE_NAMES):
        raise SystemExit(f'Expected {len(RULE_NAMES)} searches in {RULES_FILE.name}, found {len(queries)}')
    return dict(zip(RULE_NAMES, queries))


def expected_from(csv_text: str) -> dict:
    """Counts, windows and per-scenario flags that the Python lab wrote into the CSV."""
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    scenarios: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        counts = scenarios[row['scenario']]
        for field, column in SCENARIO_FIELDS.items():
            counts[field] += 1 if column is None else int(row[column])
    return {
        'events': len(rows),
        'scenarios': scenarios,
        'device_windows': {(r['checkout_id'], r['device_fp'], int(r['bucket_epoch']))
                           for r in rows if r['device_rule'] == '1'},
        'checkout_windows': {(r['checkout_id'], int(r['bucket_epoch'])) for r in rows if r['checkout_rule'] == '1'},
    }


def as_number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run_checks(expected: dict, results: dict) -> list[tuple[str, bool, str]]:
    checks = []
    total = expected['events']
    ingestion = (results['ingestion'] or [{}])[0]
    for field, label in (('events', 'events'), ('unique_events', 'unique event IDs')):
        value = as_number(ingestion.get(field))
        checks.append((f'Ingestion: {label}', value == total, f'{ingestion.get(field)} (expected {total})'))
    error = as_number(ingestion.get('max_timestamp_error'))
    checks.append(('Ingestion: timestamp error', error == 0, f'{ingestion.get("max_timestamp_error")} s (expected 0)'))

    parity = (results['parity'] or [{}])[0]
    for rule in ('device', 'checkout', 'decline'):
        value = parity.get(f'{rule}_mismatches')
        checks.append((f'Parity: {rule} rule mismatches', as_number(value) == 0, f'{value} (expected 0)'))

    device = {(r['checkout_id'], r['device_fp'], int(r['bucket_epoch'])) for r in results['device_windows']}
    checks.append(('D1 device windows', device == expected['device_windows'],
                   f'{len(device)} (expected {len(expected["device_windows"])})'))
    checkout = {(r['checkout_id'], int(r['bucket_epoch'])) for r in results['checkout_windows']}
    checks.append(('D2 checkout windows', checkout == expected['checkout_windows'],
                   f'{len(checkout)} (expected {len(expected["checkout_windows"])})'))

    found = {row['scenario']: row for row in results['scenario_coverage']}
    for scenario, counts in sorted(expected['scenarios'].items()):
        row = found.get(scenario, {})
        ok = all(as_number(row.get(field)) == counts[field] for field in SCENARIO_FIELDS)
        detail = (f'D1 {row.get("device_flagged")}, D2 {row.get("checkout_flagged")}, '
                  f'D0 {row.get("decline_flagged")}, model {row.get("model_flagged")} of {row.get("events")} events')
        checks.append((f'Coverage: {scenario}', ok, detail))
    extra = sorted(set(found) - set(expected['scenarios']))
    checks.append(('Coverage: no unexpected scenarios', not extra, ', '.join(extra) or 'none'))
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--json', type=Path, metavar='PATH', help='also write the raw results and checks as JSON')
    args = parser.parse_args()

    indexed = subprocess.run(['docker', 'exec', '-u', '0', CONTAINER, 'cat', INDEXED_CSV], capture_output=True)
    if indexed.returncode:
        raise SystemExit('No lab data found in Splunk. Start the lab with: python3 splunk/start_lab.py')
    digest = hashlib.sha256(indexed.stdout).hexdigest()
    expected = expected_from(indexed.stdout.decode())
    results = {'ingestion': search(INGESTION_SPL), **{name: search(q) for name, q in load_rules().items()}}
    checks = run_checks(expected, results)

    origin = 'the bundled baseline' if digest == BASELINE_SHA256 else 'a variant; expected values come from this file'
    print(f'Indexed CSV: sha256 {digest[:12]}... ({origin})\n')
    width = max(len(name) for name, _, _ in checks)
    for name, ok, detail in checks:
        print(f'  {"PASS" if ok else "FAIL"}  {name:<{width}}  {detail}')
    failed = sum(not ok for _, ok, _ in checks)
    print(f'\n{len(checks) - failed} of {len(checks)} checks passed.')

    if args.json:
        report = {'verified_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                  'csv_sha256': digest, 'passed': not failed,
                  'checks': [{'check': name, 'passed': ok, 'detail': detail} for name, ok, detail in checks],
                  'results': results}
        args.json.write_text(json.dumps(report, indent=2) + '\n')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
