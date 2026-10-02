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


class TemplateLimits(unittest.TestCase):
    def test_inline_code_fits_the_zipfile_limit(self):
        for logical_id in ("CheckoutFunction", "SimulateFunction"):
            code = inline_code(logical_id)
            self.assertLessEqual(len(code), ZIPFILE_LIMIT, f"{logical_id} inline code is too long")
            self.assertEqual(len(code), len(code.encode()), f"{logical_id} must be ASCII")

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

    def test_provider_outcomes_are_stable_and_both_return_http_200(self):
        # Fixed fingerprints sit immediately either side of the 35% and 96% cutoffs.
        cases = [("ct_card_31", "approved"), ("ct_card_174", "declined"),
                 ("card_149", "approved"), ("card_112", "declined")]
        for card, outcome in cases:
            with self.subTest(card=card):
                for _ in range(2):
                    response, events = self.pay(card)
                    self.assertEqual(response["statusCode"], 200)
                    self.assertEqual(events[0]["outcome"], outcome)
                    reply = json.loads(response["body"])
                    self.assertEqual(reply["outcome"], outcome)
                    self.assertEqual(reply["decline_code"], events[0]["decline_code"])
                    if outcome == "approved":
                        self.assertIsNone(events[0]["decline_code"])
                    else:
                        self.assertIn(events[0]["decline_code"], self.mod.CODES)

    def test_payment_preserves_integer_cent_amounts(self):
        for amount in (0, 100, 101):
            with self.subTest(amount=amount):
                response, events = self.pay("card_amount", amount=amount)
                self.assertEqual(response["statusCode"], 200)
                self.assertEqual(events[0]["amount_minor"], amount)

    def test_device_falls_back_to_the_header_the_waf_rule_keys_on(self):
        body = {"card_fp": "card_h", "amount_minor": 2000}
        _, events = call(self.mod.handler, api_event("/pay", body, {"X-Device-Fp": "dev_header"}))
        self.assertEqual(events[0]["device_fp"], "dev_header")

    def test_rejects_plain_and_space_separated_card_numbers(self):
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
    """Record outgoing requests and return supplied responses or transport errors."""

    class Response(io.BytesIO):
        status = 200

        def __init__(self, payload):
            super().__init__(json.dumps(payload).encode())

    def __init__(self, responses=None):
        self.responses = iter(responses) if responses is not None else None
        self.calls = []  # (url, device header, body)

    def __call__(self, req, timeout=None):
        headers = {k.lower(): v for k, v in req.header_items()}
        body = json.loads(req.data.decode())
        self.calls.append((req.full_url, headers.get("x-device-fp"), body))
        response = next(self.responses) if self.responses is not None else {"outcome": "approved"}
        if isinstance(response, Exception):
            raise response
        return self.Response(response)


class SimulateHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load("SimulateFunction", {"API_URL": API_URL})

    def run_scenario(self, event, fake=None):
        fake = fake or FakeApi()
        with mock.patch.object(urllib.request, "urlopen", fake), mock.patch("time.sleep"):
            result = self.mod.handler(event, None)
        return result, fake

    def test_scenario_requests_have_the_expected_shape_and_use_only_the_stack_api(self):
        cases = [("normal", 60, 60, 1), ("loud", 120, 1, 120), ("spread", 360, 80, 5)]
        for scenario, attempts, devices, max_attempts in cases:
            with self.subTest(scenario=scenario):
                result, fake = self.run_scenario({"scenario": scenario, "seed": 7,
                                                  "url": "https://example.com/not-this"})
                self.assertEqual(result["sent"], attempts)
                self.assertEqual(len(fake.calls), attempts)
                self.assertEqual(result["api"], API_URL)
                self.assertEqual(result["status"], {"200": attempts})
                per_device = Counter(d for _, d, _ in fake.calls)
                self.assertEqual(len(per_device), devices)
                self.assertEqual(max(per_device.values()), max_attempts)
                self.assertEqual(len({b["card_fp"] for _, _, b in fake.calls}), attempts)
                saves = 0
                for url, device, body in fake.calls:
                    self.assertIn(url, (API_URL + "/pay", API_URL + "/save-card"))
                    self.assertEqual(device, body["device_fp"])
                    self.assertEqual(body["currency"], "USD")
                    self.assertEqual(body["card_fp"].startswith("ct_"), scenario != "normal")
                    if url.endswith("/save-card"):
                        saves += 1
                        self.assertNotIn("amount_minor", body)
                    elif scenario == "normal":
                        self.assertGreater(body["amount_minor"], 100)
                    else:
                        self.assertTrue(0 <= body["amount_minor"] <= 100)
                self.assertTrue(0 < saves < attempts, "each scenario must exercise both routes")
                if scenario == "normal":
                    self.assertLessEqual(saves, 20, "normal traffic must not trip the alarm")
                    self.assertTrue(all(d.startswith("shop_dev_") for d in per_device))
                elif scenario == "loud":
                    self.assertEqual(set(per_device), {"ct_loud_dev_1"})
                else:
                    self.assertEqual(set(per_device), {f"ct_spread_dev_{i}" for i in range(1, 81)})

    def test_histogram_preserves_successes_blocks_and_network_errors(self):
        fake = FakeApi([{"outcome": "approved"},
                        urllib.error.HTTPError(API_URL, 403, "Forbidden", {}, None),
                        urllib.error.URLError("connection failed"), {"outcome": "declined"}])
        result, _ = self.run_scenario({"scenario": "loud", "n": 4, "seed": 3}, fake)
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(result["sent"], 4)
        self.assertEqual(result["status"], {"200": 2, "403": 1, "0": 1})
        self.assertEqual(result["outcomes"], {"approved": 1, "declined": 1})

    def test_fresh_fingerprints_per_seed_and_reproducible_for_the_same_seed(self):
        _, a = self.run_scenario({"scenario": "loud", "seed": 11, "n": 8})
        _, b = self.run_scenario({"scenario": "loud", "seed": 11, "n": 8})
        _, c = self.run_scenario({"scenario": "loud", "seed": 12, "n": 8})
        self.assertEqual(a.calls, b.calls)
        self.assertTrue({x[2]["card_fp"] for x in a.calls}.isdisjoint({x[2]["card_fp"] for x in c.calls}))

    def test_request_count_is_capped_for_the_sandbox(self):
        result, fake = self.run_scenario({"scenario": "normal", "n": 5000, "pace": 0})
        self.assertEqual(result["sent"], 600)
        self.assertEqual(len(fake.calls), 600)

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
