"""Unit tests for the two inline Lambda handlers in probe-watch.yaml.

The handlers live inside the template (CloudFormation ZipFile), so the tests pull
them out of the YAML text, check the 4,096-character inline limit, and run them
with the HTTP call stubbed. Standard library only.

    python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import re
import types
import unittest
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from unittest import mock

TEMPLATE = Path(__file__).resolve().parents[1] / "probe-watch.yaml"
ZIPFILE_LIMIT = 4096  # CloudFormation's limit for inline Lambda code
API_URL = "https://abc123.execute-api.us-east-1.amazonaws.com/demo"


def yaml_block(marker, after=""):
    """Return the literal block scalar that follows `marker` in the template, dedented."""
    text = TEMPLATE.read_text()
    start = text.index(after) if after else 0
    block = text.index(marker, start)
    lines = text[block:].split("\n")[1:]
    indent = len(lines[0]) - len(lines[0].lstrip())
    out = []
    for line in lines:
        if not line.strip():
            out.append("")
        elif len(line) - len(line.lstrip()) < indent:
            break
        else:
            out.append(line[indent:])
    return "\n".join(out).rstrip() + "\n"


def inline_code(logical_id):
    """Return the ZipFile block of a function resource, dedented."""
    return yaml_block("ZipFile: |", after=f"\n  {logical_id}:\n")


def mapped_queries():
    """The two Logs Insights queries from the template's Mappings section."""
    text = TEMPLATE.read_text()
    per_device = re.search(r'PerDevice:\n\s+Text: "(.*)"\n', text).group(1)
    checkout_wide = re.search(r"CheckoutWide:\n\s+Text: '(.*)'\n", text).group(1)
    return per_device, checkout_wide


def load(logical_id, env):
    code = inline_code(logical_id)
    module = types.ModuleType(logical_id)
    with mock.patch.dict(os.environ, env, clear=False):
        exec(compile(code, f"{logical_id}.py", "exec"), module.__dict__)
    return module


def api_event(path, body, headers=None):
    return {
        "path": path,
        "resource": path,
        "httpMethod": "POST",
        "headers": headers or {},
        "body": json.dumps(body) if isinstance(body, dict) else body,
        "requestContext": {"requestId": "req-1", "identity": {"sourceIp": "203.0.113.9"}},
    }


