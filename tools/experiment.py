"""Feature-set experiments for the intensity model (train/test time split)."""
import csv
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

from calibrate import fit_ridge, apply_model, auc, spearman, rankdata  # noqa: E402

DATA = os.path.join(HERE, "data")


def logit(p, eps=0.012):
    p = max(eps, min(1 - eps, p))
    return math.log(p / (1 - p))


def load():
    rows = list(csv.DictReader(open(os.path.join(DATA, "backtest_features.csv"), encoding="utf-8-sig")))
    out = []
    for r in rows:
        d = {k: float(r[k]) for k in ("canvas", "canvas_high", "canvas_mid", "canvas_low",
                                      "light", "horizon", "purity", "aod", "precip",
                                      "cc_high", "cc_mid", "cc_low", "cc_low_up",
                                      "rh", "blh", "aod_up")}
        d["y"] = int(r["y"])
        d["date"] = r["date"]
        out.append(d)
    return out


def build(rows, name):
    def f(r):
        if name == "A":  # three decks
            return [logit(r["canvas_high"] * 1.6), logit(r["canvas_mid"] * 2.0),
                    logit(r["canvas_low"] * 2.2), math.log(max(r["light"], .004)),
                    logit(r["horizon"]), logit(r["purity"]), r["aod"],
                    1.0 if r["precip"] >= .2 else 0.0]
        if name == "B":  # total canvas + high
            return [logit(r["canvas"]), logit(r["canvas_high"] * 1.6),
                    math.log(max(r["light"], .004)), logit(r["horizon"]),
                    logit(r["purity"]), r["aod"], 1.0 if r["precip"] >= .2 else 0.0]
        if name == "C":  # three decks + raw covers
            return [logit(r["canvas_high"] * 1.6), logit(r["canvas_mid"] * 2.0),
                    logit(r["canvas_low"] * 2.2), math.log(max(r["light"], .004)),
                    logit(r["horizon"]), logit(r["purity"]), r["aod"],
                    r["cc_high"] / 100, r["cc_low_up"] / 100,
                    1.0 if r["precip"] >= .2 else 0.0]
        if name == "D":  # decks + humidity + blh
            return [logit(r["canvas_high"] * 1.6), logit(r["canvas_mid"] * 2.0),
                    logit(r["canvas_low"] * 2.2), math.log(max(r["light"], .004)),
                    logit(r["horizon"]), logit(r["purity"]), r["aod"], r["rh"] / 100,
                    r["blh"] / 1000, 1.0 if r["precip"] >= .2 else 0.0]
        if name == "E":  # interaction: high canvas boosted when air is clean
            return [logit(r["canvas_high"] * 1.6), logit(r["canvas_mid"] * 2.0),
                    logit(r["canvas_low"] * 2.2), math.log(max(r["light"], .004)),
                    logit(r["horizon"]), logit(r["purity"]), r["aod"],
                    logit(r["canvas_high"] * 1.6) * logit(r["purity"]),
                    1.0 if r["precip"] >= .2 else 0.0]
        if name == "F":  # only physical primitives, no aggregation
            return [r["cc_high"] / 100, r["cc_mid"] / 100, r["cc_low"] / 100,
                    r["cc_low_up"] / 100, math.log(max(r["light"], .004)),
                    logit(r["horizon"]), logit(r["purity"]), r["aod"]]
        raise ValueError(name)
    return [f(r) for r in rows]


def evaluate(name, alpha):
    rows = load()
    train = [r for r in rows if r["date"] <= "2023-06-30"]
    test = [r for r in rows if r["date"] > "2023-06-30"]
    xa, xb = build(train, name), build(test, name)
    m = fit_ridge(xa, [r["y"] for r in train], alpha)
    ia = [apply_model(m, x) for x in xa]
    ib = [apply_model(m, x) for x in xb]
    ya = [r["y"] for r in train]
    yb = [r["y"] for r in test]
    return {
        "name": name, "alpha": alpha,
        "auc_tr": round(auc(ia, [1 if y else 0 for y in ya]), 3),
        "auc_te": round(auc(ib, [1 if y else 0 for y in yb]), 3),
        "rho_tr": round(spearman(ia, ya), 3),
        "rho_te": round(spearman(ib, yb), 3),
        "coef": [round(c, 2) for c in m["coef"]],
    }


if __name__ == "__main__":
    for name in "ABCDEF":
        for alpha in (0.3, 1.2, 3.0):
            print(json.dumps(evaluate(name, alpha), ensure_ascii=False))
