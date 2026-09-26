"""Obrat na extréme dňa (mean reversion) na zlate – odvodené z rozboru 42 ručných obchodov (druhý trader).

Long, keď: cena za posledné 4 h klesla aspoň `drop4h` USD, je aspoň `vwap_dist` USD pod dennou VWAP
a v spodnej časti denného rozsahu (poloha <= `pos`). Short zrkadlovo. Vstup trhom na ďalšej M1.
Výstup: SL `sl`, TP `tp` (alebo návrat k VWAP pri tp=0), časový limit `max_min`. Jeden obchod naraz. Časy UTC.

python -m smc_bot.gold_reversal --months 2026-09 [--set drop4h=20 ...] [--grid]
"""
from __future__ import annotations

import argparse
import itertools

import numpy as np
import pandas as pd

from .gold_pullback import exit_trade, load, stats

DEFAULT = {"drop4h": 15.0, "vwap_dist": 10.0, "pos": 0.2, "sl": 18.0, "tp": 30.0, "max_min": 480,
           "hours": (0, 24), "cooldown": 30, "partial": 0.0, "trail": 4.0, "be_after_partial": False}


def day_pos(a: dict) -> tuple[np.ndarray, np.ndarray]:
    """Poloha ceny v dennom rozsahu (0 = low dňa, 1 = high dňa), iba z uzavretých sviečok."""
    day = pd.Series(a["day"])
    hi = pd.Series(a["ah"]).groupby(day).cummax().to_numpy()
    lo = pd.Series(a["bl"]).groupby(day).cummin().to_numpy()
    rng = np.maximum(hi - lo, 1e-9)
    return (a["mid"] - lo) / rng, rng


def simulate(a: dict, p: dict) -> list[dict]:
    n = len(a["mid"])
    pos, _ = day_pos(a)
    out, i = [], 241
    h0, h1 = p["hours"]
    while i < n - 1:
        if p.get("allow") is not None and not p["allow"][i]:  # režimový filter (napr. iba trend / iba bočný trh)
            i += 1
            continue
        if not (h0 <= a["hour"][i] < h1):
            i += 1
            continue
        mv = a["mid"][i] - a["mid"][i - 240]
        dv = a["mid"][i] - a["vwap"][i]
        d = 0
        if mv <= -p["drop4h"] and dv <= -p["vwap_dist"] and pos[i] <= p["pos"]:
            d = 1
        elif mv >= p["drop4h"] and dv >= p["vwap_dist"] and pos[i] >= 1 - p["pos"]:
            d = -1
        if d == 0:
            i += 1
            continue
        j = i + 1
        q = dict(p)
        if p["tp"] <= 0:  # TP = návrat k VWAP
            q["tp"] = max(abs(dv), 3.0)
        res, k = exit_trade(a, j, d, q)
        out.append({"t": a["t"][j], "dir": d, "pts": res})
        i = k + 1 + p["cooldown"]
    return out


def run(data: dict, p: dict):
    per, allp = {}, []
    for mo, a in data.items():
        pts = np.array([x["pts"] for x in simulate(a, p) if str(x["t"])[:7] == mo])
        per[mo] = pts
        allp.append(pts)
    return np.concatenate(allp), per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="2026-09")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--set", action="append", default=[])
    a = ap.parse_args()
    months = a.months.split(",")
    data = load(months)
    base = dict(DEFAULT)
    for kv in a.set:
        k, v = kv.split("=")
        base[k] = v.lower() == "true" if isinstance(DEFAULT[k], bool) else type(DEFAULT[k])(eval(v))
    if not a.grid:
        pts, per = run(data, base)
        print("SPOLU", stats(pts, 21 * len(months)))
        for mo, x in per.items():
            print("  ", mo, stats(x, 21))
        return
    rows = []
    for drop, vd, tp, sl in itertools.product((10.0, 15.0, 25.0), (6.0, 10.0, 15.0), (0.0, 20.0, 30.0), (15.0, 20.0)):
        pts, per = run(data, {**base, "drop4h": drop, "vwap_dist": vd, "tp": tp, "sl": sl})
        if not len(pts):
            continue
        w, l = pts[pts > 0].sum(), -pts[pts <= 0].sum()
        rows.append({"drop4h": drop, "vwap": vd, "tp": tp or "VWAP", "sl": sl, "n": len(pts), "win%": round(100 * (pts > 0).mean()),
                     "PF": round(w / l, 2) if l else 99, "USD": round(pts.sum(), 1)})
    print(pd.DataFrame(rows).sort_values("PF", ascending=False).head(15).to_string(index=False))


if __name__ == "__main__":
    main()