def call(handler, event):
    """Invoke a handler and return (response, printed JSON lines)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        response = handler(event, None)
    lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    return response, lines


EVENT_FIELDS = ["ts", "flow", "outcome", "decline_code", "device_fp", "card_fp", "bin",
                "amount_minor", "currency", "request_id", "source_ip"]


def probe_shaped(evt):
    """The metric filter, re-stated in Python: card saves, or payments of <= 100 cents."""
    return evt["flow"] == "save_card" or evt["amount_minor"] <= 100


class TemplateLimits(unittest.TestCase):
    def test_inline_code_fits_the_zipfile_limit(self):
        for logical_id in ("CheckoutFunction", "SimulateFunction"):
            code = inline_code(logical_id)
            self.assertLessEqual(len(code), ZIPFILE_LIMIT, f"{logical_id} inline code is too long")
            self.assertEqual(len(code), len(code.encode()), f"{logical_id} must be ASCII")

    def test_metric_filter_pattern_is_the_documented_one(self):
        text = TEMPLATE.read_text()
        self.assertIn('FilterPattern: \'{ ($.flow = "save_card") || ($.amount_minor <= 100) }\'', text)

    def test_checkout_wide_query_counts_what_the_metric_filter_counts(self):
        per_device, checkout_wide = mapped_queries()
        self.assertIn('(flow = "save_card" or amount_minor <= 100)', checkout_wide)
        self.assertIn("by device_fp, bin(5m)", per_device)
        self.assertIn("by bin(5m)", checkout_wide)

    def test_dashboard_body_is_valid_json_and_repeats_the_saved_queries(self):
        body = re.sub(r"\$\{[^}]+\}", "X", yaml_block("DashboardBody: !Sub |"))
        widgets = json.loads(body)["widgets"]
        self.assertEqual(Counter(w["type"] for w in widgets), {"log": 2, "alarm": 1, "metric": 1})
        for w in widgets:
            self.assertTrue(0 <= w["x"] and w["x"] + w["width"] <= 24, "dashboard grid is 24 wide")
            self.assertEqual(w["properties"].get("region", "X"), "X")
        per_device, checkout_wide = mapped_queries()
        log_queries = [w["properties"]["query"] for w in widgets if w["type"] == "log"]
        self.assertEqual(log_queries, [f"SOURCE 'X' | {per_device}", f"SOURCE 'X' | {checkout_wide}"])
        metric = next(w for w in widgets if w["type"] == "metric")
        self.assertEqual(metric["properties"]["period"], 300)
        self.assertEqual(metric["properties"]["annotations"]["horizontal"][0]["value"], 20)


class CheckoutHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load("CheckoutFunction", {})

    def pay(self, card, amount=1500, device="dev_1", headers=None):
        body = {"card_fp": card, "device_fp": device, "amount_minor": amount,
                "bin": "411111", "currency": "usd"}
        return call(self.mod.handler, api_event("/pay", body, headers))

    def test_payment_returns_200_and_one_event_with_the_documented_schema(self):
        response, events = self.pay("card_a")
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(list(evt), EVENT_FIELDS)
        self.assertEqual(evt["flow"], "pay")
        self.assertEqual(evt["amount_minor"], 1500)
        self.assertEqual(evt["currency"], "USD")
        self.assertEqual(evt["device_fp"], "dev_1")
        self.assertEqual(evt["card_fp"], "card_a")
        self.assertEqual(evt["request_id"], "req-1")
        self.assertEqual(evt["source_ip"], "203.0.113.9")
        self.assertTrue(evt["ts"].endswith("Z"))
        reply = json.loads(response["body"])
        self.assertEqual(reply["outcome"], evt["outcome"])
        self.assertEqual(reply["request_id"], "req-1")

    def test_card_save_is_its_own_flow_with_zero_amount(self):
        body = {"card_fp": "card_b", "device_fp": "dev_2", "bin": "424242"}
        response, events = call(self.mod.handler, api_event("/save-card", body))
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(events[0]["flow"], "save_card")
        self.assertEqual(events[0]["amount_minor"], 0)
        self.assertTrue(probe_shaped(events[0]))

    def test_declines_still_return_http_200(self):
        declined = [self.pay(f"ct_x{i}") for i in range(40)]
        declined = [(r, e) for r, e in declined if e[0]["outcome"] == "declined"]
        self.assertTrue(declined, "expected some test cards to decline")
        for response, events in declined:
            self.assertEqual(response["statusCode"], 200)
            self.assertIn(events[0]["decline_code"], self.mod.CODES)

    def test_approved_events_have_no_decline_code(self):
        _, events = self.pay("card_steady")
        for _ in range(3):
            _, again = self.pay("card_steady")
            self.assertEqual(again[0]["outcome"], events[0]["outcome"], "a card's fate is fixed")
        approved = [e for _, e in (self.pay(f"card_{i}") for i in range(30)) if e[0]["outcome"] == "approved"]
        self.assertTrue(approved)
        self.assertTrue(all(e[0]["decline_code"] is None for e in approved))

    def test_test_cards_approve_far_less_often_than_shopper_cards(self):
        def rate(prefix):
            n = 2000
            ok = sum(self.pay(f"{prefix}{i}")[1][0]["outcome"] == "approved" for i in range(n))
            return ok / n
        self.assertAlmostEqual(rate("ct_card_"), 0.35, delta=0.04)
        self.assertGreaterEqual(rate("card_"), 0.93)

    def test_small_payment_is_probe_shaped_and_a_basket_is_not(self):
        _, small = self.pay("card_s", amount=100)
        _, basket = self.pay("card_t", amount=101)
        self.assertTrue(probe_shaped(small[0]))
        self.assertFalse(probe_shaped(basket[0]))

    def test_device_falls_back_to_the_header_the_waf_rule_keys_on(self):
        body = {"card_fp": "card_h", "amount_minor": 2000}
        _, events = call(self.mod.handler, api_event("/pay", body, {"X-Device-Fp": "dev_header"}))
        self.assertEqual(events[0]["device_fp"], "dev_header")

    def test_rejects_anything_that_looks_like_a_card_number(self):
        for pan in ("4111111111111111", "4111 1111 1111 1111", "371449635398431"):
            response, events = self.pay(pan)
            self.assertEqual(response["statusCode"], 400, pan)
            self.assertEqual(events, [], "no event may be logged for a rejected request")
            self.assertIn("fingerprint", json.loads(response["body"])["error"])

    def test_rejects_bad_input_without_logging(self):
        bad = [
            api_event("/pay", "not json"),
            api_event("/pay", {"device_fp": "dev"}),
            api_event("/pay", {"card_fp": "card_n", "amount_minor": -1}),
            api_event("/pay", {"card_fp": "card_n", "amount_minor": "1.00"}),
            api_event("/pay", {"card_fp": "card_n", "amount_minor": True}),
            api_event("/pay", {"card_fp": "card_n"}),
            api_event("/pay", "[1, 2]"),
        ]
        for event in bad:
            response, events = call(self.mod.handler, event)
            self.assertEqual(response["statusCode"], 400, event["body"])
            self.assertEqual(events, [])


class FakeApi:
    """Stands in for urllib.request.urlopen. Optionally blocks a device after a limit."""

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = json.dumps(payload).encode()

        def read(self):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def __init__(self, block_after=None):
        self.block_after = block_after
        self.calls = []  # (url, device header, body)
        self.per_device = Counter()

    def __call__(self, req, timeout=None):
        headers = {k.lower(): v for k, v in req.header_items()}
        body = json.loads(req.data.decode())
        self.calls.append((req.full_url, headers.get("x-device-fp"), body))
        self.per_device[headers.get("x-device-fp")] += 1
        if self.block_after is not None and self.per_device[headers.get("x-device-fp")] > self.block_after:
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"blocked"))
        outcome = "approved" if len(self.calls) % 3 else "declined"
        return self.Response({"outcome": outcome, "decline_code": None, "request_id": "r"})


class SimulateHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load("SimulateFunction", {"API_URL": API_URL})

    def run_scenario(self, event, fake=None):
        fake = fake or FakeApi()
        with mock.patch.object(urllib.request, "urlopen", fake), mock.patch("time.sleep"):
            result = self.mod.handler(event, None)
        return result, fake

    @staticmethod
    def shaped(url, body):
        return url.endswith("/save-card") or body["amount_minor"] <= 100

    def test_scenario_sizes_match_the_runbook(self):
        self.assertEqual({k: v[0] for k, v in self.mod.PLAN.items()}, {"normal": 60, "loud": 120, "spread": 360})

    def test_every_request_goes_to_the_stacks_own_api_with_the_device_header(self):
        result, fake = self.run_scenario({"scenario": "normal", "seed": 1, "url": "https://example.com/not-this"})
        self.assertEqual(result["sent"], 60)
        for url, device, body in fake.calls:
            self.assertTrue(url.startswith(API_URL + "/"), url)
            self.assertIn(url[len(API_URL):], ("/pay", "/save-card"))
            self.assertEqual(device, body["device_fp"], "WAF keys on the header; it must match the body")
            self.assertEqual(body["currency"], "USD")
            self.assertNotIn("amount_minor", body) if url.endswith("/save-card") else self.assertGreater(body["amount_minor"], 100)
        self.assertEqual(result["api"], API_URL)

    def test_normal_is_sixty_shoppers_and_stays_under_the_alarm_threshold(self):
        for seed in range(1, 31):
            result, fake = self.run_scenario({"scenario": "normal", "seed": seed})
            devices = {d for _, d, _ in fake.calls}
            cards = {b["card_fp"] for _, _, b in fake.calls}
            self.assertEqual((len(devices), len(cards)), (60, 60))
            self.assertFalse(any(d.startswith("ct_") for d in devices))
            shaped = sum(self.shaped(u, b) for u, _, b in fake.calls)
            self.assertLessEqual(shaped, 20, f"seed {seed}: normal traffic must not trip the alarm")
            self.assertEqual(result["status"], {"200": 60})

    def test_loud_is_one_device_fresh_cards_every_attempt_probe_shaped(self):
        result, fake = self.run_scenario({"scenario": "loud", "seed": 7})
        self.assertEqual(result["sent"], 120)
        self.assertEqual({d for _, d, _ in fake.calls}, {"ct_loud_dev_1"})
        cards = [b["card_fp"] for _, _, b in fake.calls]
        self.assertEqual(len(set(cards)), 120)
        self.assertTrue(all(c.startswith("ct_") for c in cards))
        self.assertTrue(all(self.shaped(u, b) for u, _, b in fake.calls))
        # The per-device rule (>= 20 attempts and >= 15 cards in a window) has what it needs.
        self.assertGreaterEqual(len(cards), 20)
        self.assertGreaterEqual(len(set(cards)), 15)
        self.assertEqual(sum(result["outcomes"].values()), 120)

    def test_spread_uses_eighty_devices_and_never_crosses_the_per_device_threshold(self):
        result, fake = self.run_scenario({"scenario": "spread", "seed": 7})
        self.assertEqual(result["sent"], 360)
        self.assertEqual(len(fake.per_device), 80)
        self.assertEqual(max(fake.per_device.values()), 5)
        self.assertTrue(all(d.startswith("ct_spread_dev_") for d in fake.per_device))
        self.assertEqual(len({b["card_fp"] for _, _, b in fake.calls}), 360)
        self.assertTrue(all(self.shaped(u, b) for u, _, b in fake.calls))

    def test_histogram_counts_waf_blocks_for_loud_but_not_spread(self):
        loud, _ = self.run_scenario({"scenario": "loud", "seed": 3}, FakeApi(block_after=20))
        self.assertEqual(loud["status"], {"200": 20, "403": 100})
        spread, _ = self.run_scenario({"scenario": "spread", "seed": 3}, FakeApi(block_after=20))
        self.assertEqual(spread["status"], {"200": 360})

    def test_fresh_fingerprints_per_seed_and_reproducible_for_the_same_seed(self):
        _, a = self.run_scenario({"scenario": "loud", "seed": 11})
        _, b = self.run_scenario({"scenario": "loud", "seed": 11})
        _, c = self.run_scenario({"scenario": "loud", "seed": 12})
        self.assertEqual([x[2]["card_fp"] for x in a.calls], [x[2]["card_fp"] for x in b.calls])
        self.assertTrue({x[2]["card_fp"] for x in a.calls}.isdisjoint({x[2]["card_fp"] for x in c.calls}))

    def test_request_count_is_capped_for_the_sandbox(self):
        result, _ = self.run_scenario({"scenario": "normal", "n": 5000, "pace": 0})
        self.assertEqual(result["sent"], 600)

    def test_unknown_scenario_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_scenario({"scenario": "ddos"})

    def test_refuses_to_run_against_anything_but_api_gateway(self):
        for url in ("https://example.com", "https://shop.example.com/checkout",
                    "https://abc.execute-api.us-east-1.amazonaws.com.evil.example"):
            mod = load("SimulateFunction", {"API_URL": url})
            fake = FakeApi()
            with mock.patch.object(urllib.request, "urlopen", fake), mock.patch("time.sleep"):
                with self.assertRaises(RuntimeError, msg=url):
                    mod.handler({"scenario": "normal"}, None)
            self.assertEqual(fake.calls, [], url)


if __name__ == "__main__":
    unittest.main()
