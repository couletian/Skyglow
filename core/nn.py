"""Inference for the small MLP trained on the sunsetbot reanalysis labels.

Pure-Python forward pass (no numpy at runtime); weights live in data/nn_model.json.
"""
import json
import math
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "data", "nn_model.json")
_cache = None


def load():
    global _cache
    if _cache is None:
        try:
            with open(PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def available():
    return bool(load().get("W"))


def predict(feats):
    m = load()
    if not m.get("W"):
        return None
    x = [(v - mu) / (sd or 1.0) for v, mu, sd in zip(feats, m["mean"], m["std"])]
    W, B = m["W"], m["B"]
    h = x
    for i in range(len(W)):
        h = [sum(h[j] * W[i][j][k] for j in range(len(h))) + B[i][k]
             for k in range(len(W[i][0]))]
        if i < len(W) - 1:
            h = [math.tanh(v) for v in h]
    logit = max(-30.0, min(30.0, h[0]))
    return 1.0 / (1.0 + math.exp(-logit))


def metrics():
    try:
        with open(os.path.join(HERE, "data", "nn_metrics.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
