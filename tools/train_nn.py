"""Train a small MLP on the sunsetbot reanalysis labels.

Inputs are raw meteorological variables (not the physics sub-scores), so the
network is an independent, purely data-driven counterpart to the physical model.
Training: full-batch Adam with L2, early stopping on a time-ordered holdout.
"""
import csv
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")

FEATURES = ["cc_low", "cc_mid", "cc_high", "cc_low_up", "cc_mid_up",
            "rh", "blh", "sun_elev",
            "canvas", "canvas_high", "light", "horizon", "purity", "aod", "precip"]


def load():
    rows = list(csv.DictReader(open(os.path.join(DATA, "backtest_features.csv"), encoding="utf-8-sig")))
    X, y, dates = [], [], []
    for r in rows:
        X.append([float(r[k]) for k in FEATURES])
        y.append(1.0 if int(r["y"]) >= 1 else 0.0)
        dates.append(r["date"])
    return np.array(X, dtype=np.float64), np.array(y, dtype=np.float64), dates


def auc(score, label):
    order = np.argsort(score)
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    pos = ranks[label == 1]
    neg = ranks[label == 0]
    return (pos.mean() - (len(pos) + 1) / 2) / len(neg)


def metrics(p, y):
    pred = (p >= 0.5).astype(float)
    tp = float(((pred == 1) & (y == 1)).sum())
    fp = float(((pred == 1) & (y == 0)).sum())
    fn = float(((pred == 0) & (y == 1)).sum())
    tn = float(((pred == 0) & (y == 0)).sum())
    return {
        "n": int(len(y)), "auc": round(float(auc(p, y)), 4),
        "brier": round(float(((p - y) ** 2).mean()), 4),
        "accuracy": round(float((pred == y).mean()), 4),
        "tss": round(tp / (tp + fn) - fp / (fp + tn), 4),
        "precision": round(tp / (tp + fp), 4) if tp + fp else 0.0,
        "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
        "mean_pred": round(float(p.mean()), 4), "obs_rate": round(float(y.mean()), 4),
    }


