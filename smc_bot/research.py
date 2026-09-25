"""Systematické hľadanie pravidiel s výhodou (edge) bez preučenia.

Dáta sa rozdelia na dve časti:
  * in-sample (IS): na nich sa vyberajú najlepšie kombinácie pravidiel
  * out-of-sample (OOS): novšie dáta, ktoré výber "nevidel" – slúžia iba na overenie
Kombinácia má reálnu výhodu iba vtedy, ak funguje aj na OOS.
"""
from __future__ import annotations

import itertools
import multiprocessing as mp
import os

import numpy as np
import pandas as pd

from . import engine
from .config import apply_overrides

LIQ = {
    "všetka": None,
    "hlavná": ["PWH", "PWL", "PDH", "PDL", "ASIA_H", "ASIA_L", "H1_SH", "H1_SL"],
    "HTF": ["PWH", "PWL", "PDH", "PDL", "ASIA_H", "ASIA_L"],
}
WINDOWS = {
    "00-14": ["00:00-14:00"],
    "LO+NY": ["02:00-05:00", "07:00-11:00"],
    "LO": ["01:00-05:00"],
    "NY": ["07:00-11:00"],
}
GRID = {
    "liq": list(LIQ),
    "okno": list(WINDOWS),
    "tp": ["first_min_rr", "fixed_rr", "fixed_rr_liq"],
    "min_sl": [3, 6],
    "vstup": ["proximal", "ce"],
    "vp": [False, True],
    "h4crt": [False, True],
}

_CTX = None
_BASE = None
_SPLIT = None


def overrides(combo: dict) -> list[str]:
    return [
        f"filters.sweep_kinds={LIQ[combo['liq']]!r}".replace("None", "null").replace("'", '"'),
        f"filters.signal_windows_ny={WINDOWS[combo['okno']]!r}".replace("'", '"'),
        f"target.mode={combo['tp']}",
        f"entry.min_sl_pips={combo['min_sl']}",
        f"entry.price={combo['vstup']}",
        f"filters.require_vp={str(combo['vp']).lower()}",
        f"filters.require_h4_crt={str(combo['h4crt']).lower()}",
    ]


def metrics(r: np.ndarray) -> dict:
    n = len(r)
    if n == 0:
        return {"n": 0, "win%": 0.0, "PF": 0.0, "avgR": 0.0, "R": 0.0, "DD": 0.0}
    wins, losses = r[r > 0].sum(), -r[r <= 0].sum()
    eq = np.cumsum(r)
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]).min())
    return {
        "n": n,
        "win%": round(100 * float((r > 0).mean()), 1),
        "PF": round(float(wins / losses), 2) if losses > 0 else 99.0,
        "avgR": round(float(r.mean()), 3),
        "R": round(float(r.sum()), 1),
        "DD": round(dd, 1),
    }


def _evaluate(combo: dict) -> dict:
    cfg = apply_overrides(_BASE, overrides(combo))
    _CTX.cfg = cfg
    setups = engine.run(_CTX)["setups"]
    rows = [(s["signal_ns"], s["r"]) for s in setups if s.get("status") == "FILLED" and not np.isnan(s.get("r", np.nan))]
    t = np.array([x[0] for x in rows], dtype=np.int64)
    r = np.array([x[1] for x in rows], dtype=float)
    is_m = metrics(r[t < _SPLIT])
    oos_m = metrics(r[t >= _SPLIT])
    return {**combo, **{f"IS_{k}": v for k, v in is_m.items()}, **{f"OOS_{k}": v for k, v in oos_m.items()}}


def run_grid(ctx, cfg: dict, split: str, grid: dict | None = None, workers: int | None = None) -> pd.DataFrame:
    global _CTX, _BASE, _SPLIT
    _CTX, _BASE = ctx, cfg
    _SPLIT = pd.Timestamp(split, tz="UTC").value
    grid = grid or GRID
    combos = [dict(zip(grid, vals)) for vals in itertools.product(*grid.values())]
    workers = workers or os.cpu_count() or 1
    print(f"Testujem {len(combos)} kombinácií na {workers} jadrách, OOS od {split}", flush=True)
    out = []
    with mp.get_context("fork").Pool(workers) as pool:
        for k, row in enumerate(pool.imap_unordered(_evaluate, combos, chunksize=2), 1):
            out.append(row)
            if k % 25 == 0 or k == len(combos):
                print(f"  {k}/{len(combos)}", flush=True)
    return pd.DataFrame(out)


def rank(df: pd.DataFrame, min_is: int = 60) -> pd.DataFrame:
    """Zoradí podľa IS priemerného R (iba kombinácie s dostatkom obchodov)."""
    ok = df[df["IS_n"] >= min_is].copy()
    return ok.sort_values(["IS_avgR", "IS_PF"], ascending=False)


FEATURES = ["direction", "session", "hour_ny", "sweep_kind", "target_kind", "entry_type", "bias_source",
            "w1_aligned", "target_is_bias_dol", "strong_body", "h4_crt", "htf_poi", "vp", "inducement",
            "premium_discount", "sl_bucket", "depth_bucket", "mss_bucket", "liq_rr_bucket"]


def feature_table(trades: pd.DataFrame, split: str) -> pd.DataFrame:
    """Úspešnosť a priemerné R podľa jednotlivých vlastností obchodu, zvlášť IS a OOS."""
    t = trades[trades["status"] == "FILLED"].dropna(subset=["r"]).copy()
    if not len(t):
        return pd.DataFrame()
    t["oos"] = t["signal_time"] >= pd.Timestamp(split, tz="UTC")
    t["sl_bucket"] = pd.cut(t["sl_pips"], [0, 4, 6, 9, 13, 100]).astype(str)
    t["depth_bucket"] = pd.cut(t["sweep_depth_pips"], [-1, 1, 3, 6, 100]).astype(str)
    t["mss_bucket"] = pd.cut(t["mss_bars"], [-1, 3, 8, 16, 100]).astype(str)
    t["liq_rr_bucket"] = pd.cut(t["liq_rr"], [0, 2.5, 3.5, 5, 1000]).astype(str)
    for c in ("htf_poi", "vp"):
        t[c] = t[c].fillna("").map(lambda v: bool(v))
    rows = []
    for f in FEATURES:
        for val, g in t.groupby(f):
            row = {"vlastnosť": f, "hodnota": str(val)}
            for name, part in (("IS", g[~g["oos"]]), ("OOS", g[g["oos"]])):
                m = metrics(part["r"].to_numpy())
                row.update({f"{name}_n": m["n"], f"{name}_win%": m["win%"], f"{name}_avgR": m["avgR"]})
            rows.append(row)
    return pd.DataFrame(rows)
