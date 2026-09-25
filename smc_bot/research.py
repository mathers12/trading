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

def _j(x) -> str:
    """Hodnota pre --set (JSON je platný YAML)."""
    import json

    return json.dumps(x, ensure_ascii=False)


HTF_LIQ = ["PWH", "PWL", "PDH", "PDL", "ASIA_H", "ASIA_L"]
# okná v miestnom čase (Bratislava); celé obchodné okno je 08:00–19:00
WINDOWS = {
    "08-19": ["08:00-19:00"],
    "LO 08-11": ["08:00-11:00"],
    "LO+NY": ["08:00-11:00", "13:00-17:00"],
    "SB": ["09:00-10:00", "16:00-17:00"],  # ICT Silver Bullet (03–04 a 10–11 NY)
}

# dimenzia -> {popis: [--set prepínače]}
GRID = {
    "liq": {"všetka": ["filters.sweep_kinds=null"], "HTF": [f"filters.sweep_kinds={_j(HTF_LIQ)}"]},
    "okno": {k: [f"filters.signal_windows_local={_j(v)}"] for k, v in WINDOWS.items()},
    "vstup": {"FVG": ["entry.type=fvg_or_ob", "entry.price=proximal"], "OTE": ["entry.type=ote"]},
    "tp": {"2R": ["target.mode=fixed_rr"], "likv": ["target.mode=first_min_rr"]},
    "MO": {"-": ["filters.require_midnight_open=false"], "áno": ["filters.require_midnight_open=true"]},
    "h4crt": {"-": ["filters.require_h4_crt=false"], "áno": ["filters.require_h4_crt=true"]},
    "slbuf": {"1": ["entry.sl_buffer_pips=1"], "3": ["entry.sl_buffer_pips=3"]},
}
# 2. kolo: okolo prvkov, ktoré pomohli v IS aj OOS (OTE, Londýn/Silver Bullet, H4 CRT, širší SL)
GRID_ICT2 = {
    "liq": GRID["liq"],
    "okno": {"LO 08-11": [f"filters.signal_windows_local={_j(WINDOWS['LO 08-11'])}"],
             "SB": [f"filters.signal_windows_local={_j(WINDOWS['SB'])}"],
             "LO+SB": [f"filters.signal_windows_local={_j(['08:00-11:00', '16:00-17:00'])}"]},
    "ote": {str(x): ["entry.type=ote", f"entry.ote_level={x}"] for x in (0.62, 0.705, 0.79)},
    "tp": {"2R": ["target.mode=fixed_rr"], "likv≤3R": ["target.mode=first_min_rr", "target.max_rr=3"]},
    "h4crt": GRID["h4crt"],
    "slbuf": {"3": ["entry.sl_buffer_pips=3"], "5": ["entry.sl_buffer_pips=5"]},
}
# denný model: viac obchodov (cieľ >= 1 obchod denne na EUR/USD + GBP/USD)
GRID_DAILY = {
    "liq": {"všetka": ["filters.sweep_kinds=null"],
            "HTF+LON": [f"filters.sweep_kinds={_j(HTF_LIQ + ['LON_H', 'LON_L'])}"]},
    "okno": {"08-19": [f"filters.signal_windows_local={_j(WINDOWS['08-19'])}"],
             "LO+NY": [f"filters.signal_windows_local={_j(WINDOWS['LO+NY'])}"]},
    "vstup": {"FVG": ["entry.type=fvg_or_ob", "entry.price=proximal"], "OTE": ["entry.type=ote"]},
    "tp": {"2R": ["target.mode=fixed_rr"], "likv≤3R": ["target.mode=first_min_rr", "target.max_rr=3"]},
    "h4crt": GRID["h4crt"],
    "bias": {"D1+": ["bias.filter=true"], "-": ["bias.filter=false"]},
}
# viac obchodov z najlepšieho setupu: dlhšia platnosť limitky, voľnejšie limity, širšie okná
GRID_FREQ = {
    "okno": {"LO+SB": [f"filters.signal_windows_local={_j(['08:00-11:00', '16:00-17:00'])}"],
             "LO+NYAM": [f"filters.signal_windows_local={_j(['08:00-11:00', '14:30-17:00'])}"],
             "08-19": [f"filters.signal_windows_local={_j(WINDOWS['08-19'])}"]},
    "expir": {"60m": ["entry.expiry_minutes=60"], "180m": ["entry.expiry_minutes=180"]},
    "limity": {"1naraz/2": ["risk.one_position_at_a_time=true", "risk.max_trades_per_day=2"],
               "viac/4": ["risk.one_position_at_a_time=false", "risk.max_trades_per_day=4", "risk.max_losses_per_day=3"]},
    "h4crt": GRID["h4crt"],
    "tp": {"2R": ["target.mode=fixed_rr"], "likv≤3R": ["target.mode=first_min_rr", "target.max_rr=3"]},
}
BEST = ["entry.type=ote", "entry.ote_level=0.705", "entry.sl_buffer_pips=3", "filters.sweep_kinds=null"]
# trhový vstup po MSS (bez čakania na pullback, ktorý vyberal horšie obchody)
GRID_MARKET = {
    "okno": {"08-19": [f"filters.signal_windows_local={_j(WINDOWS['08-19'])}"],
             "LO+NYAM": [f"filters.signal_windows_local={_j(['08:00-11:00', '14:30-17:00'])}"],
             "LO": [f"filters.signal_windows_local={_j(WINDOWS['LO 08-11'])}"]},
    "liq": GRID_DAILY["liq"],
    "h4crt": GRID["h4crt"],
    "bias": GRID_DAILY["bias"],
    "tp": GRID_DAILY["tp"],
    "MO": GRID["MO"],
}
GRIDS = {"ict": GRID, "ict2": GRID_ICT2, "denny": GRID_DAILY, "freq": GRID_FREQ, "market": GRID_MARKET}

_CTX = None
_BASE = None
_SPLIT = None
_GRID = None


def overrides(combo: dict, grid: dict) -> list[str]:
    return [o for dim, label in combo.items() for o in grid[dim][label]]


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
    cfg = apply_overrides(_BASE, overrides(combo, _GRID))
    _CTX.cfg = cfg
    setups = engine.run(_CTX)["setups"]
    rows = [(s["signal_ns"], s["r"]) for s in setups if s.get("status") == "FILLED" and not np.isnan(s.get("r", np.nan))]
    t = np.array([x[0] for x in rows], dtype=np.int64)
    r = np.array([x[1] for x in rows], dtype=float)
    is_m = metrics(r[t < _SPLIT])
    oos_m = metrics(r[t >= _SPLIT])
    return {**combo, **{f"IS_{k}": v for k, v in is_m.items()}, **{f"OOS_{k}": v for k, v in oos_m.items()}}


def run_grid(ctx, cfg: dict, split: str, grid: dict | None = None, workers: int | None = None) -> pd.DataFrame:
    global _CTX, _BASE, _SPLIT, _GRID
    _CTX, _BASE = ctx, cfg
    _SPLIT = pd.Timestamp(split, tz="UTC").value
    grid = _GRID = grid or GRID
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
            "premium_discount", "midnight_open", "judas", "smt", "sl_bucket", "depth_bucket", "mss_bucket", "liq_rr_bucket"]


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