def main():
    X, y, dates = load()
    dates = np.array(dates)
    tr = dates <= "2023-06-30"
    te = dates > "2023-06-30"
    Xtr_all, ytr_all = X[tr], y[tr]
    # last 15% of the training period is the early-stopping holdout
    cut = int(len(Xtr_all) * 0.85)
    Xtr, ytr = Xtr_all[:cut], ytr_all[:cut]
    Xva, yva = Xtr_all[cut:], ytr_all[cut:]

    mu = Xtr.mean(axis=0)
    sd = Xtr.std(axis=0)
    sd[sd == 0] = 1.0
    Z = (Xtr - mu) / sd
    Zv = (Xva - mu) / sd
    Zt = (X[te] - mu) / sd

    rng = np.random.default_rng(7)
    sizes = [len(FEATURES), 8, 4, 1]
    W = [rng.normal(0, math.sqrt(2 / sizes[i]), (sizes[i], sizes[i + 1])) for i in range(3)]
    B = [np.zeros(sizes[i + 1]) for i in range(3)]
    mW = [np.zeros_like(w) for w in W]; vW = [np.zeros_like(w) for w in W]
    mB = [np.zeros_like(b) for b in B]; vB = [np.zeros_like(b) for b in B]
    lr, l2, beta1, beta2, eps = 0.02, 1e-2, 0.9, 0.999, 1e-8

    def forward(z):
        h1 = np.tanh(z @ W[0] + B[0])
        h2 = np.tanh(h1 @ W[1] + B[1])
        logit = (h2 @ W[2] + B[2]).ravel()
        return h1, h2, logit

    def loss_of(z, t):
        _, _, lg = forward(z)
        p = 1 / (1 + np.exp(-np.clip(lg, -30, 30)))
        bce = -np.mean(t * np.log(p + 1e-9) + (1 - t) * np.log(1 - p + 1e-9))
        reg = sum((w ** 2).sum() for w in W) * l2
        return bce + reg, p

    best = (1e9, None)
    for epoch in range(801):
        h1, h2, logit = forward(Z)
        p = 1 / (1 + np.exp(-np.clip(logit, -30, 30)))
        dz = ((p - ytr) / len(ytr))[:, None]
        gW2 = h2.T @ dz + 2 * l2 * W[2]
        gB2 = dz.sum(axis=0)
        dh2 = (dz @ W[2].T) * (1 - h2 ** 2)
        gW1 = h1.T @ dh2 + 2 * l2 * W[1]
        gB1 = dh2.sum(axis=0)
        dh1 = (dh2 @ W[1].T) * (1 - h1 ** 2)
        gW0 = Z.T @ dh1 + 2 * l2 * W[0]
        gB0 = dh1.sum(axis=0)
        gradsW = [gW0, gW1, gW2]; gradsB = [gB0, gB1, gB2]
        for i in range(3):
            mW[i] = beta1 * mW[i] + (1 - beta1) * gradsW[i]
            vW[i] = beta2 * vW[i] + (1 - beta2) * gradsW[i] ** 2
            mB[i] = beta1 * mB[i] + (1 - beta1) * gradsB[i]
            vB[i] = beta2 * vB[i] + (1 - beta2) * gradsB[i] ** 2
            mhat = mW[i] / (1 - beta1 ** (epoch + 1)); vhat = vW[i] / (1 - beta2 ** (epoch + 1))
            W[i] -= lr * mhat / (np.sqrt(vhat) + eps)
            mhat = mB[i] / (1 - beta1 ** (epoch + 1)); vhat = vB[i] / (1 - beta2 ** (epoch + 1))
            B[i] -= lr * mhat / (np.sqrt(vhat) + eps)
        if epoch % 25 == 0:
            vl, _ = loss_of(Zv, yva)
            if vl < best[0]:
                best = (vl, ([w.copy() for w in W], [b.copy() for b in B]))

    def pred(z):
        h1 = np.tanh(z @ W[0] + B[0])
        h2 = np.tanh(h1 @ W[1] + B[1])
        lg = (h2 @ W[2] + B[2]).ravel()
        return 1 / (1 + np.exp(-np.clip(lg, -30, 30)))

    mtr = metrics(pred(Z), ytr)
    mva = metrics(pred(Zv), yva)
    mte = metrics(pred(Zt), y[te])
    print("train", mtr)
    print("val  ", mva)
    print("test ", mte)

    # deployed weights: same architecture/hyper-parameters, all labels, no early stop
    Xa = (X - X.mean(axis=0)) / np.where(X.std(axis=0) == 0, 1, X.std(axis=0))
    mu_a, sd_a = X.mean(axis=0), X.std(axis=0)
    sd_a[sd_a == 0] = 1.0
    rng2 = np.random.default_rng(7)
    W2 = [rng2.normal(0, math.sqrt(2 / sizes[i]), (sizes[i], sizes[i + 1])) for i in range(3)]
    B2 = [np.zeros(sizes[i + 1]) for i in range(3)]
    mW2 = [np.zeros_like(w) for w in W2]; vW2 = [np.zeros_like(w) for w in W2]
    mB2 = [np.zeros_like(b) for b in B2]; vB2 = [np.zeros_like(b) for b in B2]
    for epoch in range(801):
        h1 = np.tanh(Xa @ W2[0] + B2[0])
        h2 = np.tanh(h1 @ W2[1] + B2[1])
        lg = (h2 @ W2[2] + B2[2]).ravel()
        pr = 1 / (1 + np.exp(-np.clip(lg, -30, 30)))
        dz = ((pr - y) / len(y))[:, None]
        g2 = h2.T @ dz + 2 * l2 * W2[2]; gb2 = dz.sum(axis=0)
        dh2 = (dz @ W2[2].T) * (1 - h2 ** 2)
        g1 = h1.T @ dh2 + 2 * l2 * W2[1]; gb1 = dh2.sum(axis=0)
        dh1 = (dh2 @ W2[1].T) * (1 - h1 ** 2)
        g0 = Xa.T @ dh1 + 2 * l2 * W2[0]; gb0 = dh1.sum(axis=0)
        for i, (gw, gb) in enumerate([(g0, gb0), (g1, gb1), (g2, gb2)]):
            mW2[i] = 0.9 * mW2[i] + 0.1 * gw; vW2[i] = 0.999 * vW2[i] + 0.001 * gw ** 2
            mB2[i] = 0.9 * mB2[i] + 0.1 * gb; vB2[i] = 0.999 * vB2[i] + 0.001 * gb ** 2
            W2[i] -= lr * (mW2[i] / (1 - 0.9 ** (epoch + 1))) / (np.sqrt(vW2[i] / (1 - 0.999 ** (epoch + 1))) + eps)
            B2[i] -= lr * (mB2[i] / (1 - 0.9 ** (epoch + 1))) / (np.sqrt(vB2[i] / (1 - 0.999 ** (epoch + 1))) + eps)
    W, B, mu, sd = W2, B2, mu_a, sd_a

    out = {
        "version": "mlp-15-8-4-1",
        "notes": "raw meteorological variables + physics sub-scores; trained on all 1109 labels; metrics below are from the time split",
        "features": FEATURES,
        "mean": mu.tolist(), "std": sd.tolist(),
        "W": [w.tolist() for w in W], "B": [b.tolist() for b in B],
        "metrics": {"train": mtr, "val": mva, "test": mte},
        "trained_on": "sunsetbot 再分析全量 2022-06-24..2023-12-30（1109 条）",
    }
    with open(os.path.join(DATA, "nn_model.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(os.path.join(DATA, "nn_metrics.json"), "w", encoding="utf-8") as f:
        json.dump({"version": out["version"], "metrics": out["metrics"],
                   "features": FEATURES}, f, ensure_ascii=False, indent=1)
    print("saved nn_model.json")


if __name__ == "__main__":
    main()
