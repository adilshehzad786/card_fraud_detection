#!/usr/bin/env python3
"""Defensive, synthetic card-testing detector comparison. No network calls or card data.

Run: python card_testing_lab.py --output-dir lab_output
The notebook embeds this same file and runs it without uploading any other file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path.cwd() / ".mpl_cache"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split

SEED = 61027
FEATURES = ["amount_minor", "hour_utc", "distance_km", "prior_card_attempts_1h"]
DETECTORS = ["toy_model", "device_rule", "checkout_rule", "decline_rule"]
SCENARIOS = ["legitimate_baseline", "legitimate_shared_device", "legitimate_setup_burst", "loud", "spread"]
BASE = int(pd.Timestamp("2026-09-26T10:00:00Z").timestamp())


def classic_data(seed: int) -> pd.DataFrame:
    """Intentionally simplified, overlapping synthetic transaction populations.

    This encodes a classic-fraud assumption; it is not an independent empirical
    benchmark. No trained model chooses how evaluation card-testing data is made.
    """
    rng = np.random.default_rng(seed)
    n, f = 12000, 600
    benign = pd.DataFrame({
        "amount_minor": np.clip(rng.lognormal(7.7, 1.15, n), 10, 300000).round(),
        "hour_utc": rng.choice(np.arange(24), n, p=np.array([1]*6 + [3]*3 + [8]*12 + [3]*3)/120),
        "distance_km": rng.exponential(35, n),
        "prior_card_attempts_1h": rng.poisson(.7, n),
        "label": 0,
    })
    fraud = pd.DataFrame({
        "amount_minor": np.clip(rng.lognormal(10.4, .85, f), 10, 300000).round(),
        "hour_utc": rng.choice(np.arange(24), f, p=np.array([12]*6 + [2]*18)/108),
        "distance_km": rng.exponential(300, f),
        "prior_card_attempts_1h": rng.poisson(3.0, f),
        "label": 1,
    })
    df = pd.concat([benign, fraud], ignore_index=True)
    df.insert(0, "sample_id", [f"classic_{i:06d}" for i in range(len(df))])
    return df


def train_model(seed: int):
    df = classic_data(seed)
    train, holdout = train_test_split(df, test_size=.4, stratify=df.label, random_state=seed)
    valid, test = train_test_split(holdout, test_size=.5, stratify=holdout.label, random_state=seed + 1)
    sets = [set(d.sample_id) for d in (train, valid, test)]
    assert not any(sets[i] & sets[j] for i in range(3) for j in range(i + 1, 3))
    assert len(set.union(*sets)) == len(df)
    assert not ({"label", "scenario", "sample_id", "event_id"} & set(FEATURES))
    model = RandomForestClassifier(n_estimators=180, max_depth=8, min_samples_leaf=6,
                                   random_state=seed, n_jobs=1)
    model.fit(train[FEATURES], train.label)
    valid_scores = model.predict_proba(valid[FEATURES])[:, 1]
    thresholds = np.arange(.05, .951, .01)
    quality = [f1_score(valid.label, valid_scores >= t, zero_division=0) for t in thresholds]
    threshold = float(thresholds[int(np.argmax(quality))])  # ties use lower threshold
    prediction = model.predict_proba(test[FEATURES])[:, 1] >= threshold
    tn, fp, fn, tp = confusion_matrix(test.label, prediction, labels=[0, 1]).ravel()
    metrics = {
        "threshold": round(threshold, 8), "threshold_selection": "maximize validation F1; no test tuning",
        "precision": float(precision_score(test.label, prediction, zero_division=0)),
        "recall": float(recall_score(test.label, prediction, zero_division=0)),
        "false_positive_rate": float(fp / (tn + fp)),
        "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
        "train_rows": len(train), "validation_rows": len(valid), "test_rows": len(test),
        "training_fraud_rows": int(train.label.sum()), "validation_fraud_rows": int(valid.label.sum()),
        "test_fraud_rows": int(test.label.sum()), "features": FEATURES,
        "class_weight": None, "resampling": "none",
    }
    split_manifest = pd.concat([d[["sample_id", "label"]].assign(split=name)
                               for d, name in [(train, "train"), (valid, "validation"), (test, "test")]])
    return model, threshold, metrics, split_manifest.sort_values("sample_id")


def generate_events(seed: int, spread_devices: int = 80) -> pd.DataFrame:
    # prior_card_attempts_1h is invented historical context, not reconstructed
    # from this export; those earlier attempts are omitted. Every exported card is distinct.
    if not 1 <= spread_devices <= 360:
        raise ValueError("spread_devices must be between 1 and 360")
    rng = np.random.default_rng(seed + 100)
    records = []

    def add(offset, scenario, flow, card, device, outcome, amount, distance=10.0, prior=0):
        epoch = BASE + int(offset)
        records.append({"epoch": epoch, "flow": flow, "checkout_id": "shop_demo",
                        "event_source": "synthetic_lab", "outcome": outcome, "currency": "USD",
                        "amount_minor": int(amount), "card_fp": f"fake_card_{card}",
                        "device_fp": f"fake_device_{device}", "distance_km": round(float(distance), 3),
                        "prior_card_attempts_1h": int(prior), "scenario": scenario,
                        "label": int(scenario in ("loud", "spread"))})

    # Ordinary background: payments and sparse card saves; no real account data.
    for i in range(1440):
        flow = "setup_card" if rng.random() < .08 else "payment"
        amount = 0 if flow == "setup_card" else (int(rng.integers(10, 101)) if rng.random() < .08
                                                    else int(np.clip(rng.lognormal(7.7, 1.05), 150, 100000)))
        add(rng.integers(0, 14400), "legitimate_baseline", flow, f"b{i:05d}", f"b{i:05d}",
            "approved" if rng.random() < .96 else "declined", amount, rng.exponential(30), rng.poisson(.5))

    # A genuine shared terminal: many different cards, normal purchase amounts.
    for i in range(24):
        add(8100 + i * 12, "legitimate_shared_device", "payment", f"shared{i:03d}", "shared_terminal",
            "approved", 1800 + i * 100, 5, 0)

    # A genuine card-save burst across individual devices. A deliberate FP stress case.
    for i in range(28):
        add(12600 + i * 10, "legitimate_setup_burst", "setup_card", f"onboard{i:03d}", f"onboard{i:03d}",
            "approved", 0, 10, 0)

    # Loud pattern: 120 attempts, one device, 20 minutes, mostly approvals.
    for i in range(120):
        flow = "setup_card" if i % 3 == 0 else "payment"
        add(1800 + i * 10, "loud", flow, f"loud{i:03d}", "loud", "declined" if i % 10 == 0 else "approved",
            0 if flow == "setup_card" else int(rng.integers(10, 101)), rng.uniform(1, 30), 0)

    # Spread pattern: random arrivals over three hours, one invented card per event.
    offsets = rng.integers(3600, 14400, 360)
    for i, offset in enumerate(offsets):
        flow = "setup_card" if i % 3 == 0 else "payment"
        add(offset, "spread", flow, f"spread{i:03d}", f"spread{i % spread_devices:03d}",
            "declined" if i % 10 == 0 else "approved", 0 if flow == "setup_card" else int(rng.integers(10, 101)),
            rng.uniform(1, 30), 0)

    df = pd.DataFrame(records).sort_values(["epoch", "card_fp"], kind="stable").reset_index(drop=True)
    df.insert(0, "event_id", [f"evt_{i:06d}" for i in range(len(df))])
    df.insert(1, "ts", pd.to_datetime(df.epoch, unit="s", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    df["hour_utc"] = pd.to_datetime(df.epoch, unit="s", utc=True).dt.hour
    df["bucket_epoch"] = (df.epoch // 300) * 300
    assert df.event_id.is_unique and df.card_fp.is_unique
    assert df.epoch.between(BASE, BASE + 14400 - 1).all()
    assert (pd.to_datetime(df.ts, utc=True).astype("int64") // 10**9 == df.epoch).all()
    assert (df.loc[df.flow == "setup_card", "amount_minor"] == 0).all()
    return df


def apply_detectors(df, model, threshold):
    df = df.copy()
    df["toy_model_scored"] = (df.flow == "payment").astype(int)
    df["toy_model_score"] = np.nan
    scored = df.toy_model_scored == 1
    df.loc[scored, "toy_model_score"] = model.predict_proba(df.loc[scored, FEATURES])[:, 1]
    df["toy_model"] = (df.toy_model_score >= threshold).astype(int)
    device_group = df.groupby(["checkout_id", "device_fp", "bucket_epoch"], sort=True)
    df["device_attempts"] = device_group.event_id.transform("size")
    df["device_cards"] = device_group.card_fp.transform("nunique")
    df["device_declines"] = device_group.outcome.transform(lambda s: (s == "declined").sum())
    df["device_rule"] = ((df.device_attempts >= 20) & (df.device_cards >= 15)).astype(int)
    df["decline_rule"] = ((df.device_attempts >= 20) & (df.device_cards >= 15)
                          & (df.device_declines / df.device_attempts >= .8)).astype(int)
    df["candidate"] = ((df.flow == "setup_card") | ((df.flow == "payment") & (df.currency == "USD")
                                                   & (df.amount_minor <= 100))).astype(int)
    selected = df.loc[df.candidate == 1].groupby(["checkout_id", "bucket_epoch"], sort=True)
    counts = selected.agg(checkout_candidates=("event_id", "size"), checkout_cards=("card_fp", "nunique"))
    df = df.join(counts, on=["checkout_id", "bucket_epoch"])
    df[["checkout_candidates", "checkout_cards"]] = df[["checkout_candidates", "checkout_cards"]].fillna(0).astype(int)
    df["checkout_rule"] = ((df.candidate == 1) & (df.checkout_candidates >= 20) & (df.checkout_cards >= 15)).astype(int)
    return df


def reference_rule_flags(df):
    """Independent plain-Python aggregation mirrors SPL; checks pandas implementation."""
    dev, shop = {}, {}
    for r in df.to_dict("records"):
        bucket = int(r["epoch"]) // 300 * 300
        key = (r["checkout_id"], r["device_fp"], bucket)
        dev.setdefault(key, []).append(r)
        candidate = r["flow"] == "setup_card" or (r["flow"] == "payment" and r["currency"] == "USD" and r["amount_minor"] <= 100)
        if candidate:
            shop.setdefault((r["checkout_id"], bucket), []).append(r)
    expected = {k: set() for k in ["device_rule", "checkout_rule", "decline_rule"]}
    for rows in dev.values():
        if len(rows) >= 20 and len({r["card_fp"] for r in rows}) >= 15:
            expected["device_rule"].update(r["event_id"] for r in rows)
            if sum(r["outcome"] == "declined" for r in rows) / len(rows) >= .8:
                expected["decline_rule"].update(r["event_id"] for r in rows)
    for rows in shop.values():
        if len(rows) >= 20 and len({r["card_fp"] for r in rows}) >= 15:
            expected["checkout_rule"].update(r["event_id"] for r in rows)
    for rule, ids in expected.items():
        assert ids == set(df.loc[df[rule] == 1, "event_id"]), f"Independent rule mismatch: {rule}"


def summarize(df):
    rows = []
    for scenario in SCENARIOS:
        subset = df[df.scenario == scenario]
        for detector in DETECTORS:
            flagged = subset[subset[detector] == 1]
            keys = ["checkout_id", "bucket_epoch"] + (["device_fp"] if detector in ("device_rule", "decline_rule") else [])
            rows.append({"scenario": scenario, "detector": detector, "events": len(subset),
                         "flagged_events": len(flagged), "coverage": len(flagged) / len(subset),
                         "flagged_group_intersections": len(flagged[keys].drop_duplicates()),
                         "payment_events": int((subset.flow == "payment").sum()),
                         "setup_events": int((subset.flow == "setup_card").sum()),
                         "flagged_payment_events": int((flagged.flow == "payment").sum()),
                         "flagged_setup_events": int((flagged.flow == "setup_card").sum()),
                         "payment_coverage": int((flagged.flow == "payment").sum()) / int((subset.flow == "payment").sum()) if (subset.flow == "payment").any() else None,
                         "model_unscored_events": int((subset.toy_model_scored == 0).sum()) if detector == "toy_model" else 0})
    return pd.DataFrame(rows)


def draw_chart(path, summary, classic):
    plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": "#F5F4FF"})
    bg, panel, purple, green, red = "#1a1a2e", "#25253e", "#6C5CE7", "#00B894", "#D63031"
    fig = plt.figure(figsize=(10.8, 13.5), dpi=100, facecolor=bg)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 1080); ax.set_ylim(1350, 0); ax.axis("off")
    ax.text(64, 76, "CHECKOUT SECURITY  /  SYNTHETIC LAB", fontsize=14, weight="bold", color="#ABA2FF")
    ax.text(64, 148, "Good fraud scores.\nDifferent attack signal.", fontsize=38, weight="bold", linespacing=1.15, va="top")
    ax.text(64, 295, "A toy transaction model meets card testing.", fontsize=20, color="#D1D0DF")
    ax.add_patch(FancyBboxPatch((64, 340), 952, 180, boxstyle="round,pad=0,rounding_size=20", facecolor=panel, edgecolor="none"))
    ax.text(94, 382, "CLASSIC SYNTHETIC FRAUD · HELD-OUT TEST", fontsize=14, weight="bold", color="#BDB7F2")
    for x, value, caption in [(94, classic["recall"], "recall"), (408, classic["precision"], "precision"), (722, classic["false_positive_rate"], "false-positive rate")]:
        ax.text(x, 448, f"{100*value:.1f}%", fontsize=35, weight="bold", color=green)
        ax.text(x, 488, caption, fontsize=15, color="#D1D0DF")
    ax.text(64, 578, "ATTACK EVENTS COVERED BY EACH DETECTOR", fontsize=16, weight="bold")
    ax.text(592, 628, "Loud", fontsize=20, weight="bold", ha="center")
    ax.text(870, 628, "Spread out", fontsize=20, weight="bold", ha="center")
    ax.text(592, 655, "1 device · 20 min", fontsize=13, color="#B5B3C8", ha="center")
    ax.text(870, 655, "80 devices · 3 h", fontsize=13, color="#B5B3C8", ha="center")
    for idx, (detector, name, desc) in enumerate([
        ("toy_model", "Toy transaction model*", "Amount · hour · distance · card history"),
        ("device_rule", "Per-device velocity", "20+ events / 15+ cards in 5 min"),
        ("checkout_rule", "Checkout-wide rule", "Small payments + card saves in 5 min"),
    ]):
        y = 694 + idx * 113
        ax.add_patch(FancyBboxPatch((64, y), 952, 97, boxstyle="round,pad=0,rounding_size=12", facecolor=panel, edgecolor="none"))
        ax.text(84, y + 37, name, fontsize=17, weight="bold")
        ax.text(84, y + 66, desc, fontsize=12, color="#B5B3C8")
        for x, scenario in [(592, "loud"), (870, "spread")]:
            r = summary[(summary.scenario == scenario) & (summary.detector == detector)].iloc[0]
            ax.text(x, y + 44, f"{r.coverage:.0%}", fontsize=30, weight="bold", ha="center", color=green if r.coverage > .5 else "#FF7979")
            ax.text(x, y + 73, f"{r.flagged_events} / {r.events} events", fontsize=12, color="#B5B3C8", ha="center")
    fps = summary[summary.scenario.str.startswith("legitimate")].groupby("detector").flagged_events.sum()
    benign_total = int(summary[(summary.scenario.str.startswith("legitimate")) & (summary.detector == "device_rule")].events.sum())
    ax.text(64, 1080, "Coverage comes with false positives.", fontsize=23, weight="bold")
    ax.text(64, 1119, f"Of {benign_total:,} legitimate events: device flagged {fps['device_rule']} · checkout {fps['checkout_rule']}", fontsize=17, color="#D1D0DF")
    ax.text(64, 1173, "*Payments only; 160 attack card-save events are outside model scope.\nCoverage is retrospective within fixed UTC bins, not real-time prevention.\nInvented data and scenarios. These are not production performance claims.", fontsize=13, color="#B5B3C8", linespacing=1.65, va="top")
    ax.text(64, 1294, "Measure the behavior your detector can actually see.", fontsize=18, weight="bold", color="#ABA2FF")
    fig.savefig(path, dpi=100, facecolor=bg)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", "--outdir", dest="output_dir", default="lab_output")
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--spread-devices", type=int, default=80)
    args = p.parse_args()
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    model, threshold, classic, splits = train_model(args.seed)
    events = generate_events(args.seed, args.spread_devices)
    assert events.equals(generate_events(args.seed, args.spread_devices)), "Generator is not reproducible"
    events = apply_detectors(events, model, threshold)
    reference_rule_flags(events)
    summary = summarize(events)
    csv = events.to_csv(index=False, float_format="%.10f")
    (out / "card_testing_events.csv").write_text(csv)
    summary.to_csv(out / "card_testing_metrics.csv", index=False, float_format="%.10f")
    splits.to_csv(out / "classic_split_manifest.csv", index=False)
    benign = events.label == 0
    metrics = {"seed": args.seed, "spread_devices": args.spread_devices, "event_count": len(events),
               "event_sha256": hashlib.sha256(csv.encode()).hexdigest(), "classic_test": classic,
               "synthetic_limitations": "Generators encode assumptions. No real fraud benchmark, generalization, production detection, or prevention claims.",
               "coverage_definition": "Flagged events / scenario events. Fixed UTC five-minute bins, assigned retrospectively. Model scores payments only; unscored setups are uncovered.",
               "currency_and_units": "USD integer minor units; small payment means <=100 cents ($1).",
               "benign_false_positives": {d: int(events.loc[benign, d].sum()) for d in DETECTORS},
               "benign_event_count": int(benign.sum()),
               "versions": {"numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__, "matplotlib": matplotlib.__version__},
               "scenarios": json.loads(summary.to_json(orient="records")),
               "self_checks": ["unique invented event IDs and card identifiers", "timestamps round-trip in UTC", "disjoint stratified training/validation/test sample IDs", "no label/scenario/ID in features", "repeat generation identical", "independent rule aggregation agrees"]}
    (out / "card_testing_metrics.json").write_text(json.dumps(metrics, indent=2, allow_nan=False) + "\n")
    # The official chart is for the default 80-device baseline. Avoid mislabeling a variant.
    if args.spread_devices == 80:
        draw_chart(out / "card_testing_chart.png", summary, classic)
    print(json.dumps({k: metrics[k] for k in ["seed", "event_count", "event_sha256", "classic_test", "benign_false_positives"]}, indent=2))
    print(summary[["scenario", "detector", "events", "flagged_events", "coverage"]].to_string(index=False))
    print(f"Saved verified outputs to: {out.resolve()}")


if __name__ == "__main__":
    main()
