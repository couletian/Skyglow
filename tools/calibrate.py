"""Fit the calibration layer and produce the validation report.

Target: sunsetbot 再分析记录 (Shanghai, 2022-06-24 .. 2023-12-30), 6 ordinal grades.
Model: ridge regression on 6 physical sub-scores -> latent intensity,
       cumulative logistic calibration -> probability & grade thresholds.
Split: time-based (train 2022-06-24..2023-06-30, test 2023-07-01..2023-12-30).
"""
import csv
import json
import math
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core.model import GRADES  # noqa: E402
from core.model import DEFAULT_MODEL, _raw_features, load_model  # noqa: E402,F401

DATA = os.path.join(HERE, "data")
ALPHA = 1.2  # ridge strength


def load_rows():
    with open(os.path.join(DATA, "backtest_features.csv"), encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        if int(r["y"]) < 0:
            continue
        f = {"canvas": float(r["canvas"]), "light": float(r["light"]),
             "horizon": float(r["horizon"]), "purity": float(r["purity"]),
             "aod": float(r["aod"]), "precip": float(r["precip"]),
             "canvas_high": float(r["canvas_high"]), "canvas_mid": float(r["canvas_mid"]),
             "canvas_low": float(r["canvas_low"])}
        out.append({"date": r["date"], "event": r["event"], "y": int(r["y"]),
                    "x": _raw_features(f), "raw": f, "pred_sb": int(r["sunsetbot"]),
                    "cases": r.get("cases", "-"), "phenomena": r.get("phenomena", "-"),
                    "cc_mid": r["cc_mid"], "cc_high": r["cc_high"],
                    "cc_low": r["cc_low"], "aod": r["aod"], "precip_raw": r["precip"]})
    return out


def solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            continue
        m[c], m[p] = m[p], m[c]
        pv = m[c][c]
        for j in range(c, n + 1):
            m[c][j] /= pv
        for r in range(n):
            if r == c:
                continue
            fac = m[r][c]
            if fac == 0:
                continue
            for j in range(c, n + 1):
                m[r][j] -= fac * m[c][j]
    return [m[i][n] for i in range(n)]


def fit_ridge(xs, ys, alpha=ALPHA):
    n, p = len(xs), len(xs[0])
    mu = [sum(x[j] for x in xs) / n for j in range(p)]
    sd = [math.sqrt(sum((x[j] - mu[j]) ** 2 for x in xs) / n) or 1.0 for j in range(p)]
    z = [[(x[j] - mu[j]) / sd[j] for j in range(p)] for x in xs]
    ym = sum(ys) / n
    a = [[sum(z[i][j] * z[i][k] for i in range(n)) + (alpha if j == k else 0.0)
          for k in range(p)] for j in range(p)]
    b = [sum(z[i][j] * (ys[i] - ym) for i in range(n)) for j in range(p)]
    coef = solve(a, b)
    intercept = ym
    return {"mean": mu, "std": sd, "coef": coef, "intercept": intercept}


def apply_model(m, x):
    z = m["intercept"]
    for xi, mu, sd, b in zip(x, m["mean"], m["std"], m["coef"]):
        z += b * (xi - mu) / (sd or 1.0)
    return z


def fit_logistic_1d(idx, labels, iters=60):
    """P(y=1) = sigmoid(a*(idx-b)), Newton-Raphson on (a,b)."""
    n = len(idx)
    pos = [i for i, y in enumerate(labels) if y]
    b = sum(idx[i] for i in pos) / max(1, len(pos)) if pos else 0.0
    a = 1.0
    for _ in range(iters):
        g0 = g1 = 0.0
        h00 = h01 = h11 = 0.0
        for x, y in zip(idx, labels):
            z = a * (x - b)
            p = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
            w = p * (1 - p)
            d = p - y
            g0 += d * (x - b)
            g1 += -d * a
            h00 += w * (x - b) ** 2
            h01 += -w * a * (x - b)
            h11 += w * a * a
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-9:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (-h01 * g0 + h00 * g1) / det
        a -= 0.6 * da
        b -= 0.6 * db
        a = max(0.05, min(8.0, a))
    return a, b


def auc(scores, labels):
    pairs = sorted(zip(scores, labels))
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        r = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[k] = r
        i = j + 1
    pos = [ranks[k] for k in range(len(pairs)) if pairs[k][1]]
    neg = [ranks[k] for k in range(len(pairs)) if not pairs[k][1]]
    if not pos or not neg:
        return 0.5
    return (sum(pos) / len(pos) - (len(pos) + 1) / 2.0) / len(neg)


def rankdata(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    ranks = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def spearman(a, b):
    ra, rb = rankdata(a), rankdata(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((x - mb) ** 2 for x in rb))
    return num / (da * db) if da and db else 0.0


def metrics(pred_idx, probs, labels):
    glow = [1 if y >= 1 else 0 for y in labels]
    n = len(labels)
    brier = sum((p - g) ** 2 for p, g in zip(probs, glow)) / n
    exact = sum(1 for i in range(n) if labels[i] == pred_idx[i]) / n
    adj = sum(1 for i in range(n) if abs(labels[i] - pred_idx[i]) <= 1) / n
    tp = sum(1 for i in range(n) if probs[i] >= 0.5 and glow[i])
    fp = sum(1 for i in range(n) if probs[i] >= 0.5 and not glow[i])
    fn = sum(1 for i in range(n) if probs[i] < 0.5 and glow[i])
    tn = n - tp - fp - fn
    tpr = tp / (tp + fn) if tp + fn else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tpr
    # best achievable TSS (threshold sweep) — informative under class imbalance
    best_tss, best_thr, best_tp, best_fp = -1.0, 0.5, 0, 0
    for thr in [i / 200 for i in range(10, 191)]:
        t2 = sum(1 for i in range(n) if probs[i] >= thr and glow[i])
        f2 = sum(1 for i in range(n) if probs[i] >= thr and not glow[i])
        r2 = t2 / (t2 + fn) if t2 + fn else 0.0
        q2 = f2 / (f2 + tn) if f2 + tn else 0.0
        if r2 - q2 > best_tss:
            best_tss, best_thr, best_tp, best_fp = r2 - q2, thr, t2, f2
    return {
        "n": n, "auc": round(auc(probs, glow), 4),
        "brier": round(brier, 4),
        "accuracy": round(exact, 4), "adjacent": round(adj, 4),
        "spearman": round(spearman(pred_idx, labels), 4),
        "tss": round(tpr - fpr, 4), "tss_best": round(best_tss, 4),
        "tss_thr": best_thr,
        "recall_best": round(best_tp / (best_tp + fn), 4) if best_tp + fn else 0.0,
        "precision_best": round(best_tp / (best_tp + best_fp), 4) if best_tp + best_fp else 0.0,
        "precision": round(prec, 4), "recall": round(rec, 4),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "mean_pred": round(sum(probs) / n, 4),
        "obs_rate": round(sum(glow) / n, 4),
    }


def confusion(pred_idx, labels):
    m = [[0] * 6 for _ in range(6)]
    for p, y in zip(pred_idx, labels):
        m[y][p] += 1
    return m


def fit_layer(rows, alpha=ALPHA):
    """Ridge intensity + logistic probability + quantile grade thresholds."""
    m = fit_ridge([r["x"] for r in rows], [r["y"] for r in rows], alpha)
    for r in rows:
        r["idx"] = apply_model(m, r["x"])
    glow = [1 if r["y"] >= 1 else 0 for r in rows]
    a, b = fit_logistic_1d([r["idx"] for r in rows], glow)
    for r in rows:
        r["p"] = 1.0 / (1.0 + math.exp(-max(-30, min(30, a * (r["idx"] - b)))))
    order = sorted(r["idx"] for r in rows)
    n = len(order)
    thr = []
    for k in range(1, 6):
        share = sum(1 for r in rows if r["y"] >= k) / n
        q = min(0.995, max(0.5, 1.0 - share))
        thr.append(round(order[min(n - 1, int(q * n))], 4))
    for i in range(1, 5):
        if thr[i] <= thr[i - 1]:
            thr[i] = round(thr[i - 1] + 0.03, 4)
    for r in rows:
        r["grade"] = sum(1 for t in thr if r["idx"] >= t)
    return m, a, b, thr


def main():
    rows = load_rows()
    rows.sort(key=lambda r: r["date"])
    train = [dict(r) for r in rows if r["date"] <= "2023-06-30"]
    test = [dict(r) for r in rows if r["date"] > "2023-06-30"]
    print("rows", len(rows), "train", len(train), "test", len(test))

    m_tr, a_tr, b_tr, thr_tr = fit_layer(train)
    for r in test:
        r["idx"] = apply_model(m_tr, r["x"])
        r["p"] = 1.0 / (1.0 + math.exp(-max(-30, min(30, a_tr * (r["idx"] - b_tr)))))
        r["grade"] = sum(1 for t in thr_tr if r["idx"] >= t)

    # deployed model uses all available labels
    allrows = [dict(r) for r in rows]
    m_all, a_all, b_all, thr_all = fit_layer(allrows)

    model = dict(DEFAULT_MODEL)
    model.update({
        "version": "ridge-v2.1",
        "mean": [round(x, 6) for x in m_all["mean"]],
        "std": [round(x, 6) for x in m_all["std"]],
        "coef": [round(x, 6) for x in m_all["coef"]],
        "intercept": round(m_all["intercept"], 6),
        "prob_a": round(a_all, 6), "prob_b": round(b_all, 6),
        "thresholds": thr_all,
        "prob_cuts": [0.08, 0.16, 0.26, 0.38, 0.52],
        "metrics": {},
    })

    # 5-fold CV grouped by date: assessment of the deployed (all-data) calibration
    import random
    dates = sorted(set(r["date"] for r in rows))
    random.Random(7).shuffle(dates)
    fold_of = {d: i % 5 for i, d in enumerate(dates)}
    cv_p, cv_g, cv_y = [], [], []
    for k in range(5):
        tr_k = [dict(r) for r in rows if fold_of[r["date"]] != k]
        te_k = [dict(r) for r in rows if fold_of[r["date"]] == k]
        if not te_k:
            continue
        m_k, a_k, b_k, t_k = fit_layer(tr_k)
        for r in te_k:
            idx = apply_model(m_k, r["x"])
            r["p"] = 1.0 / (1.0 + math.exp(-max(-30, min(30, a_k * (idx - b_k)))))
            r["grade"] = sum(1 for t in t_k if idx >= t)
            cv_p.append(r["p"]); cv_g.append(r["grade"]); cv_y.append(r["y"])
    cv = metrics(cv_g, cv_p, cv_y)
    cv_bins = [[] for _ in range(10)]
    for p_, y_ in zip(cv_p, cv_y):
        cv_bins[min(9, int(p_ * 10))].append((p_, y_))
    cv_rel = [{"bin": i / 10, "n": len(b),
               "pred": round(sum(x[0] for x in b) / len(b), 3),
               "obs": round(sum(1 for x in b if x[1] >= 1) / len(b), 3)}
              for i, b in enumerate(cv_bins) if b]

    mt = metrics([r["grade"] for r in train], [r["p"] for r in train], [r["y"] for r in train])
    ms = metrics([r["grade"] for r in test], [r["p"] for r in test], [r["y"] for r in test])
    mt["idx_spearman"] = round(spearman([r["idx"] for r in train], [r["y"] for r in train]), 4)
    ms["idx_spearman"] = round(spearman([r["idx"] for r in test], [r["y"] for r in test]), 4)

    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "dataset": {"rows": len(rows), "dates": len(set(r["date"] for r in rows)),
                       "period": [rows[0]["date"], rows[-1]["date"]],
                       "location": "上海 31.23N 121.47E",
                       "labels": "sunsetbot.top 再分析记录（人工观测定级）",
                       "base_rate": round(sum(1 for r in rows if r["y"] >= 1) / len(rows), 4)},
           "split": {"train": [train[0]["date"], train[-1]["date"], len(train)],
                     "test": [test[0]["date"], test[-1]["date"], len(test)]},
           "metrics": {"train": mt, "test": ms, "cv": cv},
           "confusion": confusion([r["grade"] for r in test], [r["y"] for r in test]),
           "grades": GRADES,
           "coefficients": dict(zip(model["features"], [round(c, 3) for c in model["coef"]])),
           "intercept": model["intercept"], "thresholds": thr_all,
           "prob": {"a": model["prob_a"], "b": model["prob_b"]}}

    bins = [[] for _ in range(10)]
    for r in test:
        bins[min(9, int(r["p"] * 10))].append(r)
    out["cv_reliability"] = cv_rel
    out["reliability"] = [{
        "bin": i / 10, "n": len(b), "pred": round(sum(x["p"] for x in b) / len(b), 3) if b else None,
        "obs": round(sum(1 for x in b if x["y"] >= 1) / len(b), 3) if b else None,
    } for i, b in enumerate(bins) if b]

    out["scatter"] = [{"x": round(r["idx"], 2), "y": r["y"], "p": round(r["p"], 3),
                       "d": r["date"], "e": r["event"]}
                      for r in test if abs(r["p"] - 0.5) < 0.48 or r["y"] >= 2]

    sb = [r for r in test if r["pred_sb"] >= 0]
    out["benchmark"] = {
        "source": "sunsetbot 当期预报（记录表 predicted_quality 列）",
        "rows": len(sb),
        "metrics": metrics([r["pred_sb"] for r in sb],
                           [1.0 if r["pred_sb"] >= 1 else 0.0 for r in sb],
                           [r["y"] for r in sb]),
    }

    def pack(r):
        return {"date": r["date"], "event": r["event"], "actual": GRADES[r["y"]],
                "predicted": GRADES[r["grade"]], "p": round(r["p"], 3),
                "cases": r["cases"], "phenomena": r["phenomena"],
                "cloud": "低%.0f/中%.0f/高%.0f" % (float(r["cc_low"]), float(r["cc_mid"]),
                                                  float(r["cc_high"])),
                "aod": r["aod"]}

    out["cases"] = {
        "hits": [pack(r) for r in test if r["y"] >= 3 and r["grade"] >= 3][:10],
        "misses": [pack(r) for r in test if r["y"] >= 3 and r["p"] < 0.35][:8],
        "false_alarms": [pack(r) for r in test if r["y"] == 0 and r["p"] >= 0.60][:8],
    }
    out["distribution"] = {
        "actual": [sum(1 for r in rows if r["y"] == k) for k in range(6)],
        "predicted_test": [sum(1 for r in test if r["grade"] == k) for k in range(6)],
    }

    with open(os.path.join(DATA, "model.json"), "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=1)
    with open(os.path.join(DATA, "validation.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(json.dumps({"train": mt, "test": ms, "bench": out["benchmark"]["metrics"],
                      "thresholds": thr_all, "dist": out["distribution"]},
                     ensure_ascii=False, indent=1))
    print("coef", out["coefficients"])


if __name__ == "__main__":
    main()
